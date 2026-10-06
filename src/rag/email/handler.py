from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from rag.adapters.mailer import get_mailer

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),  # user-controlled values (tenant names, titles) are escaped
    undefined=StrictUndefined,
)
# Plain-text bodies and subjects only; HTML always goes through the autoescaping env above.
_text_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=False,  # noqa: S701
    undefined=StrictUndefined,
)


async def send_email(job_id: str, payload: dict[str, Any]) -> None:
    template, ctx = payload["template"], payload["context"]
    await get_mailer().send(
        to=payload["to"],
        subject=_text_env.get_template(f"{template}.subject.txt").render(**ctx).strip(),
        text=_text_env.get_template(f"{template}.txt").render(**ctx),
        html=_env.get_template(f"{template}.html").render(**ctx),
        dedupe_key=job_id,
    )
