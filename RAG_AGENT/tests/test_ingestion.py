import asyncio
import io
import json
import zipfile

import httpx
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.ingestion import (
    ChunkConfig,
    FileLimits,
    IngestionError,
    MinerUClient,
    ParsedBlock,
    ParseResult,
    adapt_mineru_response,
    build_outline,
    create_chunks,
    normalize_file,
    sanitize_html,
    scan_file,
    validate_file,
    validate_parse_quality,
)


def pdf(pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if password:
        writer.encrypt(password)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def block(text, kind="text", page=1, order=0, **kwargs):
    return ParsedBlock(
        block_id=f"b-{page}-{order}", page_no=page, block_type=kind, text=text, reading_order=order, **kwargs
    )


def test_validation_checks_magic_mime_size_pages_and_encryption():
    with pytest.raises(IngestionError, match="FILE_TYPE_UNSUPPORTED"):
        validate_file(b"not a pdf", "fake.pdf", "application/pdf")
    with pytest.raises(IngestionError, match="FILE_TYPE_UNSUPPORTED"):
        validate_file(pdf(), "a.pdf", "image/png")
    with pytest.raises(IngestionError, match="FILE_TOO_LARGE"):
        validate_file(pdf(), "a.pdf", "application/pdf", FileLimits(max_bytes=10))
    with pytest.raises(IngestionError, match="PAGE_LIMIT_EXCEEDED"):
        validate_file(pdf(2), "a.pdf", "application/pdf", FileLimits(max_pages=1))
    with pytest.raises(IngestionError, match="PDF_PASSWORD_REQUIRED"):
        validate_file(pdf(password="secret"), "a.pdf", "application/pdf")
    assert validate_file(pdf(2), "a.pdf", "application/pdf").page_count == 2


def test_image_size_and_office_macro_zipbomb_are_rejected():
    out = io.BytesIO()
    Image.new("RGB", (20, 10)).save(out, format="PNG")
    with pytest.raises(IngestionError, match="IMAGE_LIMIT_EXCEEDED"):
        validate_file(out.getvalue(), "x.png", "image/png", FileLimits(max_image_edge=19))
    for name, data in [("word/vbaProject.bin", b"macro"), ("word/document.xml", b"x" * 100000)]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<doc/>") if "vba" in name else None
            archive.writestr(name, data)
        with pytest.raises(IngestionError):
            validate_file(
                out.getvalue(),
                "x.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )


def test_scanner_default_fails_closed():
    with pytest.raises(IngestionError, match="MALWARE_SCAN_UNAVAILABLE"):
        asyncio.run(scan_file(b"text"))


def test_html_strips_scripts_remote_resources_and_normalizes():
    source = '<h1 onclick="evil()">标题</h1><script>steal()</script><iframe src="https://evil"/> <img src="http://evil">正文'
    cleaned = sanitize_html(source)
    assert all(word not in cleaned for word in ["steal", "iframe", "http://", "onclick", "script"])
    data = source.encode()
    validated = validate_file(data, "doc.html", "text/html")
    normalized = normalize_file(data, validated)
    assert normalized.pdf.startswith(b"%PDF")
    assert "正文" in PdfReader(io.BytesIO(normalized.pdf)).pages[0].extract_text()
    assert normalized.original_sha256 == validated.sha256


def test_mineru_adapter_keeps_raw_and_rejects_visual_invention():
    raw = {
        "version": "2.5.4",
        "results": {
            "document": {
                "content_list": json.dumps(
                    [
                        {
                            "type": "text",
                            "text": "标题",
                            "text_level": 1,
                            "page_idx": 0,
                            "bbox": [0, 0, 10, 10],
                        },
                        {
                            "type": "image",
                            "image_caption": ["已知图注"],
                            "image_description": "invented",
                            "page_idx": 0,
                        },
                    ]
                ),
                "middle_json": {"pdf_info": [{"page_idx": 0}]},
            }
        },
    }

    def handle(request):
        assert request.url.path == "/file_parse"
        assert b"image_analysis" in request.content and b"false" in request.content
        return httpx.Response(200, json=raw)

    client = MinerUClient("http://mineru", transport=httpx.MockTransport(handle))
    parsed = asyncio.run(client.parse(pdf(), "v1"))
    assert parsed.raw == raw and parsed.page_count == 1
    assert parsed.blocks[0].block_type == "title" and parsed.blocks[0].page_no == 1
    assert parsed.blocks[1].text == "已知图注" and "invented" not in parsed.blocks[1].text


def test_quality_blocks_blank_garbled_and_low_confidence():
    assert validate_parse_quality(ParseResult([block("text")], {}, 5, "test"), 5).status == "BLOCK"
    assert validate_parse_quality(ParseResult([block("����garbled")], {}, 1, "test"), 1).status == "BLOCK"
    assert (
        validate_parse_quality(ParseResult([block("text", confidence=0.7)], {}, 1, "test"), 1).status
        == "BLOCK"
    )
    assert validate_parse_quality(ParseResult([block("normal text")], {}, 1, "test"), 1).status == "PASS"


def test_chunk_outline_title_injection_stability_and_no_empty_image():
    blocks = [
        block("一章", "title", heading_level=1),
        block("正文" * 100, order=1),
        block("二章", "title", order=2, heading_level=1),
        block("", "image", order=3),
        block("二章正文", order=4),
    ]
    config = ChunkConfig(target_tokens=80, max_tokens=100, overlap_tokens=10, minimum_merge_tokens=20)
    chunks = create_chunks(blocks, "d", "v", "文档", config)
    assert [c.chunk_id for c in chunks] == [
        c.chunk_id for c in create_chunks(blocks, "d", "v", "文档", config)
    ]
    assert all(c.embedding_text.startswith("文档\n") for c in chunks)
    assert all(not ("一章" in c.section_path and "二章" in c.section_path) for c in chunks)
    assert create_chunks([block("", "image")], "d", "v", "document") == []


def test_cross_page_tables_repeat_headers_without_splitting_cells():
    rows1 = "<table><tr><th>名称</th><th>数值</th></tr><tr><td>甲</td><td>100</td></tr></table>"
    rows2 = "<table><tr><th>名称</th><th>数值</th></tr><tr><td>乙</td><td>200</td></tr></table>"
    chunks = create_chunks(
        [
            block("", "table", table_html=rows1, bbox=[0, 700, 900, 990]),
            block("", "table", page=2, table_html=rows2, bbox=[0, 0, 900, 300]),
        ],
        "d",
        "v",
        "doc",
        ChunkConfig(target_tokens=20, max_tokens=70, overlap_tokens=0, minimum_merge_tokens=1),
    )
    assert all("名称" in c.text and "数值" in c.text for c in chunks)
    assert len({c.table_id for c in chunks}) == 1
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 2
    assert "甲" in "".join(c.text for c in chunks) and "乙" in "".join(c.text for c in chunks)


def test_office_relationship_attributes_cannot_bypass_external_resource_check():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships><Relationship TargetMode = "External" Target="https://evil"/></Relationships>',
        )
    with pytest.raises(IngestionError, match="FILE_CORRUPT"):
        validate_file(
            out.getvalue(),
            "x.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def test_table_merged_cell_structure_survives_sanitization_and_warns():
    markup = sanitize_html(
        '<table><tr><th colspan="2" onclick="evil()">标题</th></tr><tr><td>甲</td><td>乙</td></tr></table>'
    )
    assert 'colspan="2"' in markup and "onclick" not in markup
    parsed = ParseResult([block("表格", "table", table_html=markup)], {}, 1, "test")
    assert validate_parse_quality(parsed, 1).status == "WARN"


def test_mineru_missing_page_metadata_blocks_and_timeout_is_retryable():
    with pytest.raises(IngestionError, match="MINERU_PARSE_FAILED"):
        adapt_mineru_response({"results": {"doc": {"content_list": []}}}, "v1")

    def handler(request):
        return httpx.Response(429)

    with pytest.raises(IngestionError) as raised:
        asyncio.run(MinerUClient("http://mineru", transport=httpx.MockTransport(handler)).parse(pdf(), "v1"))
    assert raised.value.retryable


def test_quality_warn_boundary_and_explained_page_difference():
    parsed = ParseResult([block("normal text", page=i, confidence=0.8) for i in range(1, 10)], {}, 10, "test")
    report = validate_parse_quality(parsed, 10)
    assert report.status == "WARN" and report.metrics["blank_page_ratio"] == 0.1
    assert validate_parse_quality(parsed, 11).status == "BLOCK"
    assert validate_parse_quality(parsed, 11, page_difference_explained=True).status == "WARN"


def test_image_normalizes_to_pdf_and_stable_chunks_include_config_version():
    out = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(out, format="PNG")
    value = out.getvalue()
    normalized = normalize_file(value, validate_file(value, "x.png", "image/png"))
    assert normalized.page_count == 1 and len(PdfReader(io.BytesIO(normalized.pdf)).pages) == 1
    blocks = [block("第一章", "title", heading_level=1), block("正文", order=1)]
    outline = build_outline(blocks)
    assert outline.children[0].section_path == ["第一章"]
    first = create_chunks(blocks, "d", "v", "doc")
    second = create_chunks(blocks, "d", "v", "doc", ChunkConfig(chunk_config_version="v2"))
    assert first[0].chunk_id != second[0].chunk_id


def test_table_oversized_cell_blocks_instead_of_truncating():
    markup = "<table><tr><th>A</th></tr><tr><td>" + "长" * 1000 + "</td></tr></table>"
    with pytest.raises(IngestionError, match="CHUNK_TOO_LARGE"):
        create_chunks([block("", "table", table_html=markup)], "d", "v", "doc")


def test_chunks_keep_precise_page_provenance():
    blocks = [block("a" * 75, page=1), block("b" * 75, page=2)]
    chunks = create_chunks(
        blocks,
        "d",
        "v",
        "doc",
        ChunkConfig(target_tokens=80, max_tokens=100, overlap_tokens=0, minimum_merge_tokens=0),
    )
    assert chunks[0].page_start == chunks[0].page_end == 1
    assert chunks[-1].page_start == chunks[-1].page_end == 2


def test_table_rowspan_is_not_split_across_chunk_boundaries():
    markup = '<table><tr><th>Name</th><th>Value</th></tr><tr><td rowspan="2">Grouped</td><td>Alpha</td></tr><tr><td>Beta</td></tr></table>'
    chunks = create_chunks(
        [block("", "table", table_html=markup)],
        "d",
        "v",
        "doc",
        ChunkConfig(target_tokens=40, max_tokens=200, overlap_tokens=0, minimum_merge_tokens=0),
    )
    assert len(chunks) == 1
    assert "Alpha" in chunks[0].text and "Beta" in chunks[0].text
