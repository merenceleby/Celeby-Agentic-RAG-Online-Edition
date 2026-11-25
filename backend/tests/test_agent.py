import pytest

from config import settings
from services.agent import NotIndexedError, RAGAgent, format_history, strip_citations
from services.cache import Cache
from services.rate_limiter import LLMBudget, RateLimitError
from tests.fakes import FakeLLM, FakeRetrieval


def make_agent(verdicts=(True,), rpm=9, retrieval=None):
    budget = LLMBudget(rpm, 1400, settings.usage_db, clock=lambda: 0.0)
    llm = FakeLLM(verdicts)
    return RAGAgent(llm, retrieval or FakeRetrieval(), budget, Cache()), llm, budget


async def test_quality_clean_pass_uses_three_calls():
    agent, llm, _ = make_agent()
    r = await agent.run_quality("who is voldemort", 6)
    m = r["metadata"]
    assert m["llm_calls"] == 3 and m["validated"] and not m["correction_applied"] and m["mode"] == "quality"
    assert r["sources"][0]["metadata"]["page"] == 651
    assert agent.retrieval.last_queries[0] == "who is voldemort"
    assert len(agent.retrieval.last_queries) == 1 + settings.num_query_variations  # 'd' fazlalığı kırpıldı


async def test_second_identical_query_is_served_from_cache_without_calls():
    agent, llm, budget = make_agent()
    await agent.run_quality("same question", 6)
    used = budget.status()["used_last_minute"]
    r = await agent.run_quality("Same question ", 6)  # büyük/küçük harf ve boşluk fark etmez
    assert r["metadata"]["cache_hit"] and r["metadata"]["llm_calls"] == 0
    assert budget.status()["used_last_minute"] == used


async def test_self_correction_runs_generate_and_validate_again():
    agent, llm, _ = make_agent(verdicts=(False, True))
    r = await agent.run_quality("q", 6)
    m = r["metadata"]
    assert m["llm_calls"] == 5 and m["correction_applied"] and m["validated"]
    assert "unsupported" in llm.last_prompt  # geri bildirim ikinci üretimde prompt'a girdi


async def test_correction_skipped_when_budget_cannot_cover_retry():
    agent, _, _ = make_agent(verdicts=(False,), rpm=4)
    m = (await agent.run_quality("q", 6))["metadata"]
    assert m["llm_calls"] == 3 and not m["validated"]
    assert "retry_skipped_rate_limit" in m["warnings"] and "answer_unverified" in m["warnings"]


async def test_still_unsupported_after_retry_is_flagged_and_not_cached():
    agent, _, _ = make_agent(verdicts=(False, False))
    m = (await agent.run_quality("q2", 6))["metadata"]
    assert m["llm_calls"] == 5 and not m["validated"] and "answer_unverified" in m["warnings"]
    again = await agent.run_quality("q2", 6)  # cache'e yazılmadığı için tekrar çalışır
    assert again["metadata"]["cache_hit"] is False


async def test_unparseable_validator_does_not_block_answer():
    agent, llm, _ = make_agent()
    original = llm.generate_json

    async def bad(prompt, ticket, system=None, temperature=0.0):
        if "alternative search queries" in prompt:
            return await original(prompt, ticket)
        ticket.use()
        return None

    llm.generate_json = bad
    m = (await agent.run_quality("q3", 6))["metadata"]
    assert m["validated"] and "validator_unparseable" in m["warnings"]


async def test_rate_limit_is_raised_before_any_llm_call():
    agent, llm, budget = make_agent()
    budget.reserve(9)
    with pytest.raises(RateLimitError):
        await agent.run_quality("q4", 6)
    assert llm.calls == 0


async def test_fast_mode_streams_with_one_call():
    agent, _, _ = make_agent()
    r = await agent.run_fast("x", 3)
    assert r["answer"] == "merhaba" and r["metadata"]["llm_calls"] == 1 and r["metadata"]["mode"] == "fast"


async def test_empty_index_raises():
    agent, _, _ = make_agent(retrieval=FakeRetrieval(n=0))
    with pytest.raises(NotIndexedError):
        await agent.run_quality("q", 6)
    with pytest.raises(NotIndexedError):
        await agent.prepare_fast("q", 6)


async def test_history_strips_old_citations_and_enters_prompt():
    assert strip_citations("Nagini is a snake [1, 2, 4].") == "Nagini is a snake."
    hist = [
        {"role": "user", "content": "Who is Nagini?"},
        {"role": "assistant", "content": "Nagini is Voldemort's snake [1, 2, 4]."},
    ]
    formatted = format_history(hist)
    assert "Nagini" in formatted and "[1" not in formatted

    agent, llm, _ = make_agent()
    await agent.run_quality("Who killed it?", 6, history=hist)
    assert "CONVERSATION HISTORY" in llm.last_prompt
    assert "Who is Nagini?" in llm.last_prompt
    assert "[1, 2, 4]" not in llm.last_prompt

    await agent.run_fast("Who killed it?", 4, history=hist)
    assert len(agent.retrieval.last_queries) == 2
    assert "Who is Nagini?" in agent.retrieval.last_queries[1]
