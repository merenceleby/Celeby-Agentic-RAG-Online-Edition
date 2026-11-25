"""Lite Eval: LLM çağrısı harcamayan değerlendirme.

1) Retrieval: sorular + beklenen kaynak/sayfa dosyasıyla Hit@k ve MRR (vektör vs hibrit).
2) Cevap kontrolleri: kayıtlı cevaplar üzerinde yerel embedding ile "dayanak" (grounding),
   soru-cevap ilgisi ve atıf geçerliliği. Bunlar PROXY metriktir, LLM-judge değildir.
3) Test sorusu üretimi (opsiyonel): bir kereliğine LLM kullanır, kotadan düşer.
"""
import asyncio
import json
import random
import re
import time
from statistics import mean
from typing import Callable, Optional

from config import settings
from services.db import connect

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
CITE = re.compile(r"\[(\d+)\]")
ABSTAIN = re.compile(r"could(?: not|n't) find|cannot find|can't find|not (?:mentioned|covered|contain)"
                     r"|bulamad|bulunamad|yer almıyor", re.I)

GEN_PROMPT = (
    "Write ONE question that can be answered using only the passage below. The question must make "
    "sense on its own and must not mention 'the passage' or 'the text'.\n"
    'Return JSON: {{"question": "..."}}\n\nPASSAGE:\n{passage}'
)


# ---------- Test soruları ----------
def normalize_question(item) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    q, src = str(item.get("question", "")).strip(), str(item.get("source", "")).strip()
    if not q or not src:
        return None
    pages = item.get("pages", item.get("page", []))
    if isinstance(pages, (int, str)):
        pages = [pages]
    pages = [int(p) for p in pages if str(p).isdigit()]
    return {"question": q, "source": src, "pages": pages, "generated": bool(item.get("generated", False))}


def load_questions() -> list[dict]:
    path = settings.eval_dir / "questions.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [q for q in (normalize_question(x) for x in data) if q] if isinstance(data, list) else []


