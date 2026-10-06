import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
import ollama
import openai
import yaml
from pydantic import BaseModel

from rag.adapters.llm.anthropic import AnthropicLLM
from rag.adapters.llm.base import LLM, Completion, Message, RouteEntry, StreamEvent, StructuredOutputError
from rag.adapters.llm.ollama import OllamaLLM
from rag.adapters.llm.openai import OpenAILLM
from rag.core.config import Settings, get_settings
from rag.core.errors import LLMUnavailable
from rag.core.logging import log_extra

log = logging.getLogger(__name__)
_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


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

    def __init__(self, purpose: str, entries: list[tuple[RouteEntry, LLM]]) -> None:
        self.purpose = purpose
        self._entries = entries
        self.provider, self.model = entries[0][1].provider, entries[0][1].model

    def _fell_back(self, entry: RouteEntry, err: BaseException) -> None:
        log.warning(
            "llm_fallback",
            extra=log_extra(purpose=self.purpose, provider=entry.provider, model=entry.model, error=type(err).__name__),
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        for i, (entry, llm) in enumerate(self._entries):
            attempts = 2 if schema is not None else 1
            for _ in range(attempts):
                try:
                    async with asyncio.timeout(entry.timeout_s):
                        result = await llm.complete(
                            messages, schema=schema, max_tokens=max_tokens, temperature=temperature
                        )
                    result.fallback_used = i > 0
                    return result
                except StructuredOutputError as e:
                    last: BaseException = e
                    continue
                except Exception as e:  # provider SDK errors, timeouts, refusals
                    last = e
                    break
            self._fell_back(entry, last)
        raise LLMUnavailable(f"All providers failed for route '{self.purpose}'")

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        for i, (entry, llm) in enumerate(self._entries):
            it = llm.stream(messages, max_tokens=max_tokens).__aiter__()
            try:
                # Fallback is only possible before the first token reaches the client.
                async with asyncio.timeout(entry.timeout_s):
                    first = await it.__anext__()
            except Exception as e:
                self._fell_back(entry, e)
                continue
            first.provider, first.model, first.fallback_used = llm.provider, llm.model, i > 0
            yield first
            async with asyncio.timeout(entry.timeout_s):
                async for ev in it:
                    ev.provider, ev.model, ev.fallback_used = llm.provider, llm.model, i > 0
                    yield ev
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
            if e.provider == "ollama"
        }
        client: ollama.AsyncClient = self._client("ollama")
        for model, keep_alive in models.items():
            await client.generate(model=model, prompt="", keep_alive=keep_alive)  # empty prompt = load only

    def for_purpose(self, purpose: str) -> FallbackLLM:
        entries: list[tuple[RouteEntry, LLM]] = []
        only = os.environ.get("RAG_PIN_PROVIDER")  # eval runs compare providers one at a time
        for e in self._routes[purpose]:
            if not self._available(e.provider) or (only and purpose == "generation" and e.provider != only):
                continue
            cls = {"ollama": OllamaLLM, "anthropic": AnthropicLLM, "openai": OpenAILLM}[e.provider]
            entries.append((e, cls(self._client(e.provider), e.model, e.options)))
        if not entries:
            raise LLMUnavailable(f"No configured provider for route '{purpose}'")
        return FallbackLLM(purpose, entries)


@lru_cache
def get_router() -> ModelRouter:
    return ModelRouter(get_settings())
