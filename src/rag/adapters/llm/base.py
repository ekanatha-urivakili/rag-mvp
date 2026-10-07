import base64
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: Literal["image/jpeg", "image/png", "image/webp"]

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str
    images: tuple[Image, ...] = ()


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class Completion:
    text: str
    parsed: BaseModel | None
    usage: Usage
    latency_ms: int
    provider: str
    model: str
    fallback_used: bool = False


@dataclass
class StreamEvent:
    """Either a text delta or (last event) the final usage."""

    text: str = ""
    usage: Usage | None = None
    provider: str = ""
    model: str = ""
    fallback_used: bool = False


@dataclass
class RouteEntry:
    provider: str
    model: str
    timeout_s: float
    options: dict[str, Any] = field(default_factory=dict)


class StructuredOutputError(Exception):
    pass


class RefusalError(Exception):
    pass


class LLM(Protocol):
    provider: str
    model: str

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion: ...

    def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[StreamEvent]: ...


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    return system, [m for m in messages if m.role != "system"]


# Not supported by every provider's constrained decoding (e.g. Ollama grammars); Pydantic re-checks them.
_UNPORTABLE = ("maxLength", "minLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern")


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema tightened for provider strict modes: every property required, no extras."""
    js = schema.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            for key in _UNPORTABLE:
                node.pop(key, None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                tighten(v)
        elif isinstance(node, list):
            for v in node:
                tighten(v)

    tighten(js)
    return js


def parse_structured(text: str, schema: type[BaseModel]) -> BaseModel:
    # Always re-validate: small local models occasionally emit invalid JSON (OWASP API10).
    try:
        return schema.model_validate_json(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return schema.model_validate(json.loads(text[start : end + 1]))
            except ValueError as e:
                raise StructuredOutputError(str(e)) from e
        raise StructuredOutputError("No JSON object in model output") from None


class Timer:
    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.ms = int((time.perf_counter() - self.start) * 1000)
