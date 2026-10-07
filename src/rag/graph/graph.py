import operator
import re
import time
import uuid
from functools import cache, lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from rag.adapters.llm.base import Message, RouteEntry, Usage
from rag.adapters.llm.router import FallbackLLM, get_router
from rag.core.config import get_settings
from rag.domain.models import ChatTurn, Citation, ModelUsage, ScoredChunk
from rag.retrieval.retriever import retrieve

PROMPT_VERSION = "v1"
_PROMPTS = Path(__file__).parent / "prompts"
_CITATION = re.compile(r"\[(\d+)\]")
INSUFFICIENT = "I couldn't find this in your documents. Try rephrasing, or upload a document that covers this topic."


@cache
def prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.{PROMPT_VERSION}.txt").read_text().strip()


class RAGState(TypedDict, total=False):
    tenant_id: str
    user_id: str
    conversation_id: str
    question: str
    history: list[ChatTurn]
    filters: dict[str, str]
    standalone_query: str
    needs_retrieval: bool
    chunks: list[ScoredChunk]
    reranked: bool
    attempts: int
    path: Annotated[list[str], operator.add]
    answer: str
    citations: list[Citation]
    models_used: Annotated[list[ModelUsage], operator.add]


class QueryAnalysis(BaseModel):
    standalone_query: str = Field(max_length=2000)
    needs_retrieval: bool


class RewrittenQuery(BaseModel):
    query: str = Field(max_length=2000)


def _usage(purpose: str, provider: str, model: str, usage: Usage | None, ms: int, fb: bool) -> ModelUsage:
    u = usage or Usage()
    return ModelUsage(
        purpose=purpose,
        provider=provider,
        model=model,
        tokens_in=u.tokens_in,
        tokens_out=u.tokens_out,
        latency_ms=ms,
        fallback_used=fb,
    )


def _history_block(history: list[ChatTurn]) -> str:
    return "\n".join(f"{t.role.upper()}: {t.content[:1500]}" for t in history) or "(no previous messages)"


def _neutralize(text: str) -> str:
    # Stop retrieved text from closing/opening our delimiters (prompt-injection hardening).
    return re.sub(r"(?i)</?\s*doc\b", lambda m: m[0].replace("<", "&lt;"), text)


def build_context(chunks: list[ScoredChunk]) -> str:
    blocks = []
    for n, c in enumerate(chunks, start=1):
        page = f' page="{c.page}"' if c.page is not None else ""
        source = _neutralize(c.source).replace('"', "'")
        heading = f"{_neutralize(c.heading_path)}\n" if c.heading_path else ""
        blocks.append(f'<doc id="{n}" source="{source}"{page}>\n{heading}{_neutralize(c.text)}\n</doc>')
    return "\n\n".join(blocks)


def _llm(purpose: str) -> FallbackLLM:
    """Route for `purpose` that tells the client which model it is trying (fallbacks included)."""
    write = get_stream_writer()

    async def announce(entry: RouteEntry) -> None:
        write({"type": "model", "purpose": purpose, "provider": entry.provider, "model": entry.model})

    return get_router().for_purpose(purpose, on_attempt=announce)


# --- Nodes -----------------------------------------------------------------------


async def analyze_query(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "status", "step": "analyzing"})
    llm = _llm("orchestration")
    user = f"Conversation:\n{_history_block(state.get('history', []))}\n\nLatest message:\n{state['question']}"
    res = await llm.complete(
        [Message("system", prompt("analyze_query")), Message("user", user)], schema=QueryAnalysis, max_tokens=300
    )
    assert isinstance(res.parsed, QueryAnalysis)
    return {
        "standalone_query": res.parsed.standalone_query.strip() or state["question"],
        "needs_retrieval": res.parsed.needs_retrieval,
        "attempts": 0,
        "path": ["analyze_query"],
        "models_used": [_usage("orchestration", res.provider, res.model, res.usage, res.latency_ms, res.fallback_used)],
    }


async def _stream_answer(purpose: str, messages: list[Message], max_tokens: int) -> tuple[str, ModelUsage]:
    write = get_stream_writer()
    parts: list[str] = []
    provider = model = ""
    fallback = False
    usage: Usage | None = None
    t0 = time.perf_counter()
    async for ev in _llm(purpose).stream(messages, max_tokens=max_tokens):
        provider, model, fallback = ev.provider or provider, ev.model or model, ev.fallback_used or fallback
        if ev.text:
            parts.append(ev.text)
            write({"type": "token", "text": ev.text})
        if ev.usage:
            usage = ev.usage
    ms = int((time.perf_counter() - t0) * 1000)
    return "".join(parts), _usage(purpose, provider, model, usage, ms, fallback)


