"""Validate before parsing. Converters run only in a separate locked-down worker."""

import hashlib
import io
import os
import subprocess
import tempfile
import time
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import Protocol
from xml.etree import ElementTree

import bleach
import httpx
from bs4 import BeautifulSoup
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from .contracts import FileLimits, IngestionError, NormalizeConfig, NormalizedDocument, ValidatedFile

MIMES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
}
OFFICE_PARTS = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}
IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF", "bmp": "BMP"}


def _pdf_pages(data: bytes, max_pages: int) -> int:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise IngestionError("PDF_PASSWORD_REQUIRED", "Encrypted PDFs are not accepted")
        count = len(reader.pages)
        if count < 1:
            raise ValueError("Empty PDF")
        if count > max_pages:
            raise IngestionError("PAGE_LIMIT_EXCEEDED", "Document exceeds page limit")
        return count
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError("FILE_CORRUPT", "PDF structure is invalid") from exc


def validate_file(
    data: bytes, filename: str, mime_type: str, limits: FileLimits | None = None
) -> ValidatedFile:
    limits = limits or FileLimits()
    kind = Path(filename).suffix.lower().lstrip(".")
    mime = mime_type.partition(";")[0].strip().lower()
    if kind not in MIMES or mime != MIMES[kind]:
        raise IngestionError("FILE_TYPE_UNSUPPORTED", "Extension and MIME must agree")
    if not data:
        raise IngestionError("FILE_CORRUPT", "File is empty")
    if len(data) > limits.max_bytes:
        raise IngestionError("FILE_TOO_LARGE", "File exceeds size limit")
    pages = None
    if kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise IngestionError("FILE_TYPE_UNSUPPORTED", "PDF magic mismatch")
        pages = _pdf_pages(data, limits.max_pages)
    elif kind in IMAGE_FORMATS:
        if len(data) > limits.max_image_bytes:
            raise IngestionError("FILE_TOO_LARGE", "Image exceeds size limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.format != IMAGE_FORMATS[kind]:
                        raise IngestionError("FILE_TYPE_UNSUPPORTED", "Image magic mismatch")
                    pages = getattr(picture, "n_frames", 1)
                    if pages > limits.max_pages:
                        raise IngestionError("PAGE_LIMIT_EXCEEDED")
                    for frame in range(pages):
                        picture.seek(frame)
                        if (
                            max(picture.size) > limits.max_image_edge
                            or picture.width * picture.height > limits.max_image_pixels
                        ):
                            raise IngestionError("IMAGE_LIMIT_EXCEEDED", "Image dimensions exceed limit")
                        picture.load()
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError("FILE_CORRUPT", "Image cannot be decoded") from exc
    elif kind in OFFICE_PARTS:
        if not data.startswith(b"PK\x03\x04"):
            raise IngestionError("FILE_TYPE_UNSUPPORTED", "Office ZIP magic mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                names = {x.filename for x in infos}
                if len(infos) > limits.max_zip_entries or len(names) != len(infos):
                    raise ValueError("Too many or duplicate entries")
                if "[Content_Types].xml" not in names or OFFICE_PARTS[kind] not in names:
                    raise ValueError("Incorrect Office type")
                if sum(x.file_size for x in infos) > limits.max_zip_bytes:
                    raise ValueError("Expanded ZIP exceeds limit")
                for item in infos:
                    path = PurePosixPath(item.filename.replace("\\", "/"))
                    name = item.filename.lower()
                    if path.is_absolute() or ".." in path.parts or ":" in name:
                        raise ValueError("Unsafe ZIP path")
                    if (
                        item.flag_bits & 1
                        or "vbaproject" in name
                        or "activex" in name
                        or "embeddings/" in name
                    ):
                        raise ValueError("Encrypted or executable Office content")
                    if item.file_size > max(1, item.compress_size) * limits.max_zip_ratio:
                        raise ValueError("Unsafe ZIP compression ratio")
                    content = archive.read(item)
                    if name.endswith((".xml", ".rels")):
                        xml = content.decode("utf-8-sig")
                        if any(x in xml.lower() for x in ["<!doctype", "<!entity", "macroenabled"]):
                            raise ValueError("Active XML is not allowed")
                        tree = ElementTree.fromstring(xml)
                        for element in tree.iter():
                            if any(
                                key.rsplit("}", 1)[-1].lower() == "targetmode" and value.lower() == "external"
                                for key, value in element.attrib.items()
                            ):
                                raise ValueError("External relationships are not allowed")
        except Exception as exc:
            raise IngestionError("FILE_CORRUPT", "Unsafe or invalid Office archive") from exc
    else:
        try:
            text = data.decode("utf-8-sig")
            if any(ord(char) < 32 and char not in "\r\n\t\f" for char in text):
                raise ValueError("Binary controls")
            if data.startswith((b"%PDF", b"PK\x03\x04", b"MZ", b"\x7fELF")):
                raise ValueError("Binary magic")
        except (ValueError, UnicodeError) as exc:
            raise IngestionError("FILE_TYPE_UNSUPPORTED", "Text input must be UTF-8 text") from exc
    return ValidatedFile(hashlib.sha256(data).hexdigest(), kind, mime, pages, Path(filename).name)


class MalwareScanner(Protocol):
    async def scan(self, data: bytes) -> None: ...


class HTTPMalwareScanner:
    """Configured scanner must return {\"status\": \"clean\"}; anything else blocks."""

    def __init__(self, url: str, timeout: float = 30, token: str | None = None):
        self.url, self.timeout, self.token = url, timeout, token

    async def scan(self, data: bytes) -> None:
        try:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    self.url, content=data, headers={**headers, "Content-Type": "application/octet-stream"}
                )
                response.raise_for_status()
                status = response.json().get("status")
        except (httpx.HTTPError, ValueError) as exc:
            raise IngestionError("MALWARE_SCAN_UNAVAILABLE", "Scanner unavailable", retryable=True) from exc
        if status != "clean":
            raise IngestionError("MALWARE_SCAN_REJECTED", "Scanner did not attest clean content")


class DevelopmentBypassScanner:
    """Only construct after verifying an explicit development-only setting."""

    async def scan(self, data: bytes) -> None:
        return None


async def scan_file(data: bytes, scanner: MalwareScanner | None = None) -> None:
    if scanner is None:
        raise IngestionError("MALWARE_SCAN_UNAVAILABLE", "A malware scanner must be configured")
    await scanner.scan(data)


def sanitize_html(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.find_all(
        [
            "script",
            "style",
            "iframe",
            "object",
            "embed",
            "svg",
            "math",
            "link",
            "meta",
            "base",
            "img",
            "video",
            "audio",
            "source",
        ]
    ):
        tag.decompose()

    def safe_attribute(tag, name, value):
        return (
            tag in {"th", "td"}
            and name in {"colspan", "rowspan"}
            and value.isdigit()
            and 1 <= int(value) <= 1000
        )

    return bleach.clean(
        str(soup),
        tags=[
            "p",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "li",
            "table",
            "tr",
            "th",
            "td",
            "strong",
            "em",
            "pre",
            "code",
        ],
        attributes=safe_attribute,
        protocols=[],
        strip=True,
    )


def _text_pdf(value: str) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(595, 842), invariant=1)
    document.setFont("STSong-Light", 11)
    y = 790
    for paragraph in value.splitlines() or [""]:
        # simpleSplit splits on whitespace only; insert wrapping by glyph width for CJK.
        line = ""
        lines = []
        for char in paragraph:
            if pdfmetrics.stringWidth(line + char, "STSong-Light", 11) > 499:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
        for line in lines:
            if y < 50:
                document.showPage()
                document.setFont("STSong-Light", 11)
                y = 790
            document.drawString(48, y, line)
            y -= 16
    document.save()
    return output.getvalue()


def normalize_file(
    data: bytes, validated: ValidatedFile, config: NormalizeConfig | None = None
) -> NormalizedDocument:
    config = config or NormalizeConfig()
    started = time.monotonic()
    if hashlib.sha256(data).hexdigest() != validated.sha256:
        raise IngestionError("NORMALIZE_FAILED", "Content changed after validation")
    try:
        if validated.kind == "pdf":
            # Copy only page contents; drop document actions, metadata and attachments.
            reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
            for page in reader.pages:
                for key in ("/AA", "/Annots"):
                    if key in page:
                        del page[key]
                writer.add_page(page)
            output = io.BytesIO()
            writer.write(output)
            result, converter = output.getvalue(), "pypdf-page-copy-v1"
        elif validated.kind in IMAGE_FORMATS:
            from reportlab.lib.utils import ImageReader

            output = io.BytesIO()
            document = canvas.Canvas(output, invariant=1)
            with Image.open(io.BytesIO(data)) as picture:
                for i in range(getattr(picture, "n_frames", 1)):
                    picture.seek(i)
                    frame = ImageOps.exif_transpose(picture).convert("RGB")
                    dpi = picture.info.get("dpi", (150, 150))
                    xdpi, ydpi = [max(36, min(1200, float(x or 150))) for x in dpi[:2]]
                    width, height = frame.width * 72 / xdpi, frame.height * 72 / ydpi
                    document.setPageSize((width, height))
                    document.drawImage(ImageReader(frame), 0, 0, width, height)
                    document.showPage()
            document.save()
            result, converter = output.getvalue(), "pillow-reportlab-v1"
        elif validated.kind in OFFICE_PARTS:
            with tempfile.TemporaryDirectory(prefix="ingestion-", dir=config.temp_root) as folder:
                root = Path(folder)
                source = root / f"document.{validated.kind}"
                source.write_bytes(data)
                profile = root / "profile"
                user_config = profile / "user"
                user_config.mkdir(parents=True)
                (user_config / "registrymodifications.xcu").write_text(
                    '<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>',
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        config.libreoffice_binary,
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--norestore",
                        f"-env:UserInstallation={profile.as_uri()}",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(root),
                        str(source),
                    ],
                    shell=False,
                    check=False,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=config.timeout_seconds,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                target = root / "document.pdf"
                if completed.returncode or not target.is_file():
                    raise IngestionError("NORMALIZE_FAILED", "Office converter failed")
                result = target.read_bytes()
                version = subprocess.run(
                    [config.libreoffice_binary, "--version"],
                    shell=False,
                    check=False,
                    capture_output=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                converter = (
                    version.stdout.decode(errors="replace").strip()[:200] or "LibreOffice-version-unavailable"
                )
        else:
            value = data.decode("utf-8-sig")
            if validated.kind in {"html", "htm"}:
                value = BeautifulSoup(sanitize_html(value), "html.parser").get_text("\n")
            result, converter = _text_pdf(value), "reportlab-cjk-template-v1"
        pages = _pdf_pages(result, config.max_pages)
        return NormalizedDocument(
            result,
            validated.sha256,
            hashlib.sha256(result).hexdigest(),
            pages,
            converter,
            int((time.monotonic() - started) * 1000),
        )
    except IngestionError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise IngestionError("NORMALIZE_FAILED", "Conversion timed out", retryable=True) from exc
    except Exception as exc:
        raise IngestionError("NORMALIZE_FAILED", "Document conversion failed") from exc
