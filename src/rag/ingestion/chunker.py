import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from rag.ingestion.parsers import Page

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@lru_cache
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc().encode(text, disallowed_special=()))


@dataclass
class ChunkOut:
    index: int
    text: str
    heading_path: str
    page_start: int | None
    page_end: int | None
    token_count: int

    @property
    def embed_text(self) -> str:
        return f"{self.heading_path}\n\n{self.text}" if self.heading_path else self.text


@dataclass
class _Section:
    heading_path: str
    paragraphs: list[tuple[str, int | None]]


def _sections(pages: list[Page]) -> list[_Section]:
    stack: list[tuple[int, str]] = []
    sections: list[_Section] = [_Section("", [])]
    for page in pages:
        para: list[str] = []

        def flush(para: list[str] = para, page_no: int | None = page.number) -> None:
            if para:
                sections[-1].paragraphs.append(("\n".join(para).strip(), page_no))
                para.clear()

        for line in page.markdown.splitlines():
            m = _HEADING.match(line)
            if m:
                flush()
                level, title = len(m[1]), m[2].strip().strip("*_ ")
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                sections.append(_Section(" > ".join(t for _, t in stack), []))
            elif not line.strip():
                flush()
            else:
                para.append(line)
        flush()
    return [s for s in sections if s.paragraphs]


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    tokens = _enc().encode(text, disallowed_special=())
    step = max(size - overlap, 1)
    return [_enc().decode(tokens[i : i + size]) for i in range(0, len(tokens), step)]


def chunk_pages(pages: list[Page], size: int = 500, overlap: int = 50) -> list[ChunkOut]:
    """Structure-aware: split on headings first, then pack paragraphs up to `size` tokens with overlap."""
    out: list[ChunkOut] = []

    def emit(parts: list[tuple[str, int | None]], heading: str) -> None:
        text = "\n\n".join(p for p, _ in parts).strip()
        if not text:
            return
        pages_ = [pg for _, pg in parts if pg is not None]
        out.append(
            ChunkOut(
                index=len(out),
                text=text,
                heading_path=heading,
                page_start=min(pages_) if pages_ else None,
                page_end=max(pages_) if pages_ else None,
                token_count=count_tokens(text),
            )
        )

    for section in _sections(pages):
        buf: list[tuple[str, int | None]] = []
        buf_tokens = 0
        for text, page_no in section.paragraphs:
            n = count_tokens(text)
            if n > size:
                emit(buf, section.heading_path)
                buf, buf_tokens = [], 0
                for piece in _hard_split(text, size, overlap):
                    emit([(piece, page_no)], section.heading_path)
                continue
            if buf_tokens + n > size and buf:
                emit(buf, section.heading_path)
                tail = buf[-1]
                # Carry the last paragraph forward as overlap when it's small enough.
                buf = [tail] if count_tokens(tail[0]) <= overlap else []
                buf_tokens = sum(count_tokens(p) for p, _ in buf)
            buf.append((text, page_no))
            buf_tokens += n
        emit(buf, section.heading_path)
    return out
