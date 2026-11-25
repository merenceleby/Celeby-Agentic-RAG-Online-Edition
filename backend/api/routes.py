import asyncio
import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from services import evaluation
from services.agent import NotIndexedError
from services.llm import LLMError
from services.rate_limiter import RateLimitError

router = APIRouter(prefix="/api")
EXPECTED = (RateLimitError, LLMError, NotIndexedError)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["quality", "fast"] = "quality"
    max_sentences: int = Field(6, ge=1, le=15)
    chat_id: Optional[int] = None


class FeedbackRequest(BaseModel):
    message_id: int
    rating: Literal[1, -1]
    comment: Optional[str] = Field(None, max_length=1000)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _fail(st, mode: str, query: str, e: Exception) -> None:
    if isinstance(e, RateLimitError):
        kind = f"rate_limit_{e.kind}"
    else:
        kind = "not_indexed" if isinstance(e, NotIndexedError) else "llm_error"
    st.metrics.record(mode, ok=False, error_kind=kind, query=query)


def _begin(st, chat_id: Optional[int], query: str) -> int:
    """Sohbeti aç (ya da mevcut olanı doğrula) ve kullanıcı mesajını kaydet."""
    if chat_id is None:
        chat_id = st.history.create_chat(query)
    elif not st.history.exists(chat_id):
        raise HTTPException(404, "Chat not found")
    st.history.add_message(chat_id, "user", query)
    return chat_id


def _recent_history(st, chat_id: Optional[int], limit: int = 8) -> list[dict]:
    """Aynı sohbetin son mesajlarını döndürür (diğer sohbetlerden tamamen izole)."""
    if chat_id is None:
        return []
    chat = st.history.get_chat(chat_id)
    if not chat:
        return []
    return [{"role": m["role"], "content": m["content"]} for m in chat["messages"][-limit:]]


# ---------- Sorgu ----------
@router.post("/query")
async def query(req: QueryRequest, request: Request):
    st = request.app.state
    run = st.agent.run_fast if req.mode == "fast" else st.agent.run_quality
    history = _recent_history(st, req.chat_id)
    try:
        result = await run(req.query, req.max_sentences, history=history)
    except EXPECTED as e:
        _fail(st, req.mode, req.query, e)
        raise
    # Kullanıcı mesajı yalnızca başarılı sorguda kaydedilir (limitte yetim mesaj kalmaz).
    chat_id = _begin(st, req.chat_id, req.query)
    mid = st.history.add_message(chat_id, "assistant", result["answer"], result["sources"], result["metadata"])
    result["metadata"].update(chat_id=chat_id, message_id=mid)
    st.metrics.record(req.mode, meta=result["metadata"], query=req.query)
    return result


@router.post("/query-stream")
async def query_stream(req: QueryRequest, request: Request):
    st = request.app.state
    history = _recent_history(st, req.chat_id)
    try:  # Rezervasyon stream başlamadan yapılır: limitte gerçek HTTP 429 döner.
        run = await st.agent.prepare_fast(req.query, req.max_sentences, history=history)
    except EXPECTED as e:
        _fail(st, "fast", req.query, e)
        raise
    chat_id = _begin(st, req.chat_id, req.query)

    async def events():
        yield _sse("chat", {"chat_id": chat_id})
        yield _sse("sources", run.sources)
        parts: list[str] = []
        try:
            async for tok in run.tokens:
                parts.append(tok)
                yield _sse("token", {"text": tok})
        except (RateLimitError, LLMError) as e:
            _fail(st, "fast", req.query, e)
            payload = {"message": getattr(e, "message", str(e))}
            if isinstance(e, RateLimitError):
                payload.update(kind=e.kind, retry_after=e.retry_after)
            yield _sse("error", payload)
            return
        meta = run.metadata()
        mid = st.history.add_message(chat_id, "assistant", "".join(parts).strip(), run.sources, meta)
        meta.update(chat_id=chat_id, message_id=mid, cache_hit=False)
        st.metrics.record("fast", meta=meta, query=req.query)
        yield _sse("done", meta)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- Sohbet geçmişi ----------
