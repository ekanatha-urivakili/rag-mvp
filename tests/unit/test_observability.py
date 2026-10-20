"""Model-route metrics and fallback accounting, chat outcome classification, and OTLP header parsing."""

from collections.abc import AsyncIterator

import pytest
from prometheus_client import REGISTRY
from pydantic import BaseModel

from rag.adapters.llm.base import Completion, Message, RouteEntry, StreamEvent, Usage
from rag.adapters.llm.router import FallbackLLM
from rag.api.routers.chat import _outcome
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
    with pytest.raises(LLMUnavailable):
        await _route("t_none", _Fake("openai", fail=True)).complete(MSGS, max_tokens=10)
    assert _value("llm_fallback_total", purpose="t_none", **{"from": "openai", "to": "none"}) == 1


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
