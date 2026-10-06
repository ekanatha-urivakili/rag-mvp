import asyncio
from functools import lru_cache
from typing import Protocol

import ollama
import openai
from fastembed import SparseTextEmbedding
from qdrant_client import models as qm
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from rag.core.config import get_settings

_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.InternalServerError,
    ollama.ResponseError,
    ConnectionError,
    TimeoutError,
)


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, model: str, api_key: str | None) -> None:
        self.model = model
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=10, max_retries=0)

    @retry(retry=retry_if_exception_type(_RETRYABLE), stop=stop_after_attempt(4), wait=wait_exponential_jitter(1, 20))
    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


class OllamaEmbedder:
    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self._client = ollama.AsyncClient(host=host, timeout=60)

    @retry(retry=retry_if_exception_type(_RETRYABLE), stop=stop_after_attempt(4), wait=wait_exponential_jitter(1, 20))
    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embed(model=self.model, input=texts)
        return [list(v) for v in resp.embeddings]


class SparseEmbedder:
    """BM25 term weights computed locally on CPU; IDF is applied by Qdrant."""

    def __init__(self, model: str) -> None:
        self._model = SparseTextEmbedding(model_name=model)

    async def embed_documents(self, texts: list[str]) -> list[qm.SparseVector]:
        def _run() -> list[qm.SparseVector]:
            return [
                qm.SparseVector(indices=e.indices.tolist(), values=e.values.tolist()) for e in self._model.embed(texts)
            ]

        return await asyncio.to_thread(_run)

    async def embed_query(self, text: str) -> qm.SparseVector:
        def _run() -> qm.SparseVector:
            e = next(iter(self._model.query_embed(text)))
            return qm.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())

        return await asyncio.to_thread(_run)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "openai":
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
        return OpenAIEmbedder(s.embedding_model, key)
    return OllamaEmbedder(s.embedding_model, s.ollama_base_url)


@lru_cache
def get_sparse_embedder() -> SparseEmbedder:
    return SparseEmbedder(get_settings().sparse_model)
