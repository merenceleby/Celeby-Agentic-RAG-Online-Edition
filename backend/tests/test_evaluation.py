import json

import pytest

from config import settings
from services import evaluation as ev
from services.rate_limiter import LLMBudget, RateLimitError
from tests.fakes import FakeLLM, FakeRetrieval, FakeStore, fake_embed, make_doc


def src(text):
    return {"content": text, "metadata": {"source": "b.pdf", "page": 1}}


# ---------- test soruları ----------
def test_normalize_question_variants():
    assert ev.normalize_question({"question": " Q? ", "source": "a.pdf", "page": 5})["pages"] == [5]
    assert ev.normalize_question({"question": "Q?", "source": "a.pdf", "pages": [1, "2", "x"]})["pages"] == [1, 2]
    assert ev.normalize_question({"question": "Q?", "source": "a.pdf"})["pages"] == []
    assert ev.normalize_question({"question": "", "source": "a.pdf"}) is None
    assert ev.normalize_question({"question": "Q?"}) is None
    assert ev.normalize_question("not a dict") is None


def test_load_and_save_questions(isolated_state):
    assert ev.load_questions() == []
    ev.save_questions([{"question": "Q1?", "source": "a.pdf", "pages": [1]}])
    assert ev.load_questions()[0]["question"] == "Q1?"
    (settings.eval_dir / "questions.json").write_text("{bozuk", encoding="utf-8")
    assert ev.load_questions() == []
    (settings.eval_dir / "questions.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert ev.load_questions() == []


async def test_generate_questions_reserves_budget_and_dedupes():
    docs = [make_doc(i, page=i, content="x" * 400) for i in range(1, 6)]
    budget = LLMBudget(9, 1400, settings.usage_db, clock=lambda: 0.0)
    llm = FakeLLM()  # hep aynı soruyu üretir
    r = await ev.generate_questions(llm, budget, FakeStore(docs=docs), 4)
    assert llm.calls == 4 and budget.status()["used_last_minute"] == 4
    assert r["added"] == 1 and r["total"] == 1  # aynı soru tekrar eklenmez
    q = ev.load_questions()[0]
    assert q["generated"] and q["source"] == "book.pdf" and len(q["pages"]) == 1


async def test_generate_questions_respects_rate_limit():
    budget = LLMBudget(3, 1400, settings.usage_db, clock=lambda: 0.0)
    budget.reserve(2)
    store = FakeStore(docs=[make_doc(i, content="x" * 400) for i in range(5)])
    llm = FakeLLM()
    with pytest.raises(RateLimitError):
        await ev.generate_questions(llm, budget, store, 3)
    assert llm.calls == 0


# ---------- retrieval ----------
def test_hit_rank_matches_source_and_page():
    docs = [make_doc(1, "a.pdf", 1), make_doc(2, "b.pdf", 7), make_doc(3, "b.pdf", 8)]
    assert ev.hit_rank(docs, {"source": "b.pdf", "pages": [8]}) == 3
    assert ev.hit_rank(docs, {"source": "b.pdf", "pages": []}) == 2  # sayfa verilmediyse sadece kaynak
    assert ev.hit_rank(docs, {"source": "c.pdf", "pages": []}) is None
    assert ev.hit_rank(docs, {"source": "b.pdf", "pages": [99]}) is None


def test_summarize_ranks():
    s = ev.summarize_ranks([1, 2, None, 4], [0.1, 0.1, 0.1, 0.1], k=5)
    assert s["hit_at_1"] == 25.0 and s["hit_at_3"] == 50.0 and s["hit_at_5"] == 75.0
    assert s["mrr"] == round((1 + 0.5 + 0.25) / 4, 3) and s["avg_ms"] == 100
    assert ev.summarize_ranks([1], [0.0], k=3)["hit_at_5"] is None  # k < 5 ise Hit@5 tanımsız


def test_run_retrieval_eval_reports_both_pipelines_and_misses():
    retrieval = FakeRetrieval(docs=[make_doc(1, "book.pdf", 651)])
    qs = [{"question": "q1", "source": "book.pdf", "pages": [651]},
          {"question": "q2", "source": "book.pdf", "pages": [12]}]
    out = ev.run_retrieval_eval(retrieval, qs)
    assert out["questions"] == 2 and out["vector"]["hit_at_1"] == 50.0 and out["hybrid"]["mrr"] == 0.5
    assert out["missed"] == [{"question": "q2", "source": "book.pdf", "pages": [12]}]


# ---------- cevap kontrolleri ----------
def test_split_sentences_strips_citations_and_short_fragments():
    s = ev.split_sentences("Voldemort kept Nagini close by [1]. Yes. He feared losing her [2].")
    assert s == ["Voldemort kept Nagini close by .", "He feared losing her ."] or len(s) == 2
    assert all("[" not in x for x in s)


def test_grounded_answer_scores_higher_than_invented_one():
    sources = [src("Voldemort keeps the snake Nagini close by him because she is a Horcrux")]
    good = ev.check_answer(fake_embed, "Who is Nagini?", "Nagini is the snake Voldemort keeps close by him [1].", sources)
    bad = ev.check_answer(fake_embed, "Who is Nagini?", "Quidditch matches take place every spring at the stadium.", sources)
    assert good["grounding"] == 1.0 and bad["grounding"] == 0.0
    assert good["relevancy"] > bad["relevancy"] and good["citation_valid"] is True and bad["citation_valid"] is None


def test_invalid_citation_marker_is_detected():
    r = ev.check_answer(fake_embed, "q", "The snake stays near him always [3].", [src("The snake stays near him always")])
    assert r["citation_valid"] is False


def test_abstained_answers_are_not_scored():
    r = ev.check_answer(fake_embed, "q", "I could not find this in the documents.", [src("something")])
    assert r["abstained"] is True and "grounding" not in r
    assert ev.check_answer(fake_embed, "q", "answer", []) is None  # kaynaksız cevap kontrol edilmez


def test_run_answer_checks_aggregates():
    answers = [
        {"message_id": 1, "question": "Who is Nagini?", "sources": [src("Voldemort keeps the snake Nagini close by him")],
         "answer": "Nagini is the snake Voldemort keeps close by him [1]."},
        {"message_id": 2, "question": "Weather?", "sources": [src("Voldemort keeps the snake Nagini close by him")],
         "answer": "Quidditch matches take place every spring at the stadium."},
        {"message_id": 3, "question": "Unknown?", "sources": [src("text")], "answer": "I could not find that."},
    ]
    out = ev.run_answer_checks(fake_embed, answers)
    assert out["answers"] == 3 and out["scored"] == 2 and out["abstained"] == 1
    assert out["grounding"] == 50.0 and out["citation_valid"] == 100.0
    assert out["worst"][0]["question"] == "Weather?"


def test_eval_runs_are_saved_and_latest_returned():
    assert ev.latest_runs() == {"retrieval": None, "answers": None}
    ev.save_run("retrieval", {"kind": "retrieval", "questions": 1})
    ev.save_run("retrieval", {"kind": "retrieval", "questions": 2})
    latest = ev.latest_runs()
    assert latest["retrieval"]["questions"] == 2 and "ts" in latest["retrieval"] and latest["answers"] is None
