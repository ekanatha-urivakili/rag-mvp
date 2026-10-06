from email.message import EmailMessage
from email.utils import make_msgid
from functools import lru_cache
from typing import Protocol

import aiosmtplib

from rag.core.config import get_settings


class Mailer(Protocol):
    async def send(self, *, to: str, subject: str, text: str, html: str, dedupe_key: str) -> None: ...


class SmtpMailer:
    async def send(self, *, to: str, subject: str, text: str, html: str, dedupe_key: str) -> None:
        s = get_settings()
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")  # header injection guard
        msg["Message-ID"] = make_msgid(idstring=dedupe_key)
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_user,
            password=s.smtp_password.get_secret_value() if s.smtp_password else None,
            start_tls=s.smtp_tls,
            timeout=s.smtp_timeout_s,
        )


@lru_cache
def get_mailer() -> Mailer:
    return SmtpMailer()
