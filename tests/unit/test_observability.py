"""Model-route metrics and fallback accounting, chat outcome classification, and OTLP header parsing."""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from prometheus_client import REGISTRY
from pydantic import BaseModel, ValidationError

from rag.adapters.llm.base import Completion, Message, RouteEntry, StreamEvent, Usage
from rag.adapters.llm.router import FallbackLLM
from rag.api.routers.chat import _outcome
from rag.core.config import Settings
from rag.core.errors import LLMUnavailable
from rag.core.telemetry import _headers
from rag.graph.graph import INSUFFICIENT

MSGS = [Message("user", "hi")]


class _Fake:
    def __init__(self, provider: str, fail: bool = False) -> None:
        self.provider, self.model, self.fail = provider, f"{provider}-m", fail

    async def complete(
        self, messages: list[Message], *, schema: type[BaseModel] | None = None, max_tokens: int, temperature: float = 0
    ) -> Completion:
        if self.fail:
            raise ConnectionError("down")
        return Completion("ok", None, Usage(3, 5), 1, self.provider, self.model)

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        if self.fail:
            raise ConnectionError("down")
        yield StreamEvent(text="a")
        yield StreamEvent(usage=Usage(2, 4))


def _route(purpose: str, *llms: _Fake) -> FallbackLLM:
    return FallbackLLM(purpose, [(RouteEntry(f.provider, f.model, 5), f) for f in llms])


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_fallback_is_counted_with_from_and_to() -> None:
    before = _value("llm_fallback_total", purpose="t_fb", **{"from": "ollama", "to": "anthropic"})
    res = await _route("t_fb", _Fake("ollama", fail=True), _Fake("anthropic")).complete(MSGS, max_tokens=10)
    assert res.fallback_used and res.provider == "anthropic"
    assert _value("llm_fallback_total", purpose="t_fb", **{"from": "ollama", "to": "anthropic"}) == before + 1
    assert _value("llm_requests_total", purpose="t_fb", provider="ollama", model="ollama-m", outcome="error") >= 1
    assert _value("llm_tokens_total", purpose="t_fb", provider="anthropic", model="anthropic-m", direction="out") >= 5


async def test_exhausted_route_counts_fallback_to_none() -> None:
    started = time.time()
    with pytest.raises(LLMUnavailable):
        await _route("t_none", _Fake("openai", fail=True)).complete(MSGS, max_tokens=10)
    assert _value("llm_fallback_total", purpose="t_none", **{"from": "openai", "to": "none"}) == 1
    assert started <= _value("llm_route_exhausted_timestamp_seconds", purpose="t_none") <= time.time()


async def test_stream_falls_back_before_first_token_and_records_usage() -> None:
    events = [ev async for ev in _route("t_st", _Fake("ollama", fail=True), _Fake("openai")).stream(MSGS, max_tokens=5)]
    assert [e.text for e in events] == ["a", ""] and all(e.provider == "openai" and e.fallback_used for e in events)
    assert _value("llm_requests_total", purpose="t_st", provider="openai", model="openai-m", outcome="ok") == 1
    assert _value("llm_tokens_total", purpose="t_st", provider="openai", model="openai-m", direction="in") == 2


@pytest.mark.parametrize(
    ("path", "answer", "expected"),
    [
        (["analyze_query", "respond_direct"], "hello", "direct"),
        (["analyze_query", "retrieve", "insufficient_context"], INSUFFICIENT, "insufficient_context"),
        (["analyze_query", "retrieve", "generate", "validate_citations"], INSUFFICIENT, "insufficient_context"),
        (["analyze_query", "retrieve", "generate", "validate_citations"], "It is X [1].", "answered"),
    ],
)
def test_outcome(path: list[str], answer: str, expected: str) -> None:
    assert _outcome(path, answer) == expected


def test_otlp_headers_parse() -> None:
    assert _headers("Authorization=Basic abc==, x-tenant = t1,junk") == {
        "Authorization": "Basic abc==",
        "x-tenant": "t1",
    }


@pytest.mark.parametrize("token", ["", " ", "\t"])
def test_metrics_token_cannot_be_blank(token: str) -> None:
    with pytest.raises(ValidationError, match="METRICS_TOKEN must not be empty"):
        Settings(metrics_token=token)


async def test_cancelling_first_stream_read_closes_span(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from rag.adapters.llm import router as router_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(router_mod, "tracer", provider.get_tracer("test"))
    entered = asyncio.Event()
    closed = asyncio.Event()

    class Waiting(_Fake):
        async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
            try:
                entered.set()
                await asyncio.Event().wait()
                yield StreamEvent(text="unreachable")
            finally:
                closed.set()

    stream = _route("t_cancel", Waiting("ollama")).stream(MSGS, max_tokens=5)
    task = asyncio.create_task(anext(stream))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and spans[0].attributes["rag.outcome"] == "cancelled"
    provider.shutdown()


async def test_closing_stream_closes_provider_iterator() -> None:
    closed = False

    class Closing(_Fake):
        async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
            nonlocal closed
            try:
                yield StreamEvent(text="hello")
                yield StreamEvent(text="world")
            finally:
                closed = True

    stream = _route("t_close", Closing("openai")).stream(MSGS, max_tokens=5)
    assert (await anext(stream)).text == "hello"
    await stream.aclose()
    assert closed


def test_unhandled_exceptions_are_counted_as_http_500() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag.api.middleware import SecurityMiddleware

    app = FastAPI()
    app.add_middleware(SecurityMiddleware)

    @app.get("/test-unhandled")
    def fail() -> None:
        raise RuntimeError("failure")

    labels = {"method": "GET", "route": "/test-unhandled", "status": "500"}
    before = _value("http_requests_total", **labels)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/test-unhandled").status_code == 500
    assert _value("http_requests_total", **labels) == before + 1


def test_custom_http_methods_share_a_bounded_metric_label() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag.api.middleware import SecurityMiddleware

    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    before = _value("http_requests_total", method="OTHER", route="unmatched", status="404")
    with TestClient(app) as client:
        for method in ("CUSTOM-ONE", "CUSTOM-TWO"):
            assert client.request(method, "/missing").status_code == 404
            assert (
                REGISTRY.get_sample_value(
                    "http_requests_total", {"method": method, "route": "unmatched", "status": "404"}
                )
                is None
            )
    assert _value("http_requests_total", method="OTHER", route="unmatched", status="404") == before + 2
