"""Deterministic structural chunks with a conservative offline token budget.

UTF-8 bytes bound byte-level BPE tokens from above. This deliberately underfills
some chunks rather than silently exceeding provider limits or downloading a
tokenizer at runtime. Persist this algorithm in the chunk config version.
"""

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from uuid import NAMESPACE_URL, uuid5

from bs4 import BeautifulSoup

from .contracts import Chunk, ChunkConfig, IngestionError, OutlineNode, ParsedBlock


def count_tokens(text: str) -> int:
    return len(text.encode("utf-8"))


def build_outline(blocks: list[ParsedBlock]) -> OutlineNode:
    root = OutlineNode("", 0, None, [])
    stack = [root]
    for block in sorted(blocks, key=lambda b: (b.page_no, b.reading_order)):
        if block.block_type != "title":
            continue
        level = block.heading_level or 1
        if level < 1 or level > 6:
            raise IngestionError("PARSE_QUALITY_BLOCKED", "Invalid heading level")
        while stack[-1].level >= level:
            stack.pop()
        node = OutlineNode(
            block.text.strip(), level, block.block_id, stack[-1].section_path + [block.text.strip()]
        )
        stack[-1].children.append(node)
        stack.append(node)
    return root


def _split_text(text: str, budget: int, overlap: int, *, offsets=False) -> list:
    text = text.strip()
    parts = []
    start = 0
    while start < len(text):
        end, size = start, 0
        while end < len(text) and size + count_tokens(text[end]) <= budget:
            size += count_tokens(text[end])
            end += 1
        if end == start:
            raise IngestionError("CHUNK_TOO_LARGE", "Title leaves no room for content")
        if end < len(text):
            # Prefer paragraph, then sentence boundaries in the latter half.
            split = max(text.rfind("\n\n", start, end), text.rfind("\n", start, end))
            if split < start + (end - start) // 2:
                candidates = [m.end() + start for m in re.finditer(r"[。！？.!?](?:\s|$)", text[start:end])]
                split = candidates[-1] if candidates else -1
            if split >= start + max(1, (end - start) // 2):
                end = split
        fragment = text[start:end].strip()
        if fragment:
            left = start + len(text[start:end]) - len(text[start:end].lstrip())
            right = end - len(text[start:end]) + len(text[start:end].rstrip())
            parts.append((fragment, left, right) if offsets else fragment)
        if end >= len(text):
            break
        next_start, size = end, 0
        while next_start > start + 1 and size + count_tokens(text[next_start - 1]) <= overlap:
            next_start -= 1
            size += count_tokens(text[next_start])
        start = max(start + 1, next_start)
    return parts


def _table_rows(block: ParsedBlock):
    soup = BeautifulSoup(block.table_html or "", "html.parser")
    rows = soup.find_all("tr")
    if not rows:
        raise IngestionError("PARSE_QUALITY_BLOCKED", "Table has no rows")
    parsed = [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
        for row in rows
    ]
    if not all(parsed):
        raise IngestionError("PARSE_QUALITY_BLOCKED", "Table has an empty row")
    header_count = 0
    for row in rows:
        if row.find("th"):
            header_count += 1
        else:
            break
    header_count = max(1, header_count)
    return (
        parsed[:header_count],
        [(row, block.page_no, str(raw)) for row, raw in zip(parsed[header_count:], rows[header_count:])],
        [str(x) for x in rows[:header_count]],
    )


def _markdown(headers, rows):
    def line(cells):
        return "| " + " | ".join(x.replace("|", "\\|").replace("\n", " ") for x in cells) + " |"

    return "\n".join(
        [*(line(x) for x in headers), line(["---"] * len(headers[-1])), *(line(x[0]) for x in rows)]
    )


def create_chunks(
    blocks: list[ParsedBlock],
    document_id: str,
    version_id: str,
    document_name: str,
    config: ChunkConfig | None = None,
) -> list[Chunk]:
    config = config or ChunkConfig()
    ordered = sorted(blocks, key=lambda b: (b.page_no, b.reading_order))
    root = build_outline(ordered)
    paths = {}

    def visit(node):
        if node.block_id:
            paths[node.block_id] = node.section_path
        for child in node.children:
            visit(child)

    visit(root)
    current, sections = [], []
    for block in ordered:
        if block.block_type == "title":
            current = paths[block.block_id]
        sections.append(list(current))
    output = []

    def prefix(path):
        return "\n".join([document_name, *path]) + "\n"

    def emit(text, path, sources, kind="text", **extra):
        text = unicodedata.normalize("NFC", text.strip())
        if not text:
            return
        embedded = prefix(path) + text
        if count_tokens(embedded) > config.max_tokens:
            raise IngestionError("CHUNK_TOO_LARGE", "An indivisible row or title exceeds chunk budget")
        sequence = len(output)
        digest = hashlib.sha256(text.encode()).hexdigest()
        identity = json.dumps(
            [str(document_id), str(version_id), path, digest, sequence, config.chunk_config_version],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        output.append(
            Chunk(
                str(uuid5(NAMESPACE_URL, identity)),
                text,
                embedded,
                list(path),
                min(b.page_no for b in sources),
                max(b.page_no for b in sources),
                kind,
                list(dict.fromkeys(b.block_id for b in sources)),
                sequence,
                config.chunk_config_version,
                count_tokens(embedded),
                **extra,
            )
        )

    i = 0
    while i < len(ordered):
        block, path = ordered[i], sections[i]
        if block.block_type == "title":
            i += 1
            continue
        budget = min(config.target_tokens, config.max_tokens - count_tokens(prefix(path)))
        if budget < 4:
            raise IngestionError("CHUNK_TOO_LARGE", "Heading path exceeds configured chunk size")
        if block.block_type == "table":
            headers, rows, header_html = _table_rows(block)
            # Headers spanning into body rows cannot be repeated over unrelated later rows.
            crossing_header = any(index + int(cell.get("rowspan", 1)) > len(header_html)
                for index, html in enumerate(header_html)
                for cell in BeautifulSoup(html, "html.parser").find_all(["th", "td"]))
            if crossing_header:
                body = _markdown(headers, rows)
                emit(body, path, [block], "table",
                     table_id=str(uuid5(NAMESPACE_URL, f"{version_id}:table:{block.block_id}")),
                     row_start=1, row_end=len(rows), markdown=body, table_html=block.table_html)
                i += 1
                continue
            sources, j = [block], i + 1
            while j < len(ordered) and ordered[j].block_type == "table" and sections[j] == path:
                candidate = ordered[j]
                h2, r2, _ = _table_rows(candidate)
                previous = sources[-1]
                matching_header = (
                    len(headers[-1]) == len(h2[-1])
                    and SequenceMatcher(
                        None, json.dumps(headers, ensure_ascii=False), json.dumps(h2, ensure_ascii=False)
                    ).ratio()
                    >= 0.85
                )
                positioned = (
                    len(previous.bbox) == 4
                    and len(candidate.bbox) == 4
                    and previous.bbox[3] >= 600
                    and candidate.bbox[1] <= 300
                )
                if candidate.page_no != previous.page_no + 1 or not matching_header or not positioned:
                    break
                rows.extend(r2)
                sources.append(candidate)
                j += 1
            table_id = str(uuid5(NAMESPACE_URL, f"{version_id}:table:{block.block_id}"))
            # Rows linked by rowspan are indivisible, including overlapping spans.
            groups, group, remaining = [], [], 0
            for row in rows:
                spans = [
                    int(cell.get("rowspan", 1))
                    for cell in BeautifulSoup(row[2], "html.parser").find_all(["th", "td"])
                ]
                remaining = max(remaining, max(spans, default=1))
                group.append(row)
                remaining -= 1
                if remaining == 0:
                    groups.append(group)
                    group = []
            if group:
                raise IngestionError("PARSE_QUALITY_BLOCKED", "Table rowspan exceeds available rows")
            batches, batch, row_start = [], [], 1
            for group in groups:
                if batch and count_tokens(_markdown(headers, batch + group)) > budget:
                    batches.append((batch, row_start))
                    row_start += len(batch)
                    batch = []
                batch.extend(group)
            if batch or not rows:
                batches.append((batch, row_start))
            for batch, first in batches:
                body = _markdown(headers, batch)
                relevant_pages = {x[1] for x in batch} or {block.page_no}
                relevant = [b for b in sources if b.page_no in relevant_pages]
                emit(
                    body,
                    path,
                    relevant,
                    "table",
                    table_id=table_id,
                    row_start=first,
                    row_end=first + len(batch) - 1,
                    markdown=body,
                    table_html="<table>" + "".join(header_html) + "".join(x[2] for x in batch) + "</table>",
                )
            i = j
        elif block.block_type == "formula":
            emit(block.formula_latex or block.text, path, [block], "formula")
            i += 1
        elif block.block_type == "image":
            texts, sources = [], [block]
            if block.text.strip():
                texts.append(block.text.strip())
            for neighbor in [i - 1, i + 1]:
                if 0 <= neighbor < len(ordered) and sections[neighbor] == path:
                    candidate = ordered[neighbor]
                    if (
                        candidate.block_type in {"text", "caption"}
                        and candidate.page_no == block.page_no
                        and candidate.text.strip()
                    ):
                        texts.append(candidate.text.strip())
                        sources.append(candidate)
            for part in _split_text("\n".join(texts), budget, min(config.overlap_tokens, budget // 4)):
                emit(part, path, sources, "image", bbox=block.bbox)
            i += 1
        else:
            sources, texts, j = [block], [block.text or block.formula_latex or ""], i + 1
            # Group paragraphs only within one outline leaf; never cross a first-level title.
            while (
                j < len(ordered)
                and sections[j] == path
                and ordered[j].block_type in {"text", "list", "caption"}
            ):
                sources.append(ordered[j])
                texts.append(ordered[j].text or ordered[j].formula_latex or "")
                j += 1
            spans, cursor, nonempty = [], 0, []
            for source, value in zip(sources, texts):
                value = value.strip()
                if value:
                    spans.append((cursor, cursor + len(value), source))
                    nonempty.append(value)
                    cursor += len(value) + 2
            combined = "\n\n".join(nonempty)
            parts = _split_text(combined, budget, min(config.overlap_tokens, budget // 3), offsets=True)
            if len(parts) > 1 and count_tokens(parts[-1][0]) < config.minimum_merge_tokens:
                start, end = parts[-2][1], parts[-1][2]
                merged = combined[start:end]
                if count_tokens(prefix(path) + merged) <= config.max_tokens:
                    parts[-2:] = [(merged, start, end)]
            for part, start, end in parts:
                relevant = [source for left, right, source in spans if left < end and right > start]
                emit(part, path, relevant)
            i = j
    return output
