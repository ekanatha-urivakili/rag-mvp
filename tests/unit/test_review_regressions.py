import json
import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rag.api.schemas import LoginIn, ResetIn
from rag.core.logging import JsonFormatter, log_extra
from rag.graph import graph
from rag.ingestion.sniff import TEXT, sniff_mime


def test_password_whitespace_is_preserved() -> None:
    assert LoginIn(email=" test@example.com ", password="  passphrase  ").password == "  passphrase  "
    assert ResetIn(token="a" * 32, new_password="  passphrase  ").new_password == "  passphrase  "


def test_utf8_boundary_is_not_a_false_rejection() -> None:
    assert sniff_mime(b"a" * 4095 + "é".encode(), "guide.txt") == TEXT


def test_structured_log_fields_are_redacted() -> None:
    record = logging.LogRecord("test", logging.INFO, "test", 1, "message", (), None)
    for key, value in log_extra(password="private-password", token="private-token").items():
        setattr(record, key, value)
    output = JsonFormatter().format(record)
    assert "private-password" not in output and "private-token" not in output
    json.loads(output)


@pytest.mark.parametrize("length", [None, "1", "100"])
async def test_body_cap_counts_bytes_even_without_honest_length(length: str | None) -> None:
    async def endpoint(request: Request) -> Response:
        await request.body()
        return Response(status_code=204)

    app = Starlette(
        routes=[Route("/", endpoint, methods=["POST"])],
        middleware=[Middleware(RequestBodyLimitMiddleware, max_body_size=10)],
    )

    async def chunks():
        yield b"123456"
        yield b"789012"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post("/", content=chunks(), headers={"Content-Length": length} if length else {})
    assert response.status_code == 413


def test_uncited_answer_becomes_insufficient_and_emits_canonical_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_graph import _chunk

    events = []
    monkeypatch.setattr(graph, "get_stream_writer", lambda: events.append)
    out = graph.validate_citations({"answer": "Invented answer [999].", "chunks": [_chunk(1)]})
    assert out["answer"] == graph.INSUFFICIENT and out["citations"] == []
    assert events[0] == {"type": "answer", "text": graph.INSUFFICIENT}
