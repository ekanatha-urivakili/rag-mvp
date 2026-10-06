from collections.abc import AsyncIterator
from typing import Any

import anthropic
from pydantic import BaseModel

from rag.adapters.llm.base import (
    Completion,
    Message,
    RefusalError,
    StreamEvent,
    Timer,
    Usage,
    parse_structured,
    split_system,
    strict_json_schema,
)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, options: dict[str, Any]) -> None:
        self._client = client
        self.model = model
        self._effort: str | None = options.get("effort")
        self._refusal_fallback: bool = bool(options.get("refusal_fallback", False))

    def _common(self, messages: list[Message], max_tokens: int) -> dict[str, Any]:
        system, rest = split_system(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in rest],
        }
        if system:
            # Static system prompt first and marked cacheable; volatile content lives in messages.
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        if self._refusal_fallback:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        kwargs = self._common(messages, max_tokens)
        if schema is not None:
            fmt = {"type": "json_schema", "schema": strict_json_schema(schema)}
            kwargs["output_config"] = {**kwargs.get("output_config", {}), "format": fmt}
        with Timer() as t:
            resp = await self._client.beta.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise RefusalError("Model declined the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(
            text=text,
            parsed=parse_structured(text, schema) if schema else None,
            usage=Usage(resp.usage.input_tokens, resp.usage.output_tokens),
            latency_ms=t.ms,
            provider=self.provider,
            model=self.model,
        )

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        async with self._client.beta.messages.stream(**self._common(messages, max_tokens)) as s:
            async for text in s.text_stream:
                yield StreamEvent(text=text)
            final = await s.get_final_message()
        if final.stop_reason == "refusal":
            raise RefusalError("Model declined the request")
        yield StreamEvent(usage=Usage(final.usage.input_tokens, final.usage.output_tokens))
