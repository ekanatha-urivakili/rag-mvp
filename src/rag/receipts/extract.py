import re
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from rag.adapters.llm.base import Image, Message
from rag.adapters.llm.router import OnAttempt, get_router

PROMPT_VERSION = "v1"
_PROMPT = Path(__file__).parent / "prompts" / f"extract_receipt.{PROMPT_VERSION}.txt"
_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.02")
# Words that make a text document worth a (paid) extraction call. Images and scans are always tried.
_RECEIPT_HINTS = re.compile(r"(?i)\b(sub ?total|total|tax|change due|visa|mastercard|amex|receipt|cashier)\b")


class LineItem(BaseModel):
    description: str = Field(max_length=500)
    quantity: float | None
    unit_price: float | None
    amount: float


class Discount(BaseModel):
    description: str = Field(max_length=500)
    amount: float


class ReceiptExtraction(BaseModel):
    is_receipt: bool
    merchant_name: str | None = Field(max_length=200)
    merchant_address: str | None = Field(max_length=500)
    merchant_phone: str | None = Field(max_length=50)
    purchase_date: str | None
    purchase_time: str | None
    currency: str | None
    items: list[LineItem] = Field(max_length=500)
    printed_item_count: int | None
    discounts: list[Discount] = Field(max_length=200)
    subtotal: float | None
    tax: float | None
    tip: float | None
    total: float | None
    payment_method: Literal["card", "cash", "mobile_wallet", "gift_card", "other"] | None
    card_brand: str | None = Field(max_length=32)
    card_last4: str | None


@dataclass
class Receipt:
    merchant_name: str | None
    merchant_address: str | None
    merchant_phone: str | None
    purchased_on: date | None
    purchased_time: time | None
    currency: str | None
    items: list[dict[str, str | None]]
    discounts: list[dict[str, str]]
    item_count: int | None
    subtotal: Decimal | None
    discount_total: Decimal | None
    tax: Decimal | None
    tip: Decimal | None
    total: Decimal | None
    payment_method: str | None
    card_brand: str | None
    card_last4: str | None
    provider: str
    model: str
    warnings: list[str] = field(default_factory=list)


@cache
def _prompt() -> str:
    return _PROMPT.read_text().strip()


def worth_extracting(text: str, from_image: bool) -> bool:
    return from_image or len(_RECEIPT_HINTS.findall(text)) >= 2


def _money(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(v)).quantize(_CENT)


def _str(v: str | None) -> str | None:
    return (v or "").strip() or None


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= _TOLERANCE


def normalize(x: ReceiptExtraction, provider: str, model: str) -> Receipt:
    """Deterministic clean-up and cross-checks. Never "fixes" numbers: mismatches become warnings."""
    warnings: list[str] = []

    purchased_on = purchased_time = None
    if x.purchase_date:
        try:
            purchased_on = date.fromisoformat(x.purchase_date.strip())
        except ValueError:
            warnings.append(f"Unreadable date: {x.purchase_date[:40]}")
    if x.purchase_time:
        try:
            purchased_time = time.fromisoformat(x.purchase_time.strip())
        except ValueError:
            warnings.append(f"Unreadable time: {x.purchase_time[:40]}")

    digits = re.sub(r"\D", "", x.card_last4 or "")
    card_last4 = digits[-4:] if len(digits) >= 4 else None  # never store more than the last 4 digits
    currency = (x.currency or "").strip().upper()

    amounts = [Decimal(str(i.amount)).quantize(_CENT) for i in x.items]
    discounts = [abs(Decimal(str(d.amount)).quantize(_CENT)) for d in x.discounts]
    subtotal, tax, tip, total = _money(x.subtotal), _money(x.tax), _money(x.tip), _money(x.total)
    discount_total = sum(discounts, Decimal(0)) if discounts else None
    items_sum, disc = sum(amounts, Decimal(0)), discount_total or Decimal(0)

    # Discounts are printed either before the subtotal or after it; accept both layouts.
    discount_in_subtotal = subtotal is not None and _close(items_sum - disc, subtotal)
    if subtotal is not None and amounts and not discount_in_subtotal and not _close(items_sum, subtotal):
        warnings.append(f"Line items sum to {items_sum}, but the printed subtotal is {subtotal}")
    if total is not None and subtotal is not None:
        expected = subtotal + (tax or 0) + (tip or 0)
        ok = _close(expected, total) or (not discount_in_subtotal and _close(expected - disc, total))
        if not ok:
            warnings.append(f"Subtotal + tax + tip = {expected}, but the printed total is {total}")

    quantities = [i.quantity if i.quantity is not None else 1.0 for i in x.items]
    counted = int(sum(quantities)) if all(float(q).is_integer() for q in quantities) else len(x.items)
    if x.printed_item_count is not None and x.items and x.printed_item_count != counted:
        warnings.append(f"Receipt says {x.printed_item_count} items; {counted} were read")

    return Receipt(
        merchant_name=_str(x.merchant_name),
        merchant_address=_str(", ".join(s for line in (x.merchant_address or "").splitlines() if (s := line.strip()))),
        merchant_phone=_str(x.merchant_phone),
        purchased_on=purchased_on,
        purchased_time=purchased_time,
        currency=currency if re.fullmatch(r"[A-Z]{3}", currency) else None,
        items=[
            {
                "description": i.description.strip(),
                "quantity": None if i.quantity is None else f"{i.quantity:g}",
                "unit_price": None if i.unit_price is None else str(_money(i.unit_price)),
                "amount": str(a),
            }
            for i, a in zip(x.items, amounts, strict=True)
        ],
        discounts=[
            {"description": d.description.strip(), "amount": str(a)}
            for d, a in zip(x.discounts, discounts, strict=True)
        ],
        item_count=x.printed_item_count if x.printed_item_count is not None else (counted if x.items else None),
        subtotal=subtotal,
        discount_total=discount_total,
        tax=tax,
        tip=tip,
        total=total,
        payment_method=x.payment_method,
        card_brand=_str(x.card_brand),
        card_last4=card_last4,
        provider=provider,
        model=model,
        warnings=warnings,
    )


