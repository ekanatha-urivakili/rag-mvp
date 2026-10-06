import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from rag.core.config import get_settings

POINT_NAMESPACE = uuid.UUID("6f1d3c1e-6b7a-4f0e-9d55-6a1c2b3d4e5f")
ALLOWED_FILTER_KEYS = frozenset({"doc_id"})


def point_id(document_id: uuid.UUID, version: int, chunk_index: int) -> str:
    # Deterministic: re-running an ingestion job overwrites points instead of duplicating them.
    return str(uuid.uuid5(POINT_NAMESPACE, f"{document_id}:{version}:{chunk_index}"))


@dataclass
class PointIn:
    id: str
    dense: list[float]
    sparse: qm.SparseVector
    payload: dict[str, Any]


@dataclass
class Hit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    """Qdrant adapter. Every read/write is scoped by tenant_id here, so no caller can forget it."""

    def __init__(self) -> None:
        s = get_settings()
        self._alias = s.qdrant_alias
        self._dim = s.embedding_dim
        self._client = AsyncQdrantClient(
            url=s.qdrant_url,
            api_key=s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None,
            timeout=s.qdrant_timeout_s,
        )

    @staticmethod
    def _tenant_filter(tenant_id: uuid.UUID, extra: dict[str, str] | None = None) -> qm.Filter:
        must: list[qm.Condition] = [qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(tenant_id)))]
        for k, v in (extra or {}).items():
            if k in ALLOWED_FILTER_KEYS:
                must.append(qm.FieldCondition(key=k, match=qm.MatchValue(value=v)))
        return qm.Filter(must=must)

    async def current_collection(self) -> str | None:
        aliases = await self._client.get_aliases()
        return next((a.collection_name for a in aliases.aliases if a.alias_name == self._alias), None)

    async def ensure_collection(self, version: int = 1) -> None:
        if await self.current_collection() is not None:
            return
        name = f"chunks_v{version}"
        await self.create_collection(name)
        await self.point_alias_to(name)

    async def create_collection(self, name: str) -> None:
        if not await self._client.collection_exists(name):
            await self._client.create_collection(
                collection_name=name,
                vectors_config={"dense": qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE)},
                sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
            )
            await self._client.create_payload_index(
                name,
                "tenant_id",
                qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD, is_tenant=True),
            )
            await self._client.create_payload_index(name, "doc_id", qm.PayloadSchemaType.KEYWORD)

    async def point_alias_to(self, name: str) -> None:
        ops: list[qm.AliasOperations] = []
        if await self.current_collection() is not None:
            ops.append(qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=self._alias)))
        ops.append(qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=name, alias_name=self._alias)))
        await self._client.update_collection_aliases(change_aliases_operations=ops)  # atomic swap

    async def drop_collection(self, name: str) -> None:
        await self._client.delete_collection(name)

    async def upsert(self, tenant_id: uuid.UUID, points: list[PointIn], collection: str | None = None) -> None:
        for p in points:
            p.payload["tenant_id"] = str(tenant_id)
        await self._client.upsert(
            collection_name=collection or self._alias,
            points=[
                qm.PointStruct(id=p.id, vector={"dense": p.dense, "sparse": p.sparse}, payload=p.payload)
                for p in points
            ],
            wait=True,
        )

    async def delete_document(self, tenant_id: uuid.UUID, doc_id: uuid.UUID, keep_version: int | None = None) -> None:
        f = self._tenant_filter(tenant_id, {"doc_id": str(doc_id)})
        if keep_version is not None:
            f.must_not = [qm.FieldCondition(key="doc_version", match=qm.MatchValue(value=keep_version))]
        await self._client.delete(collection_name=self._alias, points_selector=qm.FilterSelector(filter=f), wait=True)

    async def hybrid_search(
        self,
        tenant_id: uuid.UUID,
        dense: list[float],
        sparse: qm.SparseVector | None,
        limit: int,
        filters: dict[str, str] | None = None,
    ) -> list[Hit]:
        f = self._tenant_filter(tenant_id, filters)
        prefetch = [qm.Prefetch(query=dense, using="dense", limit=limit, filter=f)]
        if sparse is not None and sparse.indices:
            prefetch.append(qm.Prefetch(query=sparse, using="sparse", limit=limit, filter=f))
        resp = await self._client.query_points(
            collection_name=self._alias,
            prefetch=prefetch,
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            query_filter=f,
            limit=limit,
            with_payload=True,
        )
        return [Hit(id=str(p.id), score=p.score, payload=p.payload or {}) for p in resp.points]

    async def healthy(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False


@lru_cache
def get_vectorstore() -> VectorStore:
    return VectorStore()
