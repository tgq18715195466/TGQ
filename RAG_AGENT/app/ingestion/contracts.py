"""Persistence-independent ingestion contracts; page numbers are one-based."""

from dataclasses import dataclass, field
from typing import Any


class IngestionError(ValueError):
    def __init__(self, error_code: str, message: str = "", retryable: bool = False):
        self.error_code = error_code
        self.retryable = retryable
        super().__init__(f"{error_code}: {message}")


@dataclass(frozen=True)
class FileLimits:
    max_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    max_image_bytes: int = 20 * 1024 * 1024
    max_image_edge: int = 16000
    max_image_pixels: int = 80_000_000
    max_zip_bytes: int = 250 * 1024 * 1024
    max_zip_entries: int = 10000
    max_zip_ratio: int = 200


@dataclass(frozen=True)
class ValidatedFile:
    sha256: str
    kind: str
    mime_type: str
    page_count: int | None
    filename: str


@dataclass(frozen=True)
class NormalizeConfig:
    libreoffice_binary: str = "soffice"
    timeout_seconds: float = 180
    temp_root: str | None = None
    max_pages: int = 500


@dataclass(frozen=True)
class NormalizedDocument:
    pdf: bytes
    original_sha256: str
    normalized_sha256: str
    page_count: int
    converter_version: str
    duration_ms: int


@dataclass
class ParsedBlock:
    block_id: str
    page_no: int
    block_type: str
    text: str = ""
    markdown: str = ""
    bbox: list[float] = field(default_factory=list)
    reading_order: int = 0
    heading_level: int | None = None
    parent_block_id: str | None = None
    table_html: str | None = None
    formula_latex: str | None = None
    asset_id: str | None = None
    confidence: float | None = None


@dataclass
class ParseResult:
    blocks: list[ParsedBlock]
    raw: dict[str, Any]
    page_count: int
    parser_version: str


@dataclass
class QualityReport:
    status: str
    warnings: list[str]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 650
    max_tokens: int = 1000
    overlap_tokens: int = 100
    minimum_merge_tokens: int = 300
    chunk_config_version: str = "outline-v1"

    def __post_init__(self):
        if not 0 <= self.overlap_tokens < self.target_tokens <= self.max_tokens:
            raise ValueError("Require 0 <= overlap < target <= max")
        if not 0 <= self.minimum_merge_tokens <= self.target_tokens:
            raise ValueError("minimum_merge_tokens must be between 0 and target")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    embedding_text: str
    section_path: list[str]
    page_start: int
    page_end: int
    block_type: str
    block_ids: list[str]
    chunk_sequence: int
    chunk_config_version: str
    token_count: int
    table_id: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    markdown: str | None = None
    table_html: str | None = None
    bbox: list[float] = field(default_factory=list)


@dataclass
class OutlineNode:
    title: str
    level: int
    block_id: str | None
    section_path: list[str]
    children: list["OutlineNode"] = field(default_factory=list)
