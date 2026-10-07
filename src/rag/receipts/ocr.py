"""Image normalisation and OCR (RapidOCR: PaddleOCR PP-OCR models on ONNX Runtime, CPU, bundled in the wheel)."""

import io
import re
from functools import cache
from typing import Any

from PIL import Image as PILImage
from PIL import ImageOps

from rag.adapters.llm.base import Image
from rag.core.config import get_settings

_LLM_MAX_EDGE = 2400
_PAN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def mask_card_numbers(text: str) -> str:
    """Masks anything that looks like a full card number (PAN) down to its last 4 digits."""

    def repl(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m[0])
        return f"************{digits[-4:]}" if _luhn(digits) else m[0]

    return _PAN.sub(repl, text)


def load_image(data: bytes) -> PILImage.Image:
    img = PILImage.open(io.BytesIO(data))  # lazy: reads the header only
    if img.width * img.height > get_settings().max_image_pixels:  # decompression-bomb guard
        raise ValueError("Image is too large")
    return ImageOps.exif_transpose(img).convert("RGB")  # phone photos are often stored rotated


def for_llm(img: PILImage.Image) -> Image:
    scale = min(1.0, _LLM_MAX_EDGE / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return Image(buf.getvalue(), "image/jpeg")


@cache
def _engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR()


def group_lines(boxes: list[list[list[float]]], texts: list[str]) -> list[str]:
    """Rebuilds printed lines from word boxes: receipts put the item and its price far apart on one row."""
    items = []
    for box, text in zip(boxes, texts, strict=True):
        ys = [p[1] for p in box]
        items.append((min(ys), max(ys), min(p[0] for p in box), text))
    items.sort(key=lambda t: (t[0] + t[1]) / 2)
    rows: list[list[tuple[float, float, float, str]]] = []
    for it in items:
        center = (it[0] + it[1]) / 2
        if rows:
            last = rows[-1]
            top, bottom = min(r[0] for r in last), max(r[1] for r in last)
            if top <= center <= bottom:
                last.append(it)
                continue
        rows.append([it])
    return ["  ".join(r[3] for r in sorted(row, key=lambda r: r[2])) for row in rows]


def ocr(img: PILImage.Image) -> str:
    import numpy as np

    result = _engine()(np.asarray(img)[:, :, ::-1])  # RGB → BGR
    if not result.txts:
        return ""
    boxes = [b.tolist() for b in result.boxes]
    return mask_card_numbers("\n".join(group_lines(boxes, list(result.txts))))


def render_pdf_page(page: Any, dpi: int = 200) -> PILImage.Image:
    pix = page.get_pixmap(dpi=dpi)
    return PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)


def llm_image(data: bytes, mime: str) -> Image:
    """The picture the vision model reads: the photo itself, or the first page of a PDF."""
    if mime == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            return for_llm(render_pdf_page(doc[0]))
    return for_llm(load_image(data))