@router.get("/chats")
async def list_chats(request: Request):
    return request.app.state.history.list_chats()


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: int, request: Request):
    chat = request.app.state.history.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    return chat


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: int, request: Request):
    request.app.state.history.delete_chat(chat_id)
    return {"deleted": chat_id}


# ---------- Feedback ----------
@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request):
    try:
        request.app.state.feedback.submit(req.message_id, req.rating, req.comment)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.delete("/feedback")
async def reset_feedback(request: Request):
    request.app.state.feedback.reset()
    return {"ok": True}


@router.get("/feedback")
async def list_feedback(request: Request):
    fb = request.app.state.feedback
    return {"summary": fb.summary(), "items": fb.recent()}


# ---------- Metrikler & kota ----------
@router.get("/metrics")
async def metrics(request: Request):
    return request.app.state.metrics.summary()


@router.delete("/metrics")
async def reset_metrics(request: Request):
    request.app.state.metrics.reset()
    return {"ok": True}


@router.get("/metrics/recent")
async def metrics_recent(request: Request):
    return request.app.state.metrics.recent()


@router.get("/quota")
async def quota(request: Request):
    return request.app.state.budget.status()


# ---------- Dokümanlar ----------
@router.get("/documents")
async def documents(request: Request):
    store = request.app.state.store
    indexed = await asyncio.to_thread(store.sources)
    files = sorted(p.name for p in settings.docs_dir.glob("*.pdf"))
    return {"files": [{"name": f, "chunks": indexed.get(f, 0), "indexed": f in indexed} for f in files],
            "total_chunks": await asyncio.to_thread(store.count)}


@router.post("/index")
async def index(request: Request, reset: bool = False):
    st = request.app.state
    if st.indexer.status()["running"]:
        raise HTTPException(409, "Indexing is already running.")
    paths = await asyncio.to_thread(st.indexer.pending, reset)
    if not paths and not reset:
        return {"started": False, "files": 0, "message": "No new files to index."}
    st.indexer.start(paths, reset)
    return {"started": True, "files": len(paths)}


@router.get("/index/status")
async def index_status(request: Request):
    return request.app.state.indexer.status()


@router.post("/upload")
async def upload(file: UploadFile, request: Request):
    st = request.app.state
    name = Path(file.filename or "").name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files can be uploaded")
    if st.indexer.status()["running"]:
        raise HTTPException(409, "Another file is still being indexed. Wait until it finishes.")
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
    path = settings.docs_dir / name
    path.write_bytes(data)
    if not st.indexer.start([path]):
        raise HTTPException(409, "Another file is still being indexed. Wait until it finishes.")
    return {"file": name, "started": True}


@router.delete("/documents/{name}")
async def delete_document(name: str, request: Request):
    name = Path(name).name
    await asyncio.to_thread(request.app.state.store.delete_source, name)
    (settings.docs_dir / name).unlink(missing_ok=True)
    request.app.state.bm25.invalidate()
    return {"deleted": name}


# ---------- Lite Eval ----------
@router.get("/eval/questions")
async def eval_questions():
    return {"questions": evaluation.load_questions(), "file": "backend/data/eval/questions.json"}


@router.post("/eval/generate")
async def eval_generate(request: Request, n: int = 5):
    st = request.app.state
    if st.store.count() == 0:
        raise HTTPException(409, "Index some documents first.")
    return await evaluation.generate_questions(st.llm, st.budget, st.store, n)


@router.post("/eval/retrieval")
async def eval_retrieval(request: Request):
    st = request.app.state
    questions = evaluation.load_questions()
    if not questions:
        raise HTTPException(400, "Add test questions first (generate them or edit questions.json).")
    result = await asyncio.to_thread(evaluation.run_retrieval_eval, st.agent.retrieval, questions)
    evaluation.save_run("retrieval", result)
    return result


@router.post("/eval/answers")
async def eval_answers(request: Request, limit: int = 30):
    st = request.app.state
    answers = st.history.recent_answers(max(1, min(limit, 100)))
    if not answers:
        raise HTTPException(400, "No saved answers with sources yet. Ask a few questions first.")
    result = await asyncio.to_thread(evaluation.run_answer_checks, st.store.embed, answers)
    evaluation.save_run("answers", result)
    return result


@router.get("/eval/latest")
async def eval_latest():
    return evaluation.latest_runs()
