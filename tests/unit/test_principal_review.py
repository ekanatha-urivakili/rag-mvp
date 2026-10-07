import io
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image

from rag.adapters.storage import ObjectStorage
from rag.core.config import get_settings
from rag.core.errors import UnsupportedMediaType
from rag.ingestion.parsers import parse
from rag.ingestion.sniff import PDF, sniff_mime
from rag.receipts.ocr import render_pdf_page


def test_pdf_render_rejects_dimensions_before_allocating_pixels() -> None:
    page = Mock(rect=Mock(width=100_000, height=100_000))
    with pytest.raises(ValueError, match="dimensions"):
        render_pdf_page(page)
    page.get_pixmap.assert_not_called()


def test_pdf_render_converts_cmyk_to_rgb() -> None:
    with pymupdf.open() as doc:
        page = doc.new_page(width=72, height=72)
        page.draw_rect(page.rect, color=(0, 1, 1, 0), fill=(0, 1, 1, 0))
        img = render_pdf_page(page)
    assert img.mode == "RGB" and img.size == (200, 200)


def test_pdf_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_pdf_pages", 1)
    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        raw = doc.tobytes()
    with pytest.raises(ValueError, match="page count"):
        parse(raw, PDF)


def test_pillow_bomb_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buf, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(UnsupportedMediaType, match="dimensions"):
        sniff_mime(buf.getvalue(), "bomb.png")


async def test_partial_s3_delete_is_retried() -> None:
    storage = ObjectStorage.__new__(ObjectStorage)
    storage._bucket = "test"
    storage._s3 = Mock()
    storage._s3.get_paginator.return_value.paginate.return_value = [{"Contents": [{"Key": "tenant/doc/v1"}]}]
    storage._s3.delete_objects.return_value = {"Errors": [{"Key": "tenant/doc/v1", "Code": "AccessDenied"}]}
    with pytest.raises(RuntimeError, match="could not delete"):
        await storage.delete_prefix("tenant/doc/")
    storage._s3.delete_objects.return_value = {"Deleted": [{"Key": "tenant/doc/v1"}]}
    await storage.delete_prefix("tenant/doc/")
