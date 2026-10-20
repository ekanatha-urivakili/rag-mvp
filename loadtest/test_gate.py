"""Run with LOCUST_SKIP_MONKEY_PATCH=1 uv run --with locust==2.46.7 python -m pytest -q loadtest/test_gate.py."""

from unittest.mock import MagicMock

import pytest
from loadtest.locustfile import TTFT_TARGET_S, ApiUser, _gate
from locust.env import Environment


def test_gate_rejects_empty_chat_sample() -> None:
    environment = Environment()
    _gate(environment)
    assert environment.process_exit_code == 1


@pytest.mark.parametrize("failed", [True, False])
def test_gate_requires_latency_target_and_no_failures(failed: bool) -> None:
    environment = Environment()
    environment.stats.log_request("SSE", "SSE chat ttft", 100, 0)
    if failed:
        environment.stats.log_error("POST", "/v1/chat", "failed")
    _gate(environment)
    assert (environment.process_exit_code == 1) is failed


def test_gate_rejects_slow_answers() -> None:
    environment = Environment()
    environment.stats.log_request("SSE", "SSE chat ttft", (TTFT_TARGET_S + 1) * 1000, 0)
    _gate(environment)
    assert environment.process_exit_code == 1


@pytest.mark.parametrize("done", [True, False])
def test_stream_must_finish_with_done_event(done: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    environment = Environment(host="http://localhost:8000")
    monkeypatch.setattr(ApiUser, "host", environment.host)
    user = ApiUser(environment)
    user.client = MagicMock()
    response = user.client.post.return_value.__enter__.return_value
    response.status_code = 200
    lines = ["event: token", 'data: {"text":"hello"}', ""]
    if done:
        lines.extend(["event: done", 'data: {"message_id":"test"}', ""])
    response.iter_lines.return_value = lines
    user.chat()
    if done:
        response.success.assert_called_once()
        response.failure.assert_not_called()
    else:
        response.failure.assert_called_once()
        response.success.assert_not_called()
