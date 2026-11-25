"""LangGraph RAG agent.

Çağrı bütçesi:
  Fast    = 1 LLM çağrısı  (generate, streaming)
  Quality = 3 LLM çağrısı  (rewrite + generate + validate)
            + en fazla MAX_CORRECTION_ATTEMPTS x 2 (generate + validate), limit izin verirse
"""
import asyncio
import hashlib
import re
import time
from typing import Any, AsyncIterator, TypedDict

from langgraph.graph import END, StateGraph

from config import settings
from services.rate_limiter import RateLimitError


class NotIndexedError(Exception):
    pass


class AgentState(TypedDict, total=False):
    query: str
    max_sentences: int
    history: list[dict]
    ticket: Any
    queries: list[str]
    docs: list[dict]
    answer: str
    validated: bool
    needs_retry: bool
    reason: str
    attempts: int
    corrected: bool
    warnings: list[str]


_CITE_STRIP_RE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def strip_citations(text: str) -> str:
    """Önceki cevaplardaki [1], [1, 2, 4] etiketlerini temizler (yeni atıflarla karışmasın)."""
    return re.sub(r"\s+", " ", _CITE_STRIP_RE.sub("", text or "")).strip()


def format_history(history: list[dict] | None, max_chars: int = 1600) -> str:
    if not history:
        return ""
    lines = []
    for m in history[-8:]:
        role = "User" if m.get("role") == "user" else "Assistant"
        body = strip_citations(m.get("content", "")) if role == "Assistant" else (m.get("content") or "").strip()
        if body:
            lines.append(f"{role}: {body[:400]}")
    out, total = [], 0
    for line in reversed(lines):
        if total + len(line) > max_chars and out:
            break
        out.append(line)
        total += len(line)
    return "\n".join(reversed(out))


REWRITE_PROMPT = (
    "Write {n} alternative search queries for the question below. Use different wording and "
    "keywords likely to appear in the source documents. If the question is not in English, "
    "include queries in both the question's language and English to match multilingual documents. "
    "{hist_note}"
    'Return JSON: {{"queries": ["...", "..."]}}\n\n'
    "{hist_block}Question: {query}"
)

ANSWER_SYSTEM = (
    "You are a careful assistant. Answer ONLY using the provided CONTEXT. "
    "Cite sources inline like [1], [2]. If CONVERSATION HISTORY is provided, use it ONLY to "
    "understand follow-up references (such as pronouns); base your answer and citations strictly "
    "on the current CONTEXT. If the CONTEXT does not contain the answer, say you could "
    "not find it in the documents. Reply in the same language as the question. "
    "Use at most {max_sentences} sentences."
)

VALIDATE_PROMPT = (
    "Decide whether every claim in the ANSWER is supported by the CONTEXT.\n"
    'Return JSON: {{"supported": true|false, "reason": "short explanation"}}\n\n'
    "CONTEXT:\n{context}\n\nANSWER:\n{answer}"
)


def build_context(docs: list[dict]) -> str:
    parts, total = [], 0
    for i, d in enumerate(docs, start=1):
        m = d["metadata"]
        text = d.get("context") or d["content"]
        block = f"[{i}] ({m.get('source')}, p.{m.get('page')})\n{text}"
        if total + len(block) > settings.max_context_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def format_sources(docs: list[dict]) -> list[dict]:
    return [{"content": d["content"], "metadata": d["metadata"],
             "relevance_score": round(d.get("score", 0.0), 4)} for d in docs]


def answer_prompt(query: str, docs: list[dict], feedback: str | None = None,
                  history: list[dict] | None = None) -> str:
    hist = format_history(history)
    hist_block = f"CONVERSATION HISTORY:\n{hist}\n\n" if hist else ""
    extra = (f"\nA previous draft was judged partly unsupported ({feedback}). "
             "Stick strictly to the context.\n") if feedback else ""
    return f"{hist_block}CONTEXT:\n{build_context(docs)}\n{extra}\nQUESTION: {query}"


class FastRun:
    """Fast mod: kaynaklar hemen hazır, token'lar akış olarak gelir."""

    def __init__(self, sources: list[dict], tokens: AsyncIterator[str], t0: float):
        self.sources, self.tokens, self._t0 = sources, tokens, t0

    def metadata(self) -> dict:
        return {"query_time": round(time.perf_counter() - self._t0, 2), "num_sources": len(self.sources),
                "mode": "fast", "llm_calls": 1}


