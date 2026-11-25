from config import settings


def rrf(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}
    for lst in ranked_lists:
        for rank, d in enumerate(lst, start=1):
            scores[d["id"]] = scores.get(d["id"], 0.0) + 1.0 / (k + rank)
            docs.setdefault(d["id"], d)
    return [docs[i] for i in sorted(scores, key=scores.get, reverse=True)]


def _dedupe_overlap(docs: list[dict], threshold: float = 0.65) -> list[dict]:
    """Aynı dosyadan yüksek oranda çakışan (sayfa geçişi tekrarlı) chunk'ları ele."""
    kept: list[tuple[dict, set[str]]] = []
    for d in docs:
        words = set(d.get("content", "").lower().split())
        src = (d.get("metadata") or {}).get("source")
        dup = False
        if words:
            for prev, pwords in kept:
                if (prev.get("metadata") or {}).get("source") == src and pwords:
                    overlap = len(words & pwords) / min(len(words), len(pwords))
                    if overlap >= threshold:
                        dup = True
                        break
        if not dup:
            kept.append((d, words))
    return [d for d, _ in kept]


def _merge_texts(parts: list[str]) -> str:
    """Ardışık chunk'ları çakışan kısımlarını tekrar etmeden birleştir."""
    if not parts:
        return ""
    merged = parts[0]
    for nxt in parts[1:]:
        max_ov = min(len(merged), len(nxt), 300)
        ov = 0
        for k in range(max_ov, 25, -1):
            if merged.endswith(nxt[:k]):
                ov = k
                break
        merged = (merged + nxt[ov:]) if ov else (merged + "\n" + nxt)
    return merged


class Retrieval:
    def __init__(self, store, bm25, reranker):
        self.store, self.bm25, self.reranker = store, bm25, reranker

    def vector_only(self, query: str) -> list[dict]:
        """Tek vektör araması, rerank yok (eval karşılaştırması için)."""
        return self.store.search_many([query], settings.top_k_rerank)[0]

    def _expand_neighbors(self, docs: list[dict]) -> list[dict]:
        window = settings.neighbor_window
        if window <= 0 or not hasattr(self.store, "get_neighbors"):
            return docs
        out = []
        for d in docs:
            meta = d.get("metadata") or {}
            seq, src = meta.get("seq"), meta.get("source")
            if seq is None or not src:
                out.append(d)
                continue
            neighbors = self.store.get_neighbors(src, int(seq), window=window)
            if len(neighbors) > 1:
                merged = _merge_texts([n["content"] for n in neighbors])
                out.append({**d, "context": merged})
            else:
                out.append(d)
        return out

    def hybrid(self, queries: list[str], original: str) -> list[dict]:
        """Çok sorgulu vektör + BM25 → RRF → cross-encoder → dedupe → skor filtresi → komşu genişletme."""
        vec = self.store.search_many(queries, settings.top_k_retrieval)
        bm = self.bm25.search_many(queries, settings.top_k_bm25)
        fused = rrf(vec + bm)[: settings.top_k_retrieval]
        ranked = self.reranker.rerank(original, fused, settings.top_k_rerank + 4)
        ranked = _dedupe_overlap(ranked)[: settings.top_k_rerank]
        if settings.min_rerank_score > 0 and len(ranked) > 3:
            kept = [d for d in ranked if d.get("score", 0.0) >= settings.min_rerank_score]
            ranked = kept if len(kept) >= 3 else ranked[:3]
        return self._expand_neighbors(ranked)