def save_questions(questions: list[dict]) -> None:
    settings.eval_dir.mkdir(parents=True, exist_ok=True)
    (settings.eval_dir / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")


async def generate_questions(llm, budget, store, n: int) -> dict:
    """Rastgele chunk'lardan n soru üretir (n çağrı harcar, dakikalık limite göre kısıtlanır)."""
    n = max(1, min(n, 20, budget.rpm))
    docs = await asyncio.to_thread(store.all_documents)
    pool = [d for d in docs if len(d["content"]) >= 300] or docs
    sample = random.sample(pool, min(n, len(pool)))
    if not sample:
        return {"added": 0, "total": len(load_questions()), "requested": n}
    ticket = budget.reserve(len(sample))  # limitteyse RateLimitError, hiç çağrı yapılmadan

    async def one(d):
        data = await llm.generate_json(GEN_PROMPT.format(passage=d["content"][:1500]), ticket)
        q = str(data.get("question", "")).strip() if isinstance(data, dict) else ""
        if len(q) < 8:
            return None
        return {"question": q, "source": d["metadata"]["source"], "pages": [d["metadata"]["page"]],
                "generated": True}

    results = await asyncio.gather(*[one(d) for d in sample])
    existing = load_questions()
    seen = {x["question"].lower() for x in existing}
    new = []
    for r in results:  # hem dosyadakilerle hem aynı partideki tekrarlarla karşılaştır
        if r and r["question"].lower() not in seen:
            seen.add(r["question"].lower())
            new.append(r)
    save_questions(existing + new)
    return {"added": len(new), "total": len(existing) + len(new), "requested": len(sample)}


# ---------- Retrieval değerlendirmesi ----------
def hit_rank(docs: list[dict], item: dict) -> Optional[int]:
    for rank, d in enumerate(docs, start=1):
        m = d["metadata"]
        if m.get("source") == item["source"] and (not item["pages"] or m.get("page") in item["pages"]):
            return rank
    return None


def summarize_ranks(ranks: list[Optional[int]], latencies: list[float], k: int) -> dict:
    n = len(ranks)
    pct = lambda kk: round(100 * sum(1 for r in ranks if r and r <= kk) / n, 1) if kk <= k else None  # noqa: E731
    return {"hit_at_1": pct(1), "hit_at_3": pct(3), "hit_at_5": pct(5),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "avg_ms": round(1000 * mean(latencies))}


def run_retrieval_eval(retrieval, questions: list[dict]) -> dict:
    k = settings.top_k_rerank
    pipelines: dict[str, Callable[[str], list[dict]]] = {
        "vector": lambda q: retrieval.vector_only(q),
        "hybrid": lambda q: retrieval.hybrid([q], q),  # sorgu yeniden yazma yok (LLM gerektirir)
    }
    out: dict = {"kind": "retrieval", "questions": len(questions), "k": k}
    for name, fn in pipelines.items():
        ranks, lats, missed = [], [], []
        for item in questions:
            t0 = time.perf_counter()
            docs = fn(item["question"])
            lats.append(time.perf_counter() - t0)
            r = hit_rank(docs, item)
            ranks.append(r)
            if r is None:
                missed.append({"question": item["question"], "source": item["source"], "pages": item["pages"]})
        out[name] = summarize_ranks(ranks, lats, k)
        if name == "hybrid":
            out["missed"] = missed[:15]
    return out


# ---------- Cevap kontrolleri ----------
def split_sentences(answer: str) -> list[str]:
    text = CITE.sub("", answer)
    return [s.strip() for s in SENT_SPLIT.split(text) if len(s.split()) >= 4]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def check_answer(embed: Callable[[list[str]], list[list[float]]], question: str, answer: str,
                 sources: list[dict]) -> Optional[dict]:
    texts = [s["content"] for s in sources]
    if not texts:
        return None
    cites = [int(x) for x in CITE.findall(answer)]
    citation_valid = None if not cites else all(1 <= c <= len(sources) for c in cites)
    sentences = split_sentences(answer)
    if ABSTAIN.search(answer) or not sentences:
        return {"abstained": True, "citation_valid": citation_valid}
    vecs = embed([question or answer, CITE.sub("", answer)] + sentences + texts)
    q, a = vecs[0], vecs[1]
    sv, tv = vecs[2:2 + len(sentences)], vecs[2 + len(sentences):]
    sims = [max(_dot(s, t) for t in tv) for s in sv]
    thr = settings.eval_support_threshold
    return {"abstained": False, "grounding": sum(1 for x in sims if x >= thr) / len(sims),
            "support": mean(sims), "relevancy": max(0.0, min(1.0, _dot(q, a))),
            "citation_valid": citation_valid}


def run_answer_checks(embed, answers: list[dict]) -> dict:
    rows = []
    for a in answers:
        r = check_answer(embed, a["question"], a["answer"], a["sources"])
        if r:
            rows.append({**r, "question": a["question"], "message_id": a["message_id"]})
    scored = [r for r in rows if not r["abstained"]]
    cited = [r for r in rows if r.get("citation_valid") is not None]
    pct = lambda x: round(100 * x, 1)  # noqa: E731
    worst = sorted(scored, key=lambda r: r["grounding"])[:10]
    return {
        "kind": "answers", "answers": len(rows), "scored": len(scored), "abstained": len(rows) - len(scored),
        "grounding": pct(mean(r["grounding"] for r in scored)) if scored else None,
        "relevancy": pct(mean(r["relevancy"] for r in scored)) if scored else None,
        "citation_valid": pct(sum(1 for r in cited if r["citation_valid"]) / len(cited)) if cited else None,
        "threshold": settings.eval_support_threshold,
        "worst": [{"question": r["question"], "grounding": pct(r["grounding"]), "relevancy": pct(r["relevancy"])}
                  for r in worst],
    }


# ---------- Sonuçları sakla ----------
def save_run(kind: str, data: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO eval_runs(ts,kind,data) VALUES(?,?,?)", (time.time(), kind, json.dumps(data)))


def latest_runs() -> dict:
    out = {}
    with connect() as c:
        for kind in ("retrieval", "answers"):
            row = c.execute("SELECT ts,data FROM eval_runs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
            out[kind] = {**json.loads(row["data"]), "ts": row["ts"]} if row else None
    return out