class RAGAgent:
    def __init__(self, llm, retrieval, budget, cache):
        self.llm, self.retrieval, self.budget, self.cache = llm, retrieval, budget, cache
        self.graph = self._build_graph()

    # ---------- Quality (LangGraph) ----------
    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("rewrite", self._rewrite)
        g.add_node("retrieve", self._retrieve)
        g.add_node("generate", self._generate)
        g.add_node("validate", self._validate)
        g.set_entry_point("rewrite")
        g.add_edge("rewrite", "retrieve")
        g.add_edge("retrieve", "generate")
        g.add_edge("generate", "validate")
        g.add_conditional_edges("validate", lambda s: "generate" if s.get("needs_retry") else END,
                                {"generate": "generate", END: END})
        return g.compile()

    async def _rewrite(self, s: AgentState) -> dict:
        n = settings.num_query_variations
        hist = format_history(s.get("history"))
        hist_note = ("Resolve any pronouns or follow-up references using CONVERSATION HISTORY; "
                     "if the question changes topic, ignore the history. ") if hist else ""
        hist_block = f"CONVERSATION HISTORY:\n{hist}\n\n" if hist else ""
        data = await self.llm.generate_json(
            REWRITE_PROMPT.format(n=n, hist_note=hist_note, hist_block=hist_block, query=s["query"]),
            s["ticket"])
        qs = [s["query"]]
        for q in (data or {}).get("queries", []) if isinstance(data, dict) else []:
            if isinstance(q, str) and q.strip() and q.strip() not in qs:
                qs.append(q.strip())
        return {"queries": qs[: 1 + n]}

    async def _retrieve(self, s: AgentState) -> dict:
        docs = await asyncio.to_thread(self.retrieval.hybrid, s["queries"], s["query"])
        return {"docs": docs}

    async def _generate(self, s: AgentState) -> dict:
        feedback = s.get("reason") if s.get("needs_retry") else None
        ans = await self.llm.generate(
            answer_prompt(s["query"], s["docs"], feedback, history=s.get("history")), s["ticket"],
            system=ANSWER_SYSTEM.format(max_sentences=s["max_sentences"]))
        return {"answer": ans.strip(), "needs_retry": False}

    async def _validate(self, s: AgentState) -> dict:
        data = await self.llm.generate_json(
            VALIDATE_PROMPT.format(context=build_context(s["docs"]), answer=s["answer"]), s["ticket"])
        if not isinstance(data, dict) or "supported" not in data:
            return {"validated": True, "needs_retry": False,
                    "warnings": s.get("warnings", []) + ["validator_unparseable"]}
        if data["supported"]:
            return {"validated": True, "needs_retry": False}
        attempts = s.get("attempts", 0)
        warn = s.get("warnings", [])
        if attempts >= settings.max_correction_attempts:
            return {"validated": False, "needs_retry": False, "warnings": warn + ["answer_unverified"]}
        try:
            s["ticket"].top_up(2)  # generate + validate
        except RateLimitError:
            return {"validated": False, "needs_retry": False,
                    "warnings": warn + ["answer_unverified", "retry_skipped_rate_limit"]}
        return {"validated": False, "needs_retry": True, "attempts": attempts + 1, "corrected": True,
                "reason": str(data.get("reason", ""))[:300]}

    def _require_index(self) -> None:
        if self.retrieval.store.count() == 0:
            raise NotIndexedError("Veritabanı boş. Önce doküman yükleyip indeksle.")

    @staticmethod
    def _effective_history(query: str, history: list[dict] | None) -> list[dict]:
        if not history:
            return []
        last_user = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
        if last_user.strip().lower() == query.strip().lower():
            return []  # aynı sorunun birebir tekrarı: geçmişi ekleme, cache çalışsın
        return history

    async def run_quality(self, query: str, max_sentences: int, history: list[dict] | None = None) -> dict:
        t0 = time.perf_counter()
        self._require_index()
        eff_hist = self._effective_history(query, history)
        hist_sig = format_history(eff_hist)
        key = "q:" + hashlib.sha256(f"{query.strip().lower()}|{max_sentences}|{hist_sig}".encode()).hexdigest()
        cached = await self.cache.get(key)
        if cached:
            cached["metadata"].update(cache_hit=True, llm_calls=0,
                                      query_time=round(time.perf_counter() - t0, 2))
            return cached

        ticket = self.budget.reserve(3)  # limitte burada RateLimitError, hiçbir çağrı yapılmadan
        s = await self.graph.ainvoke({"query": query, "max_sentences": max_sentences,
                                      "history": eff_hist, "ticket": ticket, "attempts": 0})
        result = {
            "answer": s["answer"],
            "sources": format_sources(s["docs"]),
            "metadata": {"query_time": round(time.perf_counter() - t0, 2), "num_sources": len(s["docs"]),
                         "mode": "quality", "llm_calls": ticket.used, "cache_hit": False,
                         "correction_applied": bool(s.get("corrected")),
                         "validated": bool(s.get("validated")), "warnings": s.get("warnings", [])},
        }
        if result["metadata"]["validated"]:  # doğrulanmamış cevabı cache'leme
            await self.cache.set(key, result)
        return result

    # ---------- Fast ----------
    async def prepare_fast(self, query: str, max_sentences: int, history: list[dict] | None = None) -> FastRun:
        t0 = time.perf_counter()
        self._require_index()
        ticket = self.budget.reserve(1)
        eff_hist = self._effective_history(query, history)
        queries = [query]
        if eff_hist and len(query.split()) <= 6:
            last_user = next((m.get("content", "") for m in reversed(eff_hist) if m.get("role") == "user"), "")
            if last_user.strip():
                queries.append(f"{last_user.strip()} {query}")
        docs = await asyncio.to_thread(self.retrieval.hybrid, queries, query)
        stream = self.llm.stream(answer_prompt(query, docs, history=eff_hist), ticket,
                                 system=ANSWER_SYSTEM.format(max_sentences=max_sentences))
        return FastRun(format_sources(docs), stream, t0)

    async def run_fast(self, query: str, max_sentences: int, history: list[dict] | None = None) -> dict:
        run = await self.prepare_fast(query, max_sentences, history=history)
        answer = "".join([t async for t in run.tokens]).strip()
        return {"answer": answer, "sources": run.sources, "metadata": run.metadata()}
