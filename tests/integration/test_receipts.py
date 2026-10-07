"""Receipt upload → OCR → extraction → receipts API, with the LLM call faked (the real one takes minutes locally)."""

import io
import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from qdrant_client import models as qm
from sqlalchemy import select

from rag.adapters.llm.base import RouteEntry
from rag.core.config import get_settings
from rag.db.models import Document
from rag.db.session import get_sessionmaker
from rag.ingestion import pipeline
from rag.receipts.extract import LineItem, ReceiptExtraction, normalize
from rag.worker import loop
from tests.integration.conftest import Account, make_tenant


def _receipt_png() -> bytes:
    img = Image.new("RGB", (520, 200), "white")
    draw, font = ImageDraw.Draw(img), ImageFont.load_default(size=28)
    for i, line in enumerate(["CORNER CAFE", "LATTE 4.50", "TOTAL 4.50"]):
        draw.text((20, 20 + 60 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_receipt_upload_is_extracted_and_tenant_scoped(
    client: httpx.AsyncClient, tenant: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_extract(image: Any, ocr_text: str, on_attempt: Any) -> Any:
        await on_attempt(RouteEntry("ollama", "qwen3-vl:latest", 1))
        async with get_sessionmaker()() as db:  # progress is visible to other sessions while extracting
            seen["progress"] = (await db.execute(select(Document.progress))).scalar_one()
        seen["ocr"], seen["image"] = ocr_text, image.media_type
        x = ReceiptExtraction(
            is_receipt=True,
            merchant_name="CORNER CAFE",
            merchant_address=None,
            merchant_phone=None,
            purchase_date="2026-10-01",
            purchase_time=None,
            currency="USD",
            items=[LineItem(description="LATTE", quantity=None, unit_price=None, amount=4.5)],
            printed_item_count=None,
            discounts=[],
            subtotal=None,
            tax=None,
            tip=None,
            total=4.5,
            payment_method="cash",
            card_brand=None,
            card_last4=None,
        )
        return normalize(x, "ollama", "qwen3-vl:latest")

    monkeypatch.setattr(pipeline, "extract_receipt", fake_extract)
    dim = get_settings().embedding_dim
    dense = AsyncMock(model="test", embed=AsyncMock(side_effect=lambda texts: [[1.0] * dim for _ in texts]))
    sparse = qm.SparseVector(indices=[1], values=[1.0])
    sparse_embedder = AsyncMock(embed_documents=AsyncMock(side_effect=lambda texts: [sparse for _ in texts]))
    monkeypatch.setattr(pipeline, "get_embedder", lambda: dense)
    monkeypatch.setattr(pipeline, "get_sparse_embedder", lambda: sparse_embedder)
    r = await client.post("/v1/receipts", files={"file": ("cafe.png", _receipt_png())}, headers=tenant.headers)
    assert r.status_code == 202, r.text
    doc_id = r.json()["document_id"]
    assert await loop.run_once()

    assert seen["progress"] == {"step": "extracting", "provider": "ollama", "model": "qwen3-vl:latest"}
    assert "TOTAL" in seen["ocr"]
    assert seen["image"] == "image/jpeg"
    doc = (await client.get(f"/v1/documents/{doc_id}", headers=tenant.headers)).json()
    assert (doc["status"], doc["progress"], doc["error"]) == ("ready", None, None)

    listed = (await client.get("/v1/receipts", headers=tenant.headers)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["merchant_name"] == "CORNER CAFE"
    detail = (await client.get(f"/v1/receipts/{doc_id}", headers=tenant.headers)).json()
    assert detail["total"] == "4.50"
    assert detail["items"] == [{"description": "LATTE", "quantity": None, "unit_price": None, "amount": "4.50"}]
    assert detail["model"] == "qwen3-vl:latest"

    other = await make_tenant(client, "globex")
    assert (await client.get(f"/v1/receipts/{doc_id}", headers=other.headers)).status_code == 404
    assert (await client.get("/v1/receipts", headers=other.headers)).json()["total"] == 0

    assert (await client.delete(f"/v1/documents/{doc_id}", headers=tenant.headers)).status_code == 204
    assert (await client.get(f"/v1/receipts/{doc_id}", headers=tenant.headers)).status_code == 404


async def test_receipt_api_rejects_unknown_id(client: httpx.AsyncClient, tenant: Account) -> None:
    r = await client.get(f"/v1/receipts/{uuid.uuid4()}", headers=tenant.headers)
    assert r.status_code == 404
