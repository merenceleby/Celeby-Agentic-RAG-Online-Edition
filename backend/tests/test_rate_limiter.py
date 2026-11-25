import pytest

from config import settings
from services.rate_limiter import LLMBudget, RateLimitError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(rpm=9, rpd=1400, tpm=0, est=3500):
    clock = Clock()
    return LLMBudget(rpm, rpd, settings.usage_db, clock=clock, tpm=tpm, est_tokens=est), clock


def test_minute_limit_and_retry_after():
    b, clock = make()
    for _ in range(3):
        b.reserve(3)
    with pytest.raises(RateLimitError) as e:
        b.reserve(3)
    assert e.value.kind == "minute" and e.value.retry_after == 60
    clock.t = 30
    with pytest.raises(RateLimitError) as e:
        b.reserve(3)
    assert e.value.retry_after == 30


def test_window_resets():
    b, clock = make()
    b.reserve(9)
    clock.t = 61
    b.reserve(9)


def test_failed_reserve_books_nothing():
    b, _ = make(rpm=4)
    b.reserve(3)
    with pytest.raises(RateLimitError):
        b.reserve(2)
    b.reserve(1)  # hâlâ 1 hak var


def test_daily_limit():
    b, _ = make(rpd=5)
    b.reserve(3)
    with pytest.raises(RateLimitError) as e:
        b.reserve(3)
    assert e.value.kind == "day"


def test_daily_counter_is_persistent():
    make(rpd=4)[0].reserve(3)
    b2, _ = make(rpd=4)  # yeni nesne, aynı veritabanı
    with pytest.raises(RateLimitError):
        b2.reserve(3)


def test_request_larger_than_rpm_is_rejected():
    b, _ = make(rpm=2)
    with pytest.raises(ValueError):
        b.reserve(3)


def test_ticket_blocks_unreserved_calls():
    b, _ = make()
    t = b.reserve(1)
    t.use()
    with pytest.raises(RuntimeError):
        t.use()


def test_ticket_top_up():
    b, _ = make(rpm=4)
    t = b.reserve(3)
    with pytest.raises(RateLimitError):
        t.top_up(2)
    assert t.remaining == 3
    t.top_up(1)
    assert t.remaining == 4


def test_token_limit():
    b, clock = make(tpm=10000, est=3500)
    b.reserve(2)  # 7000
    with pytest.raises(RateLimitError) as e:
        b.reserve(1)  # 10500 > 10000
    assert e.value.kind == "minute" and e.value.retry_after == 60
    clock.t = 61
    b.reserve(1)


def test_token_limit_disabled_by_default():
    b, _ = make(tpm=0, est=10**6)
    b.reserve(3)


def test_status():
    b, _ = make(tpm=20000)
    b.reserve(2)
    s = b.status()
    assert s["used_last_minute"] == 2 and s["used_today"] == 2 and s["remaining_today"] == 1398
    assert s["used_tokens_last_minute"] == 7000
