import asyncio
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from rag.adapters.llm.anthropic import AnthropicLLM
from rag.adapters.llm.base import Message, RouteEntry, StreamEvent, Usage, supports_temperature
from rag.adapters.llm.openai import OpenAILLM
from rag.adapters.llm.router import FallbackLLM
from rag.adapters.storage import ObjectStorage
from rag.core.config import get_settings
from rag.core.errors import UnsupportedMediaType
from rag.domain.models import ChatTurn
from rag.email import handler
from rag.graph.graph import _alternating_messages
from rag.ingestion.parsers import parse
from rag.ingestion.sniff import PDF, sniff_mime
from rag.receipts.ocr import llm_images, render_pdf_page


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


async def test_old_queued_ingestion_email_hides_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    mailer = AsyncMock()
    monkeypatch.setattr(handler, "get_mailer", lambda: mailer)
    await handler.send_email(
        "job",
        {
            "template": "ingestion_failed",
            "to": "recipient@example.com",
            "context": {"title": "file", "error": "secret provider response", "link": "/documents"},
        },
    )
    assert "secret provider response" not in str(mailer.send.call_args)


def test_multi_page_pdf_receipt_renders_all_pages() -> None:
    with pymupdf.open() as doc:
        doc.new_page(width=72, height=72)
        doc.new_page(width=72, height=72)
        raw = doc.tobytes()
    out = llm_images(raw, PDF)
    assert len(out) == 2
    assert all(img.media_type == "image/jpeg" and img.data for img in out)


def test_scanned_pdf_rejects_pages_above_max_ocr_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_ocr_pages", 1)
    with pymupdf.open() as doc:
        doc.new_page(width=72, height=72)
        doc.new_page(width=72, height=72)
        raw = doc.tobytes()
    with pytest.raises(ValueError, match="scanned pages"):
        parse(raw, PDF)


def test_hybrid_pdf_runs_ocr_on_image_only_pages() -> None:
    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).text((20, 40), "SCANNED PAGE TWO", fill="black", font=ImageFont.load_default(size=24))
    png = io.BytesIO()
    img.save(png, format="PNG")

    with pymupdf.open() as doc:
        p1 = doc.new_page(width=300, height=200)
        p1.insert_text((20, 50), "Digital page one content")
        p2 = doc.new_page(width=300, height=200)
        p2.insert_image(p2.rect, stream=png.getvalue())
        raw = doc.tobytes()

    pages = parse(raw, PDF)
    assert len(pages) == 2
    assert pages[0].ocr is False and "Digital page one" in pages[0].markdown
    assert pages[1].ocr is True and "SCANNED" in pages[1].markdown.upper()


def test_llm_temperature_capability_guard() -> None:
    assert supports_temperature("anthropic", "claude-haiku-4-5", {}) is True
    assert supports_temperature("anthropic", "claude-sonnet-5-5", {}) is False
    assert supports_temperature("openai", "gpt-4o-mini", {}) is True
    assert supports_temperature("openai", "gpt-5-mini", {}) is False
    assert supports_temperature("openai", "gpt-5-mini", {"supports_temperature": True}) is True

    anth_haiku = AnthropicLLM(Mock(), "claude-haiku-4-5", {})
    assert anth_haiku._common([Message("user", "hi")], 100, 0.0)["temperature"] == 0.0
    anth_5 = AnthropicLLM(Mock(), "claude-sonnet-5-5", {})
    assert "temperature" not in anth_5._common([Message("user", "hi")], 100, 0.0)

    oa_4o = OpenAILLM(Mock(), "gpt-4o-mini", {})
    assert oa_4o._sampling_kwargs(0.0) == {"temperature": 0.0}
    oa_5 = OpenAILLM(Mock(), "gpt-5-mini", {})
    assert oa_5._sampling_kwargs(0.0) == {}


async def test_fallback_stream_uses_per_chunk_inactivity_timeout() -> None:
    class _SlowSteadyLLM:
        provider = "ollama"
        model = "steady"

        async def stream(self, messages: list[Message], **_: Any) -> AsyncIterator[StreamEvent]:
            for tok in ("a", "b", "c", "d"):
                await asyncio.sleep(0.02)
                yield StreamEvent(text=tok)
            yield StreamEvent(usage=Usage(1, 4))

    fb = FallbackLLM("generation", [(RouteEntry("ollama", "steady", 0.05), _SlowSteadyLLM())])  # type: ignore[list-item]
    chunks = [ev.text async for ev in fb.stream([Message("user", "hi")], max_tokens=10) if ev.text]
    assert "".join(chunks) == "abcd"


def test_alternating_messages_coalesces_consecutive_user_turns() -> None:
    history = [
        ChatTurn(role="assistant", content="orphan leading assistant"),
        ChatTurn(role="user", content="aborted turn 1"),
        ChatTurn(role="user", content="aborted turn 2"),
        ChatTurn(role="assistant", content="good reply"),
        ChatTurn(role="user", content="aborted turn 3"),
    ]
    msgs = _alternating_messages(history, "latest question")
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0].content == "aborted turn 1\n\naborted turn 2"
    assert msgs[2].content == "aborted turn 3\n\nlatest question"


def test_golden_eval_dataset_has_at_least_50_entries() -> None:
    golden = Path(__file__).resolve().parents[2] / "evals" / "golden.jsonl"
    rows = [json.loads(line) for line in golden.read_text().splitlines() if line.strip()]
    assert len(rows) >= 50
    assert all("question" in r and "expected_sources" in r for r in rows)
