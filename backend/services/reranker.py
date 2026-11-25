import math
import threading

from config import settings


class Reranker:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(settings.reranker_model)
        return self._model

    def warmup(self) -> None:
        self.rerank("warmup", [{"id": "w", "content": "warmup", "metadata": {}, "score": 0.0}], 1)

    def rerank(self, query: str, docs: list[dict], top_k: int) -> list[dict]:
        if not docs:
            return []
        scores = self._load().predict([(query, d["content"]) for d in docs])
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [{**d, "score": 1 / (1 + math.exp(-float(s)))} for d, s in ranked]  # 0-1'e sigmoid
