"""Load test against the API (ARCHITECTURE §8 Phase 6). Measures chat time-to-first-token as its own
Locust entry ("SSE chat ttft") and fails the run when its p95 exceeds TTFT_P95_TARGET_S.

  uvx locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 10 -r 2 -t 5m

Env: LOADTEST_EMAIL / LOADTEST_PASSWORD (an existing account; `rag admin create` makes one),
TTFT_P95_TARGET_S (default 3). All simulated users share one session: login is rate-limited per email.
The API's chat limits (RL_CHAT_PER_USER, RL_CHAT_PER_TENANT) must be raised in the target environment,
otherwise 429s are reported as failures. Ollama concurrency is bounded by OLLAMA_NUM_PARALLEL on the host.
"""

import json
import os
import random
import threading
import time
from pathlib import Path

from locust import HttpUser, between, events, task
from locust.env import Environment

EMAIL = os.environ.get("LOADTEST_EMAIL", "")
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "")
TTFT_TARGET_S = float(os.environ.get("TTFT_P95_TARGET_S", "3"))
GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden.jsonl"
QUESTIONS = [json.loads(line)["question"] for line in GOLDEN.read_text().splitlines() if line.strip()] or [
    "What does the onboarding guide say about laptops?"
]
GREETINGS = ["hello", "what can you do?", "thanks!"]

_token = ""
_token_lock = threading.Lock()


def _login(host: str) -> None:
    import requests

    global _token
    r = requests.post(f"{host}/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    _token = r.json()["access_token"]


@events.test_start.add_listener
def _start(environment: Environment, **_: object) -> None:
    if not EMAIL or not PASSWORD:
        raise SystemExit("Set LOADTEST_EMAIL and LOADTEST_PASSWORD")
    _login(environment.host or "")


@events.quitting.add_listener
def _gate(environment: Environment, **_: object) -> None:
    ttft = environment.stats.get("SSE chat ttft", "SSE")
    if environment.stats.total.num_failures:
        environment.process_exit_code = 1
    if ttft.num_requests == 0:
        print("No successful chat answers were measured")
        environment.process_exit_code = 1
        return
    p95_s = ttft.get_response_time_percentile(0.95) / 1000
    print(f"chat TTFT p95 = {p95_s:.2f}s (target < {TTFT_TARGET_S}s) over {ttft.num_requests} answers")
    if p95_s >= TTFT_TARGET_S:
        environment.process_exit_code = 1


class ApiUser(HttpUser):
    wait_time = between(1, 3)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_token}"}

    def _reauth_if_expired(self, status: int, seen_token: str) -> None:
        if status != 401:
            return
        with _token_lock:
            if _token == seen_token:  # another user may have refreshed already
                _login(self.host or "")

    @task(6)
    def chat(self) -> None:
        question = random.choice(QUESTIONS) if random.random() < 0.85 else random.choice(GREETINGS)  # noqa: S311
        token = _token
        started = time.perf_counter()
        first: float | None = None
        completed = False
        with self.client.post(
            "/v1/chat",
            json={"message": question},
            headers=self._headers(),
            stream=True,
            catch_response=True,
            name="/v1/chat (until headers)",
            timeout=(10, 120),
        ) as r:
            if r.status_code != 200:
                self._reauth_if_expired(r.status_code, token)
                r.failure(f"HTTP {r.status_code}")
                return
            event = ""
            for raw in r.iter_lines(decode_unicode=True):
                if raw.startswith("event: "):
                    event = raw[7:]
                    if event in {"token", "answer"} and first is None:
                        first = time.perf_counter()
                    elif event == "error":
                        r.failure("SSE error event")
                        return
                elif raw.startswith("data: ") and event == "done":
                    completed = True
                    break
            if first is None or not completed:
                r.failure("Chat stream ended without text and a done event")
                return
            r.success()
        done = time.perf_counter()
        for name, seconds in (("SSE chat ttft", first - started), ("SSE chat full answer", done - started)):
            self.environment.events.request.fire(
                request_type="SSE",
                name=name,
                response_time=seconds * 1000,
                response_length=0,
                exception=None,
                context={},
            )

    @task(2)
    def history(self) -> None:
        r = self.client.get("/v1/conversations?limit=50", headers=self._headers(), name="/v1/conversations")
        self._reauth_if_expired(r.status_code, _token)

    @task(1)
    def documents(self) -> None:
        self.client.get("/v1/documents", headers=self._headers(), name="/v1/documents")

    @task(1)
    def me(self) -> None:
        self.client.get("/v1/me", headers=self._headers(), name="/v1/me")
