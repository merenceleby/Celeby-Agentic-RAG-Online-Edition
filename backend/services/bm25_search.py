import heapq
import re
import threading


def _tok(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


class BM25Index:
    """Chroma'daki chunk'lardan lazy kurulan BM25 indeksi. İndeks değişince invalidate() çağır."""

    def __init__(self, store):
        self._store, self._bm25, self._items = store, None, []
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._bm25, self._items = None, []

    def _ensure(self) -> bool:
        with self._lock:
            if self._bm25 is None:
                from rank_bm25 import BM25Okapi

                self._items = self._store.all_documents()
                if self._items:
                    self._bm25 = BM25Okapi([_tok(d["content"]) for d in self._items])
            return self._bm25 is not None

    def search_many(self, queries: list[str], n: int) -> list[list[dict]]:
        if not self._ensure():
            return [[] for _ in queries]
        out = []
        for q in queries:
            scores = self._bm25.get_scores(_tok(q))
            top = heapq.nlargest(n, range(len(scores)), key=scores.__getitem__)
            out.append([{**self._items[i], "score": float(scores[i])} for i in top if scores[i] > 0])
        return out
