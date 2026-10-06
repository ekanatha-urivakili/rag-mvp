from collections.abc import AsyncIterator
from typing import Any

import ollama
from pydantic import BaseModel

from rag.adapters.llm.base import (
    Completion,
    Message,
    StreamEvent,
    Timer,
    Usage,
    parse_structured,
    strict_json_schema,
)


class OllamaLLM:
    provider = "ollama"

    def __init__(self, client: ollama.AsyncClient, model: str, options: dict[str, Any]) -> None:
        self._client = client
        self.model = model
        self._think: bool = bool(options.get("think", False))
        self._keep_alive: str = str(options.get("keep_alive", "30m"))

    @staticmethod
    def _msgs(messages: list[Message]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        with Timer() as t:
            resp = await self._client.chat(
                model=self.model,
                messages=self._msgs(messages),
                format=strict_json_schema(schema) if schema else None,
                options={"temperature": temperature, "num_predict": max_tokens},
                think=self._think,
                keep_alive=self._keep_alive,
            )
        text = resp.message.content or ""
        return Completion(
            text=text,
            parsed=parse_structured(text, schema) if schema else None,
            usage=Usage(resp.prompt_eval_count or 0, resp.eval_count or 0),
            latency_ms=t.ms,
            provider=self.provider,
            model=self.model,
        )

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        resp = await self._client.chat(
            model=self.model,
            messages=self._msgs(messages),
            options={"temperature": 0.2, "num_predict": max_tokens},
            think=self._think,
            keep_alive=self._keep_alive,
            stream=True,
        )
        usage = Usage()
        async for chunk in resp:
            if chunk.message.content:
                yield StreamEvent(text=chunk.message.content)
            if chunk.done:
                usage = Usage(chunk.prompt_eval_count or 0, chunk.eval_count or 0)
        yield StreamEvent(usage=usage)
