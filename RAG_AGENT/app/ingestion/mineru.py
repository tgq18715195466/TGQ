"""MinerU /file_parse compatibility adapter. Raw output never drives chunking."""

import hashlib
import json
import math
from uuid import NAMESPACE_URL, uuid5

import httpx
from bs4 import BeautifulSoup

from .contracts import IngestionError, ParsedBlock, ParseResult, QualityReport
from .files import sanitize_html


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def _strings(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(x for x in value if isinstance(x, str))
    return ""


def adapt_mineru_response(raw: dict, version_id: str) -> ParseResult:
    try:
        results = raw["results"]
        if not isinstance(results, dict) or len(results) != 1:
            raise ValueError("Exactly one uploaded document is required")
        document = next(iter(results.values()))
        entries = _decode(document["content_list"])
        if not isinstance(entries, list):
            raise TypeError("content_list must be an array")
        blocks = []
        for order, entry in enumerate(entries):
            kind = entry.get("type", "text")
            level = entry.get("text_level") or None
            if kind == "text" and level:
                kind = "title"
            if kind in {"equation", "interline_equation"}:
                kind = "formula"
            if kind in {"discarded", "header", "footer", "page_number"}:
                continue
            original_kind = kind
            if kind in {"aside_text", "page_footnote", "code"}:
                kind = "text"
            if kind == "chart":
                kind = "image"
            if kind not in {"text", "title", "list", "table", "formula", "image", "caption"}:
                raise ValueError("Unsupported MinerU block type")
            page = int(entry["page_idx"]) + 1
            if page < 1:
                raise ValueError("Invalid page")
            bbox = [float(x) for x in entry.get("bbox", [])]
            if bbox and (len(bbox) != 4 or not all(math.isfinite(x) for x in bbox)):
                raise ValueError("Invalid bbox")
            text = _strings(entry.get("text", ""))
            if original_kind == "code":
                text = text or _strings(entry.get("code_body", ""))
            html, latex = None, None
            if kind == "image":
                # image_description/chart_analysis/etc. are intentionally excluded.
                text = "\n".join(
                    filter(
                        None,
                        [
                            _strings(entry.get("ocr_text")),
                            _strings(entry.get("image_caption")),
                            _strings(entry.get("image_footnote")),
                        ],
                    )
                )
            elif kind == "table":
                html = sanitize_html(_strings(entry.get("table_body", "")))
                text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            elif kind == "list":
                text = text or _strings(entry.get("list_items"))
            elif kind == "formula":
                latex = text
            confidence = entry.get("confidence")
            if confidence is not None:
                confidence = float(confidence)
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("Invalid confidence")
            block_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{version_id}:mineru:{page}:{order}:{hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()}",
                )
            )
            asset = entry.get("img_path")
            asset_id = str(uuid5(NAMESPACE_URL, f"{version_id}:asset:{asset}")) if asset else None
            blocks.append(
                ParsedBlock(
                    block_id,
                    page,
                    kind,
                    text,
                    text,
                    bbox,
                    order,
                    int(level) if level else None,
                    table_html=html,
                    formula_latex=latex,
                    asset_id=asset_id,
                    confidence=confidence,
                )
            )
        middle = _decode(document.get("middle_json", {}))
        page_info = middle.get("pdf_info", []) if isinstance(middle, dict) else []
        if not isinstance(page_info, list):
            raise TypeError("pdf_info must be an array")
        # Require parser-reported page metadata so trailing blank pages cannot vanish silently.
        if not page_info:
            raise ValueError("middle_json.pdf_info is required for page reconciliation")
        pages = len(page_info)
        if any(b.page_no > pages for b in blocks):
            raise ValueError("Block exceeds parser page count")
        return ParseResult(blocks, raw, pages, str(raw.get("version", "unknown")))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise IngestionError("MINERU_PARSE_FAILED", "Unsupported or malformed MinerU response") from exc


