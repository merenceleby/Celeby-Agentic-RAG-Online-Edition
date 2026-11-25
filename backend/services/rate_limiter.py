"""Tüm LLM çağrılarının geçtiği bütçe kapısı.

- Dakika: kayan pencere (sliding window)
- Gün: SQLite'ta kalıcı sayaç (Pasifik saatiyle gece yarısı sıfırlanır)
- Token (opsiyonel): çağrı başına tahmini token ile dakikalık token penceresi
- Bir sorgu başlamadan ihtiyaç duyacağı çağrı sayısı Ticket ile rezerve edilir;
  böylece sorgu yarıda "limit doldu" diye patlamaz.
"""
import math
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")


class RateLimitError(Exception):
    def __init__(self, kind: str, retry_after: float, message: str):
        super().__init__(message)
        self.kind = kind  # "minute" | "day" | "provider"
        self.retry_after = max(1, int(math.ceil(retry_after)))
        self.message = message


class Ticket:
    """Rezerve edilmiş LLM çağrı hakkı. Her çağrı use() ile bir hak harcar."""

    def __init__(self, budget: "LLMBudget", n: int):
        self._budget = budget
        self.remaining = n
        self.used = 0

    def use(self) -> None:
        if self.remaining <= 0:
            raise RuntimeError("Ticket tükendi: rezervasyonsuz LLM çağrısı yapılamaz")
        self.remaining -= 1
        self.used += 1

    def top_up(self, n: int) -> None:
        """Ek hak ister; limit varsa RateLimitError fırlatır (haklar eklenmez)."""
        self._budget.reserve(n)
        self.remaining += n


class LLMBudget:
    def __init__(self, rpm: int, rpd: int, db_path, window: float = 60.0, clock=time.monotonic,
                 tpm: int = 0, est_tokens: int = 3500):
        self.rpm, self.rpd, self.window, self._clock = rpm, rpd, window, clock
        self.tpm, self.est_tokens = tpm, est_tokens
        self._calls: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self._db = str(db_path)
        with sqlite3.connect(self._db) as c:
            c.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")

    # --- gün sayacı ---
    @staticmethod
    def _day() -> str:
        return datetime.now(PT).date().isoformat()

    def _today(self) -> int:
        with sqlite3.connect(self._db) as c:
            row = c.execute("SELECT count FROM usage WHERE day=?", (self._day(),)).fetchone()
        return row[0] if row else 0

    def _add_today(self, n: int) -> None:
        with sqlite3.connect(self._db) as c:
            c.execute(
                "INSERT INTO usage(day,count) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET count=count+excluded.count",
                (self._day(), n),
            )

    @staticmethod
    def _secs_to_day_reset() -> float:
        now = datetime.now(PT)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return (nxt - now).total_seconds()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.window:
            self._calls.popleft()
        while self._tokens and now - self._tokens[0][0] >= self.window:
            self._tokens.popleft()

    # --- API ---
    def reserve(self, n: int) -> Ticket:
        if n > self.rpm:
            raise ValueError(f"{n} çağrı dakikalık limiti ({self.rpm}) aşıyor")
        need = n * self.est_tokens
        if self.tpm and need > self.tpm:
            raise ValueError(f"{n} çağrı tahmini {need} token, dakikalık token limitini ({self.tpm}) aşıyor")
        with self._lock:
            now = self._clock()
            self._prune(now)
            if self._today() + n > self.rpd:
                raise RateLimitError("day", self._secs_to_day_reset(),
                                     "Günlük ücretsiz kota doldu. Kota Pasifik saatiyle gece yarısı sıfırlanır.")
            if len(self._calls) + n > self.rpm:
                k = len(self._calls) + n - self.rpm  # kaç eski çağrının süresi dolmalı
                wait = self._calls[k - 1] + self.window - now
                raise RateLimitError("minute", wait,
                                     f"Dakikalık istek limitine ulaşıldı. Yaklaşık {int(math.ceil(wait))} sn sonra tekrar dene.")
            if self.tpm:
                used = sum(t for _, t in self._tokens)
                if used + need > self.tpm:
                    free, wait = used, 0.0
                    for ts, tok in self._tokens:  # en eskiden başla
                        free -= tok
                        wait = ts + self.window - now
                        if free + need <= self.tpm:
                            break
                    raise RateLimitError("minute", wait,
                                         f"Dakikalık token limitine ulaşıldı. Yaklaşık {int(math.ceil(wait))} sn sonra tekrar dene.")
            self._calls.extend([now] * n)
            if self.tpm:
                self._tokens.append((now, need))
            self._add_today(n)
        return Ticket(self, n)

    def status(self) -> dict:
        with self._lock:
            now = self._clock()
            self._prune(now)
            used_today = self._today()
            reset_in = int(math.ceil(self._calls[0] + self.window - now)) if self._calls else 0
            return {
                "rpm_limit": self.rpm, "used_last_minute": len(self._calls), "minute_reset_in": reset_in,
                "rpd_limit": self.rpd, "used_today": used_today,
                "remaining_today": max(0, self.rpd - used_today),
                "tpm_limit": self.tpm, "used_tokens_last_minute": sum(t for _, t in self._tokens),
            }