async def respond_direct(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "status", "step": "responding"})
    messages = [Message("system", prompt("respond_direct"))]
    messages += [Message(t.role, t.content) for t in state.get("history", [])[-4:]]
    messages.append(Message("user", state["question"]))
    text, usage = await _stream_answer("orchestration", messages, 400)
    return {"answer": text, "citations": [], "path": ["respond_direct"], "models_used": [usage]}


async def retrieve_node(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "status", "step": "retrieving"})
    result = await retrieve(uuid.UUID(state["tenant_id"]), state["standalone_query"], state.get("filters"))
    return {"chunks": result.chunks, "reranked": result.reranked, "path": ["retrieve"]}


def grade(state: RAGState) -> Literal["generate", "rewrite_query", "insufficient_context"]:
    chunks = state.get("chunks", [])
    good = bool(chunks) and (not state.get("reranked") or chunks[0].score >= get_settings().grade_threshold)
    if good:
        return "generate"
    return "rewrite_query" if state.get("attempts", 0) < 1 else "insufficient_context"


async def rewrite_query(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "status", "step": "rewriting"})
    llm = _llm("orchestration")
    res = await llm.complete(
        [Message("system", prompt("rewrite_query")), Message("user", state["standalone_query"])],
        schema=RewrittenQuery,
        max_tokens=200,
    )
    assert isinstance(res.parsed, RewrittenQuery)
    return {
        "standalone_query": res.parsed.query.strip() or state["standalone_query"],
        "attempts": state.get("attempts", 0) + 1,
        "path": ["rewrite_query"],
        "models_used": [_usage("orchestration", res.provider, res.model, res.usage, res.latency_ms, res.fallback_used)],
    }


async def generate(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "status", "step": "generating"})
    user = (
        f"Documents:\n{build_context(state['chunks'])}\n\n"
        f"Question: {state['question']}\n"
        f"(Interpreted as: {state['standalone_query']})"
    )
    messages = [Message("system", prompt("generate")), Message("user", user)]
    text, usage = await _stream_answer("generation", messages, get_settings().generation_max_tokens)
    return {"answer": text, "path": ["generate"], "models_used": [usage]}


def validate_citations(state: RAGState) -> dict[str, Any]:
    answer, chunks = state["answer"], state["chunks"]
    valid = {n for n in (int(m) for m in _CITATION.findall(answer)) if 1 <= n <= len(chunks)}
    cleaned = _CITATION.sub(lambda m: m[0] if int(m[1]) in valid else "", answer)
    citations = [
        Citation(
            n=n,
            doc_id=chunks[n - 1].doc_id,
            source=chunks[n - 1].source,
            page=chunks[n - 1].page,
            snippet=chunks[n - 1].text[:300],
        )
        for n in sorted(valid)
    ]
    if not valid:
        cleaned = INSUFFICIENT
    get_stream_writer()({"type": "answer", "text": cleaned})
    get_stream_writer()({"type": "citations", "citations": [c.model_dump() for c in citations]})
    return {"answer": cleaned, "citations": citations, "path": ["validate_citations"]}


def insufficient_context(state: RAGState) -> dict[str, Any]:
    get_stream_writer()({"type": "token", "text": INSUFFICIENT})
    return {"answer": INSUFFICIENT, "citations": [], "path": ["insufficient_context"]}


@lru_cache
def build_graph() -> CompiledStateGraph[RAGState, None, RAGState, RAGState]:
    g = StateGraph(RAGState)
    g.add_node("analyze_query", analyze_query)
    g.add_node("respond_direct", respond_direct)
    g.add_node("retrieve", retrieve_node)
    g.add_node("rewrite_query", rewrite_query)
    g.add_node("generate", generate)
    g.add_node("validate_citations", validate_citations)
    g.add_node("insufficient_context", insufficient_context)

    g.add_edge(START, "analyze_query")
    g.add_conditional_edges("analyze_query", lambda s: "retrieve" if s["needs_retrieval"] else "respond_direct")
    g.add_conditional_edges("retrieve", grade)
    g.add_edge("rewrite_query", "retrieve")
    g.add_edge("generate", "validate_citations")
    g.add_edge("validate_citations", END)
    g.add_edge("respond_direct", END)
    g.add_edge("insufficient_context", END)
    return g.compile()
