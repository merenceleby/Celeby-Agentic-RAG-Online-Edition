import math
import time
from typing import Optional

from services.db import connect


def _pctile(sorted_vals: list[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    return round(sorted_vals[max(0, math.ceil(p * len(sorted_vals)) - 1)], 3)


class Metrics:
    def record(self, mode: str, *, ok: bool = True, error_kind: Optional[str] = None,
               meta: Optional[dict] = None, query: str = "") -> None:
        meta = meta or {}
        flag = lambda k: None if meta.get(k) is None else int(bool(meta[k]))  # noqa: E731
        with connect() as c:
            c.execute(
                "INSERT INTO metrics(ts,mode,ok,error_kind,latency,llm_calls,cache_hit,corrected,"
                "validated,num_sources,query) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), mode, int(ok), error_kind, meta.get("query_time"), meta.get("llm_calls"),
                 flag("cache_hit"), flag("correction_applied"), flag("validated"),
                 meta.get("num_sources"), query[:300]))

    def summary(self) -> dict:
        with connect() as c:
            t = c.execute("SELECT COUNT(*) n, COALESCE(SUM(llm_calls),0) calls, COALESCE(SUM(cache_hit),0) hits, "
                          "COALESCE(SUM(corrected),0) corr FROM metrics WHERE ok=1").fetchone()
            q = c.execute("SELECT COUNT(*) n, COALESCE(SUM(validated),0) v, COALESCE(SUM(corrected),0) corr "
                          "FROM metrics WHERE ok=1 AND mode='quality' AND cache_hit=0").fetchone()
            errs = c.execute("SELECT error_kind k, COUNT(*) n FROM metrics WHERE ok=0 "
                             "GROUP BY error_kind").fetchall()
            modes = c.execute("SELECT mode, COUNT(*) n, AVG(latency) avg FROM metrics "
                              "WHERE ok=1 AND cache_hit=0 GROUP BY mode").fetchall()
            lat = [r[0] for r in c.execute("SELECT latency FROM metrics WHERE ok=1 AND cache_hit=0 "
                                           "AND latency IS NOT NULL ORDER BY latency")]
        failed = sum(r["n"] for r in errs)
        pct = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
        return {
            "total_queries": t["n"], "llm_calls": t["calls"], "self_corrections": t["corr"],
            "cache_hit_rate": pct(t["hits"], t["n"]),
            "verified_rate": pct(q["v"], q["n"]), "correction_rate": pct(q["corr"], q["n"]),
            "avg_latency": round(sum(lat) / len(lat), 3) if lat else None,  # saniye
            "p95_latency": _pctile(lat, 0.95), "p99_latency": _pctile(lat, 0.99),
            "failed_queries": failed, "error_rate": pct(failed, t["n"] + failed),
            "errors": {r["k"]: r["n"] for r in errs},
            "by_mode": {r["mode"]: {"count": r["n"], "avg_latency": round(r["avg"], 3) if r["avg"] is not None else None}
                        for r in modes},
        }

    def recent(self, limit: int = 20) -> list[dict]:
        with connect() as c:
            rows = c.execute("SELECT ts,mode,ok,error_kind,latency,llm_calls,cache_hit,validated,query "
                             "FROM metrics ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with connect() as c:
            c.execute("DELETE FROM metrics")
