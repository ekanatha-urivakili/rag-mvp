"""Thin API client for the Streamlit UI. Tokens live in server-side session state, never in the browser."""

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
_TIMEOUT = httpx.Timeout(30.0, read=120.0)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _headers() -> dict[str, str]:
    token = st.session_state.get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _raise_for(r: httpx.Response) -> None:
    if r.is_success:
        return
    try:
        message = r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = f"Request failed ({r.status_code})"
    raise ApiError(r.status_code, message)


def store_tokens(data: dict[str, Any]) -> None:
    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    st.session_state.pop("me", None)


def clear_session() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def _try_refresh() -> bool:
    rt = st.session_state.get("refresh_token")
    if not rt:
        return False
    r = httpx.post(f"{API_URL}/v1/auth/refresh", json={"refresh_token": rt}, timeout=_TIMEOUT)
    if not r.is_success:
        clear_session()
        return False
    store_tokens(r.json())
    return True


def request(method: str, path: str, **kwargs: Any) -> Any:
    r = httpx.request(method, f"{API_URL}{path}", headers=_headers(), timeout=_TIMEOUT, **kwargs)
    if r.status_code == 401 and st.session_state.get("refresh_token") and _try_refresh():
        r = httpx.request(method, f"{API_URL}{path}", headers=_headers(), timeout=_TIMEOUT, **kwargs)
    _raise_for(r)
    return r.json() if r.content else None


def public_post(path: str, body: dict[str, Any]) -> Any:
    r = httpx.post(f"{API_URL}{path}", json=body, timeout=_TIMEOUT)
    _raise_for(r)
    return r.json() if r.content else None


def me() -> dict[str, Any] | None:
    if "access_token" not in st.session_state:
        return None
    if "me" not in st.session_state:
        try:
            st.session_state["me"] = request("GET", "/v1/me")
        except ApiError:
            clear_session()
            return None
    return st.session_state["me"]  # type: ignore[no-any-return]


def can(permission: str) -> bool:
    info = me()
    return bool(info and permission in info["permissions"])


def chat_stream(body: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Yields (event, data) pairs from the /v1/chat SSE stream."""
    for attempt in range(2):
        with httpx.stream("POST", f"{API_URL}/v1/chat", json=body, headers=_headers(), timeout=_TIMEOUT) as r:
            if r.status_code == 401 and attempt == 0 and _try_refresh():
                continue
            if not r.is_success:
                r.read()
                _raise_for(r)
            event = "message"
            for line in r.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    yield event, json.loads(line[6:])
            return
