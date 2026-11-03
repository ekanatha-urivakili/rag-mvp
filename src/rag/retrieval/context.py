import uuid
from html import escape

from sqlalchemy import or_, select

from rag.adapters.vectorstore import point_id
from rag.db.models import Chunk, Document
from rag.db.session import get_sessionmaker
from rag.domain.models import ScoredChunk
from rag.ingestion.chunker import count_tokens


def build_context(chunks: list[ScoredChunk]) -> str:
    blocks = []
    for n, c in enumerate(chunks, start=1):
        page = f' page="{c.page}"' if c.page is not None else ""
        heading = f"{escape(c.heading_path, quote=True)}\n" if c.heading_path else ""
        blocks.append(
            f'<doc id="{n}" source="{escape(c.source, quote=True)}"{page}>\n'
            f"{heading}{escape(c.text, quote=True)}\n</doc>"
        )
    return "\n\n".join(blocks)


def select_context(chunks: list[ScoredChunk], max_tokens: int) -> list[ScoredChunk]:
    """Keep whole chunks in priority order; the budget includes escaped markup and headings."""
    selected: list[ScoredChunk] = []
    seen: set[tuple[str, int, str, str]] = set()
    for chunk in chunks:
        key = (chunk.doc_id, chunk.doc_version, chunk.heading_path, chunk.text)
        if not chunk.text.strip() or key in seen:
            continue
        seen.add(key)
        if count_tokens(build_context([*selected, chunk])) <= max_tokens:
            selected.append(chunk)
    return selected


async def neighboring_chunks(tenant_id: uuid.UUID, seeds: list[ScoredChunk], radius: int) -> list[ScoredChunk]:
    if not seeds or radius == 0:
        return []
    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                select(Chunk, Document.title)
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.status == "ready",
                    Document.version == Chunk.version,
                    or_(
                        *(
                            (Chunk.document_id == uuid.UUID(c.doc_id))
                            & (Chunk.version == c.doc_version)
                            & Chunk.chunk_index.between(c.chunk_index - radius, c.chunk_index + radius)
                            for c in seeds
                        )
                    ),
                )
            )
        ).all()
    found = {(str(c.document_id), c.version, c.chunk_index): (c, title) for c, title in rows}
    seen = {(c.doc_id, c.doc_version, c.chunk_index) for c in seeds}
    neighbors: list[ScoredChunk] = []
    for seed in seeds:
        for distance in range(1, radius + 1):
            for index in (seed.chunk_index - distance, seed.chunk_index + distance):
                key = (seed.doc_id, seed.doc_version, index)
                if key in seen or key not in found:
                    continue
                seen.add(key)
                c, title = found[key]
                neighbors.append(
                    ScoredChunk(
                        point_id=point_id(c.document_id, c.version, c.chunk_index),
                        doc_id=str(c.document_id),
                        doc_version=c.version,
                        chunk_index=c.chunk_index,
                        text=c.text,
                        heading_path=c.heading_path,
                        source=title,
                        page=c.page_start,
                        score=seed.score,
                    )
                )
    return neighbors
