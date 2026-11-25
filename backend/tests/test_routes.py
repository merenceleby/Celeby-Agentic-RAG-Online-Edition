import re
import time

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from services.agent import RAGAgent
from services.cache import Cache
from services.chat_history import ChatHistory
from services.feedback import Feedback
from services.indexer import Indexer
from services.metrics import Metrics
from services.rate_limiter import LLMBudget
from tests.fakes import FakeBM25, FakeLLM, FakeRetrieval, make_doc


@pytest.fixture
def ctx():
    app = main.app
    budget = LLMBudget(9, 1400, settings.usage_db, clock=lambda: 0.0)
    retrieval = FakeRetrieval()
    llm = FakeLLM([True] * 10)
    st = app.state
    st.budget, st.llm, st.store, st.bm25 = budget, llm, retrieval.store, FakeBM25()
    st.history, st.metrics, st.feedback = ChatHistory(), Metrics(), Feedback()
    st.indexer = Indexer(retrieval.store, st.bm25)
    st.agent = RAGAgent(llm, retrieval, budget, Cache())
    st.warmup = {"ready": True}
    return TestClient(app), st


def wait_idle(client, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get("/api/index/status").json()
        if not s["running"]:
            return s
        time.sleep(0.02)
    raise AssertionError("indexing did not finish")


def test_health_reports_model_readiness(ctx):
    c, st = ctx
    assert c.get("/api/health").json()["models_ready"] is True
    st.warmup["ready"] = False
    assert c.get("/api/health").json()["models_ready"] is False


def test_quality_query_saves_chat_and_metrics(ctx):
    c, _ = ctx
    j = c.post("/api/query", json={"query": "Who is X?", "mode": "quality"}).json()
    m = j["metadata"]
    assert j["answer"] and m["llm_calls"] == 3 and m["chat_id"] and m["message_id"]
    chat = c.get(f"/api/chats/{m['chat_id']}").json()
    assert [x["role"] for x in chat["messages"]] == ["user", "assistant"]
    assert c.get("/api/chats").json()[0]["message_count"] == 2
    assert c.get("/api/metrics").json()["total_queries"] == 1
    assert c.post("/api/query", json={"query": "Who is X?", "chat_id": m["chat_id"]}).json()["metadata"]["cache_hit"]


def test_fast_query_and_unknown_chat(ctx):
    c, _ = ctx
    assert c.post("/api/query", json={"query": "hızlı", "mode": "fast"}).json()["answer"] == "merhaba"
    assert c.post("/api/query", json={"query": "q", "mode": "fast", "chat_id": 999}).status_code == 404


def test_validation_errors(ctx):
    c, _ = ctx
    assert c.post("/api/query", json={"query": ""}).status_code == 422
    assert c.post("/api/query", json={"query": "q", "mode": "direct"}).status_code == 422
    assert c.post("/api/query", json={"query": "q", "max_sentences": 99}).status_code == 422


def test_stream_emits_events_in_order_and_persists(ctx):
    c, st = ctx
    with c.stream("POST", "/api/query-stream", json={"query": "stream q"}) as r:
        body = "".join(r.iter_text())
    assert re.findall(r"event: (\w+)", body) == ["chat", "sources", "token", "token", "done"]
    chats = st.history.list_chats()
    assert chats[0]["message_count"] == 2
    assert st.history.get_chat(chats[0]["id"])["messages"][1]["content"] == "merhaba"


def test_rate_limit_returns_429_and_saves_nothing(ctx):
    c, st = ctx
    st.budget.reserve(9)
    r = c.post("/api/query", json={"query": "limit", "mode": "fast"})
    assert r.status_code == 429 and r.headers["retry-after"] == "60" and r.json()["kind"] == "minute"
    assert c.post("/api/query-stream", json={"query": "limit"}).status_code == 429
    assert st.history.list_chats() == []
    assert c.get("/api/metrics").json()["errors"] == {"rate_limit_minute": 2}
    assert c.get("/api/quota").json()["used_last_minute"] == 9


def test_empty_index_returns_409(ctx):
    c, st = ctx
    st.store.n = 0
    assert c.post("/api/query", json={"query": "q"}).json()["error"] == "not_indexed"


def test_feedback_flow_and_reset(ctx):
    c, _ = ctx
    mid = c.post("/api/query", json={"query": "fb", "mode": "fast"}).json()["metadata"]["message_id"]
    assert c.post("/api/feedback", json={"message_id": mid, "rating": -1, "comment": "wrong"}).json() == {"ok": True}
    assert c.post("/api/feedback", json={"message_id": 9999, "rating": 1}).status_code == 404
    assert c.post("/api/feedback", json={"message_id": mid, "rating": 5}).status_code == 422
    fb = c.get("/api/feedback").json()
    assert fb["summary"] == {"helpful": 0, "not_helpful": 1} and fb["items"][0]["comment"] == "wrong"
    assert c.delete("/api/feedback").json() == {"ok": True}
    assert c.get("/api/feedback").json()["summary"]["not_helpful"] == 0


def test_delete_chat_and_reset_metrics(ctx):
    c, _ = ctx
    cid = c.post("/api/query", json={"query": "del", "mode": "fast"}).json()["metadata"]["chat_id"]
    assert c.delete(f"/api/chats/{cid}").json() == {"deleted": cid}
    assert c.get(f"/api/chats/{cid}").status_code == 404
    assert c.delete("/api/metrics").json() == {"ok": True}
    assert c.get("/api/metrics").json()["total_queries"] == 0


def test_upload_indexes_in_background_with_progress(ctx):
    c, st = ctx
    r = c.post("/api/upload", files={"file": ("book.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r.json() == {"file": "book.pdf", "started": True}
    final = wait_idle(c)
    assert final["error"] is None and final["done"] == [{"file": "book.pdf", "chunks": 3}]
    assert (settings.docs_dir / "book.pdf").exists() and st.store.added == ["book.pdf"]


def test_upload_rejects_non_pdf_oversize_and_path_tricks(ctx):
    c, _ = ctx
    assert c.post("/api/upload", files={"file": ("notes.txt", b"x", "text/plain")}).status_code == 400
    object.__setattr__(settings, "max_upload_mb", 0)
    try:
        assert c.post("/api/upload", files={"file": ("a.pdf", b"xx", "application/pdf")}).status_code == 413
    finally:
        object.__setattr__(settings, "max_upload_mb", 50)
    c.post("/api/upload", files={"file": ("../../evil.pdf", b"%PDF", "application/pdf")})
    wait_idle(c)
    assert (settings.docs_dir / "evil.pdf").exists()  # klasör dışına yazılmadı


def test_upload_rejected_while_indexing_is_running(ctx):
    c, st = ctx
    st.indexer._state["running"] = True
    assert c.post("/api/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 409
    assert c.post("/api/index").status_code == 409
    st.indexer._state["running"] = False


def test_index_endpoint_without_new_files(ctx):
    c, _ = ctx
    assert c.post("/api/index").json()["started"] is False


def test_index_directory_picks_up_manually_copied_pdfs(ctx):
    c, st = ctx
    (settings.docs_dir / "manual.pdf").write_bytes(b"%PDF")
    assert c.post("/api/index").json() == {"started": True, "files": 1}
    wait_idle(c)
    assert "manual.pdf" in st.store.added


# ---------- Lite Eval ----------
def test_eval_endpoints_end_to_end(ctx):
    c, st = ctx
    assert c.post("/api/eval/retrieval").status_code == 400  # soru yok
    assert c.post("/api/eval/answers").status_code == 400  # kayıtlı cevap yok
    st.store.docs = [make_doc(i, page=i, content="x" * 400) for i in range(1, 4)]
    r = c.post("/api/eval/generate?n=2").json()
    assert r["added"] == 1 and c.get("/api/eval/questions").json()["questions"][0]["generated"]
    ret = c.post("/api/eval/retrieval").json()
    assert ret["questions"] == 1 and "vector" in ret and "hybrid" in ret
    c.post("/api/query", json={"query": "Who is Voldemort?", "mode": "fast"})
    ans = c.post("/api/eval/answers?limit=10").json()
    assert ans["answers"] == 1
    latest = c.get("/api/eval/latest").json()
    assert latest["retrieval"]["questions"] == 1 and latest["answers"]["answers"] == 1


def test_eval_generate_requires_index_and_respects_limit(ctx):
    c, st = ctx
    st.store.n = 0
    assert c.post("/api/eval/generate").status_code == 409
    st.store.n, st.store.docs = 5, [make_doc(content="x" * 400)]
    st.budget.reserve(9)
    assert c.post("/api/eval/generate?n=1").status_code == 429
