"""Smoke-check the used FastEmbed paths and image loaders after a Pillow dependency override.

Run: uv run python scripts/check_model_compatibility.py
Downloads model files into the configured FastEmbed cache if they are missing.
"""

import asyncio
import math
from importlib.metadata import version
from io import BytesIO

from PIL import Image

from rag.adapters.embedder import SparseEmbedder
from rag.adapters.reranker import CrossEncoderReranker
from rag.ingestion.sniff import sniff_mime


async def check() -> None:
    sparse = SparseEmbedder("Qdrant/bm25")
    vectors = await sparse.embed_documents(["Annual leave is twenty days.", "Sick leave is ten days."])
    query = await sparse.embed_query("How many days of annual leave?")
    assert len(vectors) == 2 and query.indices

    reranker = CrossEncoderReranker("BAAI/bge-reranker-base")
    scores = await reranker.score(
        "How much annual leave?", ["Annual leave is twenty days.", "The office opens at nine."]
    )
    assert len(scores) == 2 and all(math.isfinite(x) for x in scores) and scores[0] > scores[1]

    for fmt in ("PNG", "JPEG", "WEBP"):
        buf = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buf, format=fmt)
        assert sniff_mime(buf.getvalue(), f"upload.{fmt.lower()}").startswith("image/")
        buf.seek(0)
        with Image.open(buf) as image:
            image.load()
            assert image.size == (8, 8)
    print(f"Pillow {version('Pillow')} / FastEmbed {version('fastembed')}: BM25, reranker and image checks passed")


if __name__ == "__main__":
    asyncio.run(check())
