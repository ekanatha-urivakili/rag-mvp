import streamlit as st
import views
from api import ApiError, can, me

st.set_page_config(page_title="RAG Assistant", page_icon=":material/forum:", layout="wide")

# Single-use tokens arrive as ?token=… from email links: capture once, then strip from the URL.
if token := st.query_params.get("token"):
    st.session_state["link_token"] = token
    st.query_params.clear()

try:
    info = me()
except ApiError as exc:
    st.error(exc.message)
    st.stop()
public = [
    st.Page(views.verify_signup, title="Verify email", url_path="verify_signup", icon=":material/mail:"),
    st.Page(views.reset_password, title="Reset password", url_path="reset_password", icon=":material/key:"),
    st.Page(views.accept_invite, title="Accept invite", url_path="accept_invite", icon=":material/mail:"),
]
if info is None:
    pages = [st.Page(views.login, title="Sign in", icon=":material/login:", default=True), *public]
    nav = st.navigation(pages, position="hidden")
else:
    pages = [st.Page(views.chat, title="Chat", icon=":material/forum:", default=True)]
    if can("document:read"):
        pages.append(st.Page(views.documents, title="Documents", url_path="documents", icon=":material/description:"))
    if can("member:manage"):
        pages.append(st.Page(views.members, title="Members", url_path="members", icon=":material/group:"))
    if can("apikey:manage") or can("audit:read"):
        pages.append(st.Page(views.settings, title="Settings", url_path="settings", icon=":material/settings:"))
    nav = st.navigation(pages + public if "link_token" in st.session_state else pages)
    views.sidebar(info)
nav.run()
