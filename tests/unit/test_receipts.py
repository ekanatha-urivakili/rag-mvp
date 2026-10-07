import io
from decimal import Decimal
from typing import Any

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from rag.adapters.llm.anthropic import _content as anthropic_content
from rag.adapters.llm.base import Completion, Image, Message, RouteEntry, Usage
from rag.adapters.llm.ollama import OllamaLLM
from rag.adapters.llm.openai import _content as openai_content
from rag.adapters.llm.router import FallbackLLM
from rag.core.errors import UnsupportedMediaType
from rag.ingestion.parsers import parse
from rag.ingestion.sniff import JPEG, PNG, WEBP, sniff_mime
from rag.receipts.extract import Discount, LineItem, ReceiptExtraction, normalize, to_markdown, worth_extracting
from rag.receipts.ocr import group_lines, mask_card_numbers


def _png(size: tuple[int, int] = (40, 20), fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, "white").save(buf, format=fmt)
    return buf.getvalue()


def _extraction(**overrides: Any) -> ReceiptExtraction:
    base: dict[str, Any] = {
        "is_receipt": True,
        "merchant_name": "TRADER JOE'S",
        "merchant_address": "1234 MARKET ST\n\nSAN FRANCISCO CA",
        "merchant_phone": None,
        "purchase_date": "2026-10-05",
        "purchase_time": "14:32",
        "currency": "usd",
        "items": [
            LineItem(description="BANANAS", quantity=2, unit_price=0.29, amount=0.58),
            LineItem(description="MILK", quantity=None, unit_price=None, amount=5.49),
        ],
        "printed_item_count": 3,
        "discounts": [Discount(description="MEMBER", amount=-1.0)],
        "subtotal": 5.07,
        "tax": 0.34,
        "tip": None,
        "total": 5.41,
        "payment_method": "card",
        "card_brand": "VISA",
        "card_last4": "4111111111111111",
    }
    return ReceiptExtraction(**{**base, **overrides})


# --- OCR helpers ------------------------------------------------------------------


def test_masks_only_luhn_valid_card_numbers() -> None:
    text = "VISA 4111 1111 1111 1111\nORDER 1234567890123"
    assert mask_card_numbers(text) == "VISA ************1111\nORDER 1234567890123"


def test_group_lines_rebuilds_rows_left_to_right() -> None:
    def box(x: float, y: float) -> list[list[float]]:
        return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]

    lines = group_lines([box(400, 101), box(10, 100), box(10, 140)], ["5.49", "MILK", "TOTAL"])
    assert lines == ["MILK  5.49", "TOTAL"]


def test_ocr_reads_a_rendered_receipt() -> None:
    img = PILImage.new("RGB", (520, 140), "white")
    font = ImageFont.load_default(size=28)
    ImageDraw.Draw(img).text((20, 20), "SUBTOTAL 18.05", fill="black", font=font)
    ImageDraw.Draw(img).text((20, 80), "TOTAL 18.39", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    [page] = parse(buf.getvalue(), PNG)
    assert page.ocr
    assert "18.05" in page.markdown
    assert "18.39" in page.markdown


# --- Upload sniffing ----------------------------------------------------------------


def test_sniffs_images_by_content() -> None:
    assert sniff_mime(_png(), "receipt.txt") == PNG
    assert sniff_mime(_png(fmt="JPEG"), "receipt") == JPEG
    assert sniff_mime(_png(fmt="WEBP"), "receipt") == WEBP


def test_rejects_oversized_image_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_image_pixels", 100)
    with pytest.raises(UnsupportedMediaType):
        sniff_mime(_png((20, 20)), "big.png")


# --- Normalisation and cross-checks ------------------------------------------------


def test_normalize_consistent_receipt() -> None:
    r = normalize(_extraction(), "ollama", "qwen3-vl")
    assert r.warnings == []
    assert r.card_last4 == "1111"  # only ever the last 4 digits
    assert r.currency == "USD"
    assert r.merchant_address == "1234 MARKET ST, SAN FRANCISCO CA"
    assert r.item_count == 3
    assert r.discount_total == Decimal("1.00")
    assert r.items[0] == {"description": "BANANAS", "quantity": "2", "unit_price": "0.29", "amount": "0.58"}
    assert str(r.purchased_on) == "2026-10-05"


def test_normalize_accepts_discount_after_subtotal() -> None:
    r = normalize(_extraction(subtotal=6.07, total=5.41), "ollama", "m")
    assert r.warnings == []


def test_normalize_flags_mismatches_without_changing_values() -> None:
    r = normalize(_extraction(total=9.99, printed_item_count=7, purchase_date="05/10/26"), "anthropic", "m")
    assert r.total == Decimal("9.99")
    assert r.purchased_on is None
    assert len(r.warnings) == 3


def test_markdown_summary_is_searchable() -> None:
    md = to_markdown(normalize(_extraction(), "ollama", "m"))
    assert md.startswith("# Receipt: TRADER JOE'S")
    assert "| BANANAS | 2 | 0.29 | 0.58 |" in md
    assert "Total: 5.41 USD" in md
    assert "VISA ending 1111" in md


def test_text_documents_need_receipt_words() -> None:
    assert worth_extracting("", from_image=True)
    assert not worth_extracting("Quarterly roadmap and goals", from_image=False)
    assert worth_extracting("Subtotal 10.00\nTax 0.80\nTotal 10.80", from_image=False)


# --- Vision messages and model announcements ----------------------------------------


def test_providers_receive_images() -> None:
    img = Image(b"\xff\xd8\xff", "image/jpeg")
    msg = Message("user", "read this", images=(img,))
    a = anthropic_content(msg)
    assert isinstance(a, list)
    assert a[0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "/9j/"}
    o = openai_content(msg)
    assert isinstance(o, list)
    assert o[1]["image_url"]["url"] == "data:image/jpeg;base64,/9j/"
    assert OllamaLLM._msgs([msg])[0]["images"] == [b"\xff\xd8\xff"]
    assert anthropic_content(Message("user", "plain")) == "plain"


class _Fake:
    def __init__(self, provider: str, fail: bool) -> None:
        self.provider, self.model, self._fail = provider, f"{provider}-model", fail

    async def complete(self, messages: list[Message], **_: Any) -> Completion:
        if self._fail:
            raise ConnectionError("down")
        return Completion("ok", None, Usage(), 1, self.provider, self.model)


async def test_fallback_announces_each_model_tried() -> None:
    seen: list[str] = []

    async def on_attempt(e: RouteEntry) -> None:
        seen.append(f"{e.provider}/{e.model}")

    entries: list[Any] = [
        (RouteEntry("ollama", "ollama-model", 5), _Fake("ollama", fail=True)),
        (RouteEntry("anthropic", "anthropic-model", 5), _Fake("anthropic", fail=False)),
    ]
    res = await FallbackLLM("receipt_extraction", entries, on_attempt).complete([], max_tokens=10)
    assert seen == ["ollama/ollama-model", "anthropic/anthropic-model"]
    assert (res.provider, res.fallback_used) == ("anthropic", True)