class MinerUClient:
    def __init__(self, base_url: str, timeout: float = 1200, transport=None):
        self.base_url, self.timeout, self.transport = base_url.rstrip("/"), timeout, transport

    async def parse(self, pdf: bytes, version_id: str) -> ParseResult:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport, follow_redirects=False
            ) as client:
                response = await client.post(
                    self.base_url + "/file_parse",
                    files={"files": ("document.pdf", pdf, "application/pdf")},
                    data={
                        "backend": "pipeline",
                        "parse_method": "auto",
                        "lang_list": "ch",
                        "formula_enable": "true",
                        "table_enable": "true",
                        "image_analysis": "false",
                        "return_md": "true",
                        "return_content_list": "true",
                        "return_middle_json": "true",
                        "return_images": "true",
                        "response_format_zip": "false",
                    },
                )
                response.raise_for_status()
                return adapt_mineru_response(response.json(), version_id)
        except httpx.HTTPStatusError as exc:
            raise IngestionError(
                "MINERU_PARSE_FAILED",
                "MinerU HTTP failure",
                retryable=exc.response.status_code in {408, 429} or exc.response.status_code >= 500,
            ) from exc
        except httpx.HTTPError as exc:
            raise IngestionError("MINERU_PARSE_FAILED", "MinerU unavailable", retryable=True) from exc
        except (ValueError, TypeError) as exc:
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError("MINERU_PARSE_FAILED", "MinerU response is invalid") from exc


def validate_parse_quality(
    parsed: ParseResult, expected_pages: int, *, page_difference_explained: bool = False
) -> QualityReport:
    issues, severities = [], []

    def issue(message, severity):
        issues.append(message)
        severities.append(severity)

    populated = {
        b.page_no for b in parsed.blocks if (b.text or b.table_html or b.formula_latex or "").strip()
    }
    blank = max(0, expected_pages - len(populated)) / max(1, expected_pages)
    text = "".join(b.text for b in parsed.blocks)
    garbled = sum(
        c == "\ufffd" or 0xE000 <= ord(c) <= 0xF8FF or (ord(c) < 32 and c not in "\r\n\t") for c in text
    ) / max(1, len(text))
    confidence_values = [b.confidence for b in parsed.blocks if b.confidence is not None]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
    if blank > 0.05:
        issue("blank_page_ratio", "BLOCK" if blank > 0.20 else "WARN")
    if garbled > 0.01:
        issue("garbled_character_ratio", "BLOCK" if garbled > 0.05 else "WARN")
    if confidence is not None and confidence < 0.90:
        issue("ocr_confidence", "BLOCK" if confidence < 0.75 else "WARN")
    if parsed.page_count != expected_pages:
        issue(
            "page_count_mismatch",
            "WARN" if abs(parsed.page_count - expected_pages) == 1 and page_difference_explained else "BLOCK",
        )
    previous_level = 0
    for block in parsed.blocks:
        if block.block_type == "title":
            level = block.heading_level
            if not level or level < 1 or level > 6:
                issue("heading_tree_invalid", "BLOCK")
            else:
                if level > previous_level + 1:
                    issue("heading_level_jump", "WARN")
                previous_level = level
        if block.block_type == "table":
            soup = BeautifulSoup(block.table_html or "", "html.parser")
            rows = soup.find_all("tr")
            if not rows or not all(row.find_all(["th", "td"]) for row in rows):
                issue("table_not_serializable", "BLOCK")
            elif soup.find(attrs={"rowspan": True}) or soup.find(attrs={"colspan": True}):
                issue("table_merged_cells", "WARN")
    status = "BLOCK" if "BLOCK" in severities else "WARN" if severities else "PASS"
    return QualityReport(
        status,
        sorted(set(issues)),
        {
            "blank_page_ratio": blank,
            "garbled_character_ratio": garbled,
            "ocr_mean_confidence": confidence,
            "expected_pages": expected_pages,
            "parsed_pages": parsed.page_count,
        },
    )
