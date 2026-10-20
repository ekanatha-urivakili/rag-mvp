import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
import ollama
import openai
import yaml
from opentelemetry.trace import Span, StatusCode
from pydantic import BaseModel

from rag.adapters.llm.anthropic import AnthropicLLM
from rag.adapters.llm.base import LLM, Completion, Message, RouteEntry, StreamEvent, StructuredOutputError, Usage
from rag.adapters.llm.ollama import OllamaLLM
from rag.adapters.llm.openai import OpenAILLM
from rag.core.config import Settings, get_settings
from rag.core.errors import LLMUnavailable
from rag.core.logging import log_extra
from rag.core.metrics import LLM_FALLBACK, LLM_LATENCY, LLM_REQUESTS, LLM_ROUTE_EXHAUSTED_AT, LLM_TOKENS
from rag.core.telemetry import tracer

log = logging.getLogger(__name__)
_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")
OnAttempt = Callable[[RouteEntry], Awaitable[None]]


def _expand(value: Any, settings: Settings) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m[1]) or str(getattr(settings, m[1].lower(), "")), value)
    if isinstance(value, dict):
        return {k: _expand(v, settings) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, settings) for v in value]
    return value


class FallbackLLM:
    """Tries each route entry in order; falls through on timeout, connection/API errors, refusals,
    or structured output that fails validation twice."""

    def __init__(
        self, purpose: str, entries: list[tuple[RouteEntry, LLM]], on_attempt: OnAttempt | None = None
    ) -> None:
        self.purpose = purpose
        self._entries = entries
        self._on_attempt = on_attempt
        self.provider, self.model = entries[0][1].provider, entries[0][1].model

    def _fell_back(self, i: int, entry: RouteEntry, err: BaseException) -> None:
        nxt = self._entries[i + 1][0].provider if i + 1 < len(self._entries) else "none"
        LLM_FALLBACK.labels(self.purpose, entry.provider, nxt).inc()
        if nxt == "none":
            LLM_ROUTE_EXHAUSTED_AT.labels(self.purpose).set(time.time())
        log.warning(
            "llm_fallback",
            extra=log_extra(
                purpose=self.purpose, provider=entry.provider, model=entry.model, to=nxt, error=type(err).__name__
            ),
        )

    def _attrs(self, entry: RouteEntry, i: int) -> dict[str, str | int | bool]:
        return {
            "rag.purpose": self.purpose,
            "gen_ai.system": entry.provider,
            "gen_ai.request.model": entry.model,
            "rag.route_position": i,
        }

    def _observe(self, entry: RouteEntry, outcome: str, t0: float, span: Span, usage: Usage | None = None) -> None:
        LLM_REQUESTS.labels(self.purpose, entry.provider, entry.model, outcome).inc()
        LLM_LATENCY.labels(self.purpose, entry.provider).observe(time.perf_counter() - t0)
        span.set_attribute("rag.outcome", outcome)
        if outcome != "ok":
            span.set_status(StatusCode.ERROR)
        if usage:
            LLM_TOKENS.labels(self.purpose, entry.provider, entry.model, "in").inc(usage.tokens_in)
            LLM_TOKENS.labels(self.purpose, entry.provider, entry.model, "out").inc(usage.tokens_out)
            span.set_attribute("gen_ai.usage.input_tokens", usage.tokens_in)
            span.set_attribute("gen_ai.usage.output_tokens", usage.tokens_out)

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        for i, (entry, llm) in enumerate(self._entries):
            if self._on_attempt:
                await self._on_attempt(entry)
            attempts = 2 if schema is not None else 1
            for _ in range(attempts):
                with tracer.start_as_current_span("llm.complete", attributes=self._attrs(entry, i)) as span:
                    t0 = time.perf_counter()
                    try:
                        async with asyncio.timeout(entry.timeout_s):
                            result = await llm.complete(
                                messages, schema=schema, max_tokens=max_tokens, temperature=temperature
                            )
                    except StructuredOutputError as e:
                        self._observe(entry, "invalid_output", t0, span)
                        last: BaseException = e
                        continue
                    except Exception as e:  # provider SDK errors, timeouts, refusals
                        self._observe(entry, "error", t0, span)
                        last = e
                        break
                    self._observe(entry, "ok", t0, span, result.usage)
                    result.fallback_used = i > 0
                    return result
            self._fell_back(i, entry, last)
        raise LLMUnavailable(f"All providers failed for route '{self.purpose}'")

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        for i, (entry, llm) in enumerate(self._entries):
            if self._on_attempt:
                await self._on_attempt(entry)
            # Not a current-context span: it stays open across yields of this generator.
            span = tracer.start_span("llm.stream", attributes=self._attrs(entry, i))
            t0 = time.perf_counter()
            it = llm.stream(messages, max_tokens=max_tokens).__aiter__()
            usage = None
            try:
                try:
                    # Fallback is only possible before the first event reaches the client.
                    async with asyncio.timeout(entry.timeout_s):
                        first = await it.__anext__()
                except Exception as e:
                    self._observe(entry, "error", t0, span)
                    self._fell_back(i, entry, e)
                    continue
                usage = first.usage
                first.provider, first.model, first.fallback_used = llm.provider, llm.model, i > 0
                yield first
                async with asyncio.timeout(entry.timeout_s):
                    async for ev in it:
                        ev.provider, ev.model, ev.fallback_used = llm.provider, llm.model, i > 0
                        usage = ev.usage or usage
                        yield ev
            except BaseException as e:
                cancelled = isinstance(e, GeneratorExit | asyncio.CancelledError)
                self._observe(entry, "cancelled" if cancelled else "error", t0, span, usage)
                raise
            else:
                self._observe(entry, "ok", t0, span, usage)
            finally:
                span.end()
                if close := getattr(it, "aclose", None):
                    await close()
            return
        raise LLMUnavailable(f"All providers failed for route '{self.purpose}'")


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        raw = yaml.safe_load(Path(settings.models_config_path).read_text())
        cfg = _expand(raw, settings)
        self._routes: dict[str, list[RouteEntry]] = {
            purpose: [
                RouteEntry(
                    provider=e["provider"],
                    model=e["model"],
                    timeout_s=float(e.get("timeout_s", 30)),
                    options={k: v for k, v in e.items() if k not in {"provider", "model", "timeout_s"}},
                )
                for e in entries
            ]
            for purpose, entries in cfg["routes"].items()
        }
        self._settings = settings
        self._clients: dict[str, Any] = {}

    def _client(self, provider: str) -> Any:
        if provider in self._clients:
            return self._clients[provider]
        s = self._settings
        if provider == "ollama":
            client: Any = ollama.AsyncClient(host=s.ollama_base_url)
        elif provider == "anthropic":
            key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
            client = anthropic.AsyncAnthropic(api_key=key, max_retries=2)
        elif provider == "openai":
            key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
            client = openai.AsyncOpenAI(api_key=key, max_retries=2)
        else:
            raise ValueError(f"Unknown provider {provider}")
        self._clients[provider] = client
        return client

    def _available(self, provider: str) -> bool:
        s = self._settings
        return {
            "ollama": True,
            "anthropic": bool(s.anthropic_api_key and s.anthropic_api_key.get_secret_value()),
            "openai": bool(s.openai_api_key and s.openai_api_key.get_secret_value()),
        }.get(provider, False)

    async def warm_local_models(self) -> None:
        models = {
            e.model: e.options.get("keep_alive", "30m")
            for es in self._routes.values()
            for e in es
            if e.provider == "ollama" and e.options.get("warm", True)
        }
        client: ollama.AsyncClient = self._client("ollama")
        for model, keep_alive in models.items():
            await client.generate(model=model, prompt="", keep_alive=keep_alive)  # empty prompt = load only

    def has_provider(self, purpose: str) -> bool:
        return any(self._available(e.provider) for e in self._routes.get(purpose, []))

    def for_purpose(self, purpose: str, on_attempt: OnAttempt | None = None) -> FallbackLLM:
        entries: list[tuple[RouteEntry, LLM]] = []
        only = os.environ.get("RAG_PIN_PROVIDER")  # eval runs compare providers one at a time
        for e in self._routes[purpose]:
            if not self._available(e.provider) or (only and purpose == "generation" and e.provider != only):
                continue
            cls = {"ollama": OllamaLLM, "anthropic": AnthropicLLM, "openai": OpenAILLM}[e.provider]
            entries.append((e, cls(self._client(e.provider), e.model, e.options)))
        if not entries:
            raise LLMUnavailable(f"No configured provider for route '{purpose}'")
        return FallbackLLM(purpose, entries, on_attempt)


@lru_cache
def get_router() -> ModelRouter:
    return ModelRouter(get_settings())
