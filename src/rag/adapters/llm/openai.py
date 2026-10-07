from collections.abc import AsyncIterator
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam
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


def _content(m: Message) -> str | list[dict[str, Any]]:
    if not m.images:
        return m.content
    parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
    parts += [{"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.b64()}"}} for i in m.images]
    return parts


class OpenAILLM:
    provider = "openai"

    def __init__(self, client: openai.AsyncOpenAI, model: str, options: dict[str, Any]) -> None:
        self._client = client
        self.model = model
        self._options = options

    @staticmethod
    def _msgs(messages: list[Message]) -> list[ChatCompletionMessageParam]:
        return [{"role": m.role, "content": _content(m)} for m in messages]  # type: ignore[misc]

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._msgs(messages),
            "max_completion_tokens": max_tokens,
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": strict_json_schema(schema), "strict": True},
            }
        with Timer() as t:
            resp = await self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return Completion(
            text=text,
            parsed=parse_structured(text, schema) if schema else None,
            usage=Usage(usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0),
            latency_ms=t.ms,
            provider=self.provider,
            model=self.model,
        )

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=self._msgs(messages),
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        usage = Usage()
        async for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamEvent(text=chunk.choices[0].delta.content)
            if chunk.usage:
                usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        yield StreamEvent(usage=usage)
