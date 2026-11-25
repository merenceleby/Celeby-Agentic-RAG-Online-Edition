"""Redis varsa onu, yoksa bellek içi TTL cache kullanır."""
import json
import time
from typing import Optional

from config import settings


class Cache:
    def __init__(self):
        self._r, self._failed = None, False
        self._mem: dict[str, tuple[float, str]] = {}

    async def _redis(self):
        if self._r is None and not self._failed:
            try:
                import redis.asyncio as aioredis

                r = aioredis.from_url(settings.redis_url, decode_responses=True)
                await r.ping()
                self._r = r
            except Exception:  # noqa: BLE001
                self._failed = True
        return self._r

    async def get(self, key: str) -> Optional[dict]:
        if not settings.enable_cache:
            return None
        r = await self._redis()
        if r:
            raw = await r.get(key)
        else:
            hit = self._mem.get(key)
            raw = hit[1] if hit and hit[0] > time.time() else None
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict) -> None:
        if not settings.enable_cache:
            return
        raw = json.dumps(value, ensure_ascii=False)
        r = await self._redis()
        if r:
            await r.set(key, raw, ex=settings.cache_ttl)
        else:
            self._mem[key] = (time.time() + settings.cache_ttl, raw)
