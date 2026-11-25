import pytest

from services.chat_history import ChatHistory
from services.feedback import Feedback
from services.metrics import Metrics


def test_chat_history_roundtrip():
    h = ChatHistory()
    cid = h.create_chat("  Who is Nagini?  ")
    h.add_message(cid, "user", "Who is Nagini?")
    mid = h.add_message(cid, "assistant", "A snake [1].", [{"content": "x", "metadata": {"page": 1}}], {"mode": "quality"})
    chat = h.get_chat(cid)
    assert chat["title"] == "Who is Nagini?" and [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["id"] == mid and chat["messages"][1]["sources"][0]["metadata"]["page"] == 1
    assert h.list_chats()[0]["message_count"] == 2
    assert h.exists(cid) and not h.exists(999) and h.get_chat(999) is None


def test_delete_chat_removes_messages_and_feedback():
    h, fb = ChatHistory(), Feedback()
    cid = h.create_chat("t")
    h.add_message(cid, "user", "q")
    mid = h.add_message(cid, "assistant", "a")
    fb.submit(mid, 1)
    h.delete_chat(cid)
    assert h.get_chat(cid) is None and fb.summary() == {"helpful": 0, "not_helpful": 0}


def test_title_is_truncated_and_never_empty():
    h = ChatHistory()
    assert len(h.list_chats()) == 0
    h.create_chat("x" * 200)
    h.create_chat("   ")
    titles = {c["title"] for c in h.list_chats()}
    assert "x" * 60 in titles and "New chat" in titles


def test_recent_answers_only_include_sourced_answers():
    h = ChatHistory()
    cid = h.create_chat("t")
    h.add_message(cid, "user", "with sources?")
    h.add_message(cid, "assistant", "yes", [{"content": "c", "metadata": {}}])
    h.add_message(cid, "user", "no sources?")
    h.add_message(cid, "assistant", "no", [])
    got = h.recent_answers()
    assert len(got) == 1 and got[0]["question"] == "with sources?" and got[0]["answer"] == "yes"


def test_feedback_upsert_summary_and_validation():
    h, fb = ChatHistory(), Feedback()
    cid = h.create_chat("t")
    h.add_message(cid, "user", "the question")
    mid = h.add_message(cid, "assistant", "the answer")
    fb.submit(mid, -1, "  wrong page  ")
    fb.submit(mid, 1)  # aynı mesaj tekrar oylanırsa güncellenir
    assert fb.summary() == {"helpful": 1, "not_helpful": 0}
    fb.submit(mid, -1, "wrong page")
    item = fb.recent()[0]
    assert item["comment"] == "wrong page" and item["question"] == "the question"
    with pytest.raises(ValueError):
        fb.submit(9999, 1)
    user_msg = h.get_chat(cid)["messages"][0]["id"]
    with pytest.raises(ValueError):  # kullanıcı mesajı oylanamaz
        fb.submit(user_msg, 1)
    fb.reset()
    assert fb.summary()["helpful"] == 0


def test_metrics_summary_and_reset():
    m = Metrics()
    m.record("quality", meta={"query_time": 10.0, "llm_calls": 3, "cache_hit": False, "correction_applied": True,
                              "validated": True, "num_sources": 5}, query="a")
    m.record("quality", meta={"query_time": 20.0, "llm_calls": 5, "cache_hit": False, "correction_applied": False,
                              "validated": False}, query="b")
    m.record("quality", meta={"query_time": 0.01, "llm_calls": 0, "cache_hit": True, "validated": True}, query="a")
    m.record("fast", meta={"query_time": 1.0, "llm_calls": 1, "cache_hit": False}, query="c")
    m.record("quality", ok=False, error_kind="rate_limit_minute", query="d")
    s = m.summary()
    assert s["total_queries"] == 4 and s["llm_calls"] == 9 and s["self_corrections"] == 1
    assert s["cache_hit_rate"] == 25.0 and s["failed_queries"] == 1 and s["error_rate"] == 20.0
    assert s["verified_rate"] == 50.0 and s["correction_rate"] == 50.0  # önbellek dışındaki 2 quality sorgusu
    assert s["p95_latency"] == 20.0 and s["avg_latency"] == round((10 + 20 + 1) / 3, 3)  # önbellek süreye girmez
    assert s["errors"] == {"rate_limit_minute": 1} and s["by_mode"]["fast"]["count"] == 1
    assert m.recent()[0]["query"] == "d"
    m.reset()
    assert m.summary()["total_queries"] == 0 and m.summary()["avg_latency"] is None
