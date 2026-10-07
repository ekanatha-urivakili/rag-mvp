from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

UI = Path(__file__).resolve().parents[2] / "ui"


def login_page() -> None:
    from views import login

    login()


def verify_page() -> None:
    from views import verify_signup

    verify_signup()


@pytest.fixture
def views(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(UI))
    import views

    return views


def test_signin_signup_form_sends_verification(views, monkeypatch: pytest.MonkeyPatch) -> None:
    post = Mock(return_value={"status": "sent"})
    monkeypatch.setattr(views, "public_post", post)
    app = AppTest.from_function(login_page).run()
    next(i for i in app.text_input if i.label == "Your email").input("signup@example.com")
    next(i for i in app.button if i.label == "Sign up").click().run()
    assert not app.exception and app.success
    post.assert_called_once_with("/v1/auth/signup", {"email": "signup@example.com"})


def test_verify_signup_password_mismatch_does_not_submit(views, monkeypatch: pytest.MonkeyPatch) -> None:
    post = Mock()
    monkeypatch.setattr(views, "public_post", post)
    app = AppTest.from_function(verify_page)
    app.session_state["link_token"] = "a" * 32
    app.run()
    app.text_input[0].input("Workspace")
    app.text_input[1].input("a-long-password-123")
    app.text_input[2].input("different-password")
    app.button[0].click().run()
    assert not app.exception and app.error[0].value == "Passwords don't match."
    post.assert_not_called()


def test_verify_signup_expired_link_is_actionable(views, monkeypatch: pytest.MonkeyPatch) -> None:
    from api import ApiError

    monkeypatch.setattr(views, "public_post", Mock(side_effect=ApiError(400, "This link is invalid or has expired")))
    app = AppTest.from_function(verify_page)
    app.session_state["link_token"] = "a" * 32
    app.run()
    app.text_input[0].input("Workspace")
    app.text_input[1].input("a-long-password-123")
    app.text_input[2].input("a-long-password-123")
    app.button[0].click().run()
    assert not app.exception and "expired" in app.error[0].value
