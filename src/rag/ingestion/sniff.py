import io
import re
import unicodedata
import zipfile
from pathlib import PurePosixPath, PureWindowsPath

from rag.core.config import get_settings
from rag.core.errors import UnsupportedMediaType

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML = "text/html"
MARKDOWN = "text/markdown"
TEXT = "text/plain"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
IMAGES = frozenset({JPEG, PNG, WEBP})
ALLOWED = frozenset({PDF, DOCX, HTML, MARKDOWN, TEXT, *IMAGES})

_SAFE_NAME = re.compile(r"[^\w.\- ()]+", re.UNICODE)


def sanitize_filename(name: str | None) -> str:
    raw = PureWindowsPath(PurePosixPath(name or "").name).name  # strip any directory component
    raw = unicodedata.normalize("NFKC", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C")
    raw = _SAFE_NAME.sub("_", raw).strip(" .")
    return (raw or "document")[:200]


def _check_docx(data: bytes) -> None:
    limit = get_settings().max_docx_uncompressed_bytes
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise UnsupportedMediaType("ZIP archives other than .docx are not supported")
            if len(infos) > 5000 or sum(i.file_size for i in infos) > limit:
                raise UnsupportedMediaType("Document expands beyond the allowed size")  # zip bomb guard
    except zipfile.BadZipFile as e:
        raise UnsupportedMediaType("Corrupt .docx file") from e


def _check_image(data: bytes, mime: str) -> str:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:  # header only; pixels are decoded in the worker
            size = img.width * img.height
    except Image.DecompressionBombError as e:
        raise UnsupportedMediaType("Image dimensions are too large") from e
    except (UnidentifiedImageError, OSError) as e:
        raise UnsupportedMediaType("Corrupt image file") from e
    if size > get_settings().max_image_pixels:
        raise UnsupportedMediaType("Image dimensions are too large")  # decompression-bomb guard
    return mime


def sniff_mime(data: bytes, filename: str) -> str:
    """Detects type from content (magic bytes), never from the client-supplied Content-Type."""
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"PK\x03\x04"):
        _check_docx(data)
        return DOCX
    if data.startswith(b"\xff\xd8\xff"):
        return _check_image(data, JPEG)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _check_image(data, PNG)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _check_image(data, WEBP)
    if b"\x00" in data[:8192]:
        raise UnsupportedMediaType("Unsupported binary file type")
    try:
        head = data.decode("utf-8")[:4096].lstrip("﻿").lstrip().lower()
    except UnicodeDecodeError as e:
        raise UnsupportedMediaType("Text files must be UTF-8") from e
    ext = PurePosixPath(filename.lower()).suffix
    if ext in {".html", ".htm"} or head.startswith(("<!doctype html", "<html")):
        return HTML
    if ext in {".md", ".markdown"}:
        return MARKDOWN
    if ext in {".txt", ""}:
        return TEXT
    raise UnsupportedMediaType("Allowed types: PDF, DOCX, HTML, Markdown, plain text, JPEG, PNG, WebP")