async def extract_receipt(image: Image | None, ocr_text: str, on_attempt: OnAttempt | None = None) -> Receipt | None:
    """Returns None when the model says the document is not a receipt. Raises LLMUnavailable if no model answers."""
    text = ocr_text.strip()[:20_000] or "(no text recognised)"
    user = Message(
        "user",
        f"OCR text (hint only, may contain errors):\n<ocr>\n{text.replace('</ocr>', '')}\n</ocr>",
        images=(image,) if image else (),
    )
    res = (
        await get_router()
        .for_purpose("receipt_extraction", on_attempt)
        .complete([Message("system", _prompt()), user], schema=ReceiptExtraction, max_tokens=8000)
    )
    assert isinstance(res.parsed, ReceiptExtraction)
    if not res.parsed.is_receipt:
        return None
    return normalize(res.parsed, res.provider, res.model)


def _fmt(v: Decimal | None, currency: str | None) -> str:
    return "—" if v is None else f"{v} {currency or ''}".strip()


def to_markdown(r: Receipt) -> str:
    """Searchable summary indexed alongside the OCR text, so chat can answer questions about the receipt."""
    when = " ".join(str(v) for v in (r.purchased_on, r.purchased_time) if v is not None) or "unknown"
    card = " ".join(v for v in (r.card_brand, f"ending {r.card_last4}" if r.card_last4 else None) if v)
    lines = [
        f"# Receipt: {r.merchant_name or 'unknown merchant'}",
        f"Merchant: {r.merchant_name or 'unknown'}",
        *([f"Address: {r.merchant_address}"] if r.merchant_address else []),
        *([f"Phone: {r.merchant_phone}"] if r.merchant_phone else []),
        f"Purchased: {when}",
        f"Payment: {r.payment_method or 'unknown'}{f' ({card})' if card else ''}",
        f"Item count: {r.item_count if r.item_count is not None else 'unknown'}",
        "",
        "| Item | Qty | Unit price | Amount |",
        "| --- | --- | --- | --- |",
        *(
            f"| {(i['description'] or '').replace('|', '/')} | {i['quantity'] or ''} "
            f"| {i['unit_price'] or ''} | {i['amount']} |"
            for i in r.items
        ),
        "",
        *(f"Discount: {d['description']} -{d['amount']}" for d in r.discounts),
        f"Subtotal: {_fmt(r.subtotal, r.currency)}",
        f"Discounts: {_fmt(r.discount_total, r.currency)}",
        f"Tax: {_fmt(r.tax, r.currency)}",
        f"Tip: {_fmt(r.tip, r.currency)}",
        f"Total: {_fmt(r.total, r.currency)}",
    ]
    return "\n".join(lines)
