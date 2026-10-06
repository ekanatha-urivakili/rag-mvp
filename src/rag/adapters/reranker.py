import asyncio
from functools import lru_cache
from typing import Protocol

from fastembed.rerank.cross_encoder import TextCrossEncoder

from rag.core.config import get_settings


class Reranker(Protocol):
    async def score(self, query: str, documents: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model: str) -> None:
        self._model = TextCrossEncoder(model_name=model)

    async def score(self, query: str, documents: list[str]) -> list[float]:
        return await asyncio.to_thread(lambda: [float(s) for s in self._model.rerank(query, documents)])


@lru_cache
def get_reranker() -> Reranker:
    return CrossEncoderReranker(get_settings().reranker_model)
