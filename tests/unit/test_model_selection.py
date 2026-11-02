import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr, ValidationError

from rag.adapters.llm.base import Message, RouteEntry, reasoning_efforts
from rag.adapters.llm.connections import ChatSelection, apply_effort, decrypt_key, encrypt_key
from rag.adapters.llm.openai import OpenAILLM
from rag.adapters.llm.router import ModelRouter
from rag.api.schemas import ModelConnectionIn
from rag.core.config import Settings, get_settings
from rag.core.errors import AppError
from rag.db.models import ModelConnection


def _connection() -> ModelConnection:
    return ModelConnection(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider="openai",
        model="test-model",
    )


def test_keys_are_encrypted_and_bound_to_the_connection() -> None:
    row = _connection()
    key = SecretStr("test-provider-secret")
    row.encrypted_api_key = encrypt_key(row, key)
    assert key.get_secret_value() not in row.encrypted_api_key
    assert decrypt_key(row) == key
    row.user_id = uuid.uuid4()
    with pytest.raises(AppError, match="no longer be decrypted"):
        decrypt_key(row)


def test_prod_requires_a_dedicated_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "env", "prod")
    monkeypatch.setattr(get_settings(), "model_keys_encryption_key", None)
    with pytest.raises(AppError, match="MODEL_KEYS_ENCRYPTION_KEY"):
        encrypt_key(_connection(), SecretStr("test-secret"))


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "openai", "model": "test"},
        {"provider": "ollama", "model": "test", "api_key": "test-secret"},
        {"provider": "ollama", "model": "test", "base_url": "http://localhost"},
        {"provider": "openai", "model": "bad\nmodel", "api_key": "test-secret"},
    ],
)
def test_connection_rejects_missing_keys_and_arbitrary_endpoints(body: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ModelConnectionIn.model_validate(body)


async def test_model_selection_is_request_scoped_and_covers_both_routes() -> None:
    router = ModelRouter(Settings(jwt_secret=SecretStr("x" * 48), openai_api_key=SecretStr("test-secret")))
    assert router.chat_entries()[0].provider == "openai"
    ready = [asyncio.Event(), asyncio.Event()]

    async def choose(index: int) -> None:
        entry = RouteEntry("openai", f"test-model-{index}", 10)
        async with router.select_chat(entry, SecretStr(f"test-secret-{index}")):
            ready[index].set()
            await ready[1 - index].wait()
            for purpose in ("orchestration", "generation"):
                selected = router.for_purpose(purpose)
                assert selected.model == entry.model
                assert selected.provider == "openai"
                assert len(selected._entries) == 1

    await asyncio.gather(choose(0), choose(1))
    assert router.for_purpose("orchestration").model != "test-model-0"


async def test_selection_is_reset_when_request_fails() -> None:
    router = ModelRouter(Settings(jwt_secret=SecretStr("x" * 48)))
    before = router.for_purpose("generation").model
    with pytest.raises(RuntimeError):
        async with router.select_chat(RouteEntry("ollama", "temporary-model", 10)):
            raise RuntimeError("cancelled request")
    assert router.for_purpose("generation").model == before


def test_effort_capabilities_and_validation() -> None:
    assert reasoning_efforts("openai", "gpt-6-luna") == ["low", "medium", "high", "xhigh", "max"]
    assert reasoning_efforts("openai", "gpt-5-mini") == ["low", "medium", "high"]
    for provider, model in [("ollama", "qwen3.5:4b"), ("anthropic", "claude-sonnet-5-5"), ("openai", "custom-model")]:
        assert reasoning_efforts(provider, model) == []
        with pytest.raises(AppError, match="not supported"):
            apply_effort(ChatSelection("test", RouteEntry(provider, model, 10)), "high")
    selection = ChatSelection("test", RouteEntry("openai", "gpt-6-luna", 10))
    assert apply_effort(selection, "high").entry.options["reasoning_effort"] == "high"
    assert "reasoning_effort" not in selection.entry.options


async def test_openai_receives_effort_for_complete_and_stream_with_reasoning_budget() -> None:
    client = Mock()
    create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))], usage=None)
    )
    client.chat.completions.create = create
    llm = OpenAILLM(client, "gpt-6-luna", {"reasoning_effort": "high"})
    await llm.complete([Message("user", "hello")], max_tokens=300)
    assert create.call_args.kwargs["reasoning_effort"] == "high"
    assert create.call_args.kwargs["max_completion_tokens"] == 8192
    assert "temperature" not in create.call_args.kwargs

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"))], usage=None)

    create.return_value = chunks()
    events = [event.text async for event in llm.stream([Message("user", "hello")], max_tokens=2048)]
    assert "".join(events) == "OK"
    assert create.call_args.kwargs["reasoning_effort"] == "high"
    assert create.call_args.kwargs["max_completion_tokens"] == 8192
    assert "temperature" not in create.call_args.kwargs
