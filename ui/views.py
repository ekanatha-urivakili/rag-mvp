import contextlib
from typing import Any

import pandas as pd
import streamlit as st
from api import ApiError, can, chat_stream, clear_session, me, public_post, request, store_tokens

ROLES = ["viewer", "editor", "admin"]


def _error(e: ApiError) -> None:
    st.error(e.message)


# --- Public pages -------------------------------------------------------------


def login() -> None:
    st.title("Sign in")
    with st.form("login"):
        email = st.text_input("Email", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                store_tokens(public_post("/v1/auth/login", {"email": email, "password": password}))
                st.rerun()
            except ApiError as e:
                _error(e)
    with st.expander("Forgot your password?"), st.form("forgot"):
        email = st.text_input("Account email")
        if st.form_submit_button("Send reset link"):
            try:
                public_post("/v1/auth/password/forgot", {"email": email})
                st.success("If an account exists for that email, a reset link is on its way.")
            except ApiError as e:
                _error(e)


def reset_password() -> None:
    st.title("Choose a new password")
    token = st.session_state.get("link_token")
    if not token:
        st.warning("Open this page from the link in your password reset email.")
        return
    with st.form("reset"):
        pw = st.text_input("New password (min 12 characters)", type="password", autocomplete="new-password")
        pw2 = st.text_input("Repeat password", type="password", autocomplete="new-password")
        if st.form_submit_button("Set password", type="primary"):
            if pw != pw2:
                st.error("Passwords don't match.")
                return
            try:
                public_post("/v1/auth/password/reset", {"token": token, "new_password": pw})
                clear_session()
                st.success("Password updated. Sign in with your new password.")
            except ApiError as e:
                _error(e)


def accept_invite() -> None:
    st.title("Join your team")
    token = st.session_state.get("link_token")
    if not token:
        st.warning("Open this page from the link in your invitation email.")
        return
    st.caption("New here? Choose a password. Already have an account? Leave it empty.")
    with st.form("accept"):
        pw = st.text_input("Password (min 12 characters)", type="password", autocomplete="new-password")
        if st.form_submit_button("Accept invitation", type="primary"):
            try:
                data = public_post("/v1/invitations/accept", {"token": token, "password": pw or None})
                st.session_state.pop("link_token", None)
                store_tokens(data)
                st.rerun()
            except ApiError as e:
                _error(e)


# --- Shell --------------------------------------------------------------------


def sidebar(info: dict[str, Any]) -> None:
    with st.sidebar:
        st.markdown(f"**{info['email'] or 'service account'}**")
        st.caption(f"{info['tenant']['name']} · {info['tenant']['role']}")
        others = [t for t in info["tenants"] if t["id"] != info["tenant"]["id"]]
        if others:
            names = {t["name"]: t["id"] for t in others}
            choice = st.selectbox("Switch workspace", ["—", *names])
            if choice != "—":
                try:
                    store_tokens(request("POST", "/v1/auth/switch-tenant", json={"tenant_id": names[choice]}))
                    st.session_state.pop("conversation_id", None)
                    st.rerun()
                except ApiError as e:
                    _error(e)
        if info["debug_enabled"]:
            st.toggle("Debug details", key="debug")
        if st.button("Sign out", icon=":material/logout:"):
            with contextlib.suppress(ApiError):
                request("POST", "/v1/auth/logout", json={"refresh_token": st.session_state.get("refresh_token")})
            clear_session()
            st.rerun()


# --- Chat ---------------------------------------------------------------------


def _render_sources(citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for c in citations:
            page = f", page {c['page']}" if c.get("page") else ""
            st.markdown(f"**[{c['n']}] {c['source']}{page}**")
            st.caption(c["snippet"])


def _feedback(message_id: str) -> None:
    rating = st.feedback("thumbs", key=f"fb-{message_id}")
    if rating is not None and st.session_state.get(f"fb-sent-{message_id}") != rating:
        try:
            request("POST", f"/v1/messages/{message_id}/feedback", json={"rating": 1 if rating == 1 else -1})
            st.session_state[f"fb-sent-{message_id}"] = rating
        except ApiError as e:
            _error(e)


def chat() -> None:
    with st.sidebar:
        st.divider()
        if st.button("New conversation", icon=":material/add:", width="stretch"):
            st.session_state.pop("conversation_id", None)
            st.rerun()
        try:
            for conv in request("GET", "/v1/conversations", params={"limit": 30}):
                if st.button(conv["title"], key=f"conv-{conv['id']}", width="stretch", type="tertiary"):
                    st.session_state["conversation_id"] = conv["id"]
                    st.rerun()
        except ApiError as e:
            _error(e)

    st.title("Ask your documents")
    conv_id = st.session_state.get("conversation_id")
    if conv_id:
        try:
            detail = request("GET", f"/v1/conversations/{conv_id}")
            for m in detail["messages"]:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
                    if m["role"] == "assistant":
                        _render_sources(m["citations"])
                        _feedback(m["id"])
        except ApiError as e:
            _error(e)
            st.session_state.pop("conversation_id", None)

    question = st.chat_input("Ask a question about your documents", max_chars=4000)
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.empty()
        result: dict[str, Any] = {"citations": [], "done": None, "debug": None, "answer": None}

        def tokens() -> Any:
            body = {"message": question, "conversation_id": conv_id}
            for event, data in chat_stream(body):
                if event == "status":
                    status.caption(f":material/progress_activity: {data['step']}…")
                elif event == "token":
                    yield data["text"]
                elif event == "answer":
                    result["answer"] = data["text"]
                elif event == "citations":
                    result["citations"] = data
                elif event == "debug":
                    result["debug"] = data
                elif event == "error":
                    st.error(data["message"])
                elif event == "done":
                    result["done"] = data

        answer = st.empty()
        try:
            with answer.container():
                streamed = st.write_stream(tokens())
            if result["answer"] is not None and result["answer"] != streamed:
                answer.markdown(result["answer"])
        except ApiError as e:
            _error(e)
        status.empty()
        _render_sources(result["citations"])
        if result["debug"] and st.session_state.get("debug"):
            st.json(result["debug"], expanded=False)
        if result["done"]:
            st.session_state["conversation_id"] = result["done"]["conversation_id"]


# --- Documents ----------------------------------------------------------------


@st.fragment(run_every=5)
def _documents_table() -> None:
    try:
        page = request("GET", "/v1/documents", params={"limit": 100})
    except ApiError as e:
        _error(e)
        return
    if not page["items"]:
        st.info("No documents yet.")
        return
    df = pd.DataFrame(page["items"])[["title", "status", "version", "chunk_count", "size_bytes", "updated_at", "error"]]
    st.dataframe(df, hide_index=True, width="stretch")
    if can("document:delete"):
        titles = {f"{d['title']} · {d['id'][:8]}": d["id"] for d in page["items"]}
        with st.form("delete-doc"):
            target = st.selectbox("Delete a document", list(titles))
            if st.form_submit_button("Delete", type="secondary"):
                try:
                    request("DELETE", f"/v1/documents/{titles[target]}")
                    st.success(f"Deleted {target}")
                except ApiError as e:
                    _error(e)


def documents() -> None:
    st.title("Documents")
    if can("document:write"):
        with st.form("upload", clear_on_submit=True):
            files = st.file_uploader(
                "Upload PDF, DOCX, HTML, Markdown or text (max 25 MB)",
                type=["pdf", "docx", "html", "htm", "md", "txt"],
                accept_multiple_files=True,
            )
            if st.form_submit_button("Upload", type="primary") and files:
                for f in files:
                    try:
                        res = request("POST", "/v1/documents", files={"file": (f.name, f.getvalue())})
                        st.toast(f"{f.name}: {res['status']}")
                    except ApiError as e:
                        st.error(f"{f.name}: {e.message}")
    _documents_table()


# --- Admin --------------------------------------------------------------------


def members() -> None:
    st.title("Members")
    info = me()
    with st.form("invite", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        email = c1.text_input("Invite by email")
        role = c2.selectbox("Role", ROLES)
        if st.form_submit_button("Send invite", type="primary") and email:
            try:
                request("POST", "/v1/invitations", json={"email": email, "role": role})
                st.success(f"Invitation sent to {email}")
            except ApiError as e:
                _error(e)
    try:
        rows = request("GET", "/v1/members")
        pending = request("GET", "/v1/invitations")
    except ApiError as e:
        _error(e)
        return
    for m in rows:
        c1, c2, c3 = st.columns([4, 2, 1])
        c1.write(m["email"])
        if info and m["user_id"] == info["user_id"]:
            c2.write(f"{m['role']} (you)")
            continue
        new_role = c2.selectbox(
            "Role", ROLES, index=ROLES.index(m["role"]), key=f"role-{m['user_id']}", label_visibility="collapsed"
        )
        if new_role != m["role"]:
            try:
                request("PATCH", f"/v1/members/{m['user_id']}", json={"role": new_role})
                st.rerun()
            except ApiError as e:
                _error(e)
        if c3.button("Remove", key=f"rm-{m['user_id']}"):
            try:
                request("DELETE", f"/v1/members/{m['user_id']}")
                st.rerun()
            except ApiError as e:
                _error(e)
    if pending:
        st.subheader("Pending invitations")
        st.dataframe(pd.DataFrame(pending)[["email", "role", "expires_at"]], hide_index=True)


def settings() -> None:
    st.title("Settings")
    if can("apikey:manage"):
        st.subheader("API keys")
        with st.form("new-key", clear_on_submit=True):
            c1, c2 = st.columns([3, 1])
            name = c1.text_input("Key name")
            role = c2.selectbox("Role", ROLES)
            if st.form_submit_button("Create key") and name:
                try:
                    created = request("POST", "/v1/api-keys", json={"name": name, "role": role})
                    st.warning("Copy this key now — it won't be shown again.")
                    st.code(created["api_key"], language=None)
                except ApiError as e:
                    _error(e)
        try:
            for k in request("GET", "/v1/api-keys"):
                c1, c2, c3 = st.columns([4, 2, 1])
                c1.write(f"{k['name']} · `rk_{k['key_prefix']}_…`")
                c2.write("revoked" if k["revoked_at"] else k["role"])
                if not k["revoked_at"] and c3.button("Revoke", key=f"revoke-{k['id']}"):
                    request("DELETE", f"/v1/api-keys/{k['id']}")
                    st.rerun()
        except ApiError as e:
            _error(e)
    if can("audit:read"):
        st.subheader("Audit log")
        try:
            entries = request("GET", "/v1/audit-log", params={"limit": 100})
            if entries:
                st.dataframe(
                    pd.DataFrame(entries)[["created_at", "action", "actor_user_id", "target_type", "target_id", "ip"]],
                    hide_index=True,
                    width="stretch",
                )
        except ApiError as e:
            _error(e)
