import io
import zipfile

import pytest

from rag.core.errors import UnsupportedMediaType
from rag.ingestion.sniff import DOCX, HTML, MARKDOWN, PDF, TEXT, sanitize_filename, sniff_mime


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_detects_by_content_not_name() -> None:
    assert sniff_mime(b"%PDF-1.7 ...", "notes.txt") == PDF
    assert sniff_mime(_zip({"word/document.xml": b"<x/>", "[Content_Types].xml": b"<x/>"}), "a.pdf") == DOCX
    assert sniff_mime(b"<!DOCTYPE html><html></html>", "page") == HTML
    assert sniff_mime(b"# Title\n\nbody", "readme.md") == MARKDOWN
    assert sniff_mime(b"plain words", "notes.txt") == TEXT


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"MZ\x90\x00\x03\x00\x00\x00", "setup.txt"),  # executable disguised as text
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image.txt"),
        (b"\xff\xfe\x00bad utf", "x.txt"),
        (b"just text", "script.sh"),
        (_zip({"evil.sh": b"rm -rf /"}), "archive.docx"),  # non-docx zip
    ],
)
def test_rejects(data: bytes, name: str) -> None:
    with pytest.raises(UnsupportedMediaType):
        sniff_mime(data, name)


def test_zip_bomb_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_docx_uncompressed_bytes", 1000)
    bomb = _zip({"word/document.xml": b"0" * 10_000, "[Content_Types].xml": b"<x/>"})
    with pytest.raises(UnsupportedMediaType):
        sniff_mime(bomb, "bomb.docx")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("report<script>.pdf", "report_script_.pdf"),
        ("a\x00b\x1f.txt", "ab.txt"),
        ("", "document"),
        (None, "document"),
        ("....", "document"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected
