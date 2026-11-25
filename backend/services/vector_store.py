"""ChromaDB + yerel embedding (sentence-transformers). API kotası harcamaz."""
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from config import settings


def _clean_text(text: str) -> str:
    """Paragraf boşluklarını koruyarak fazla boşlukları temizle."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paras if p)


def _find_split(text: str, low: int, high: int) -> int:
    """Paragraf -> cümle sonu -> boşluk önceliğiyle kesme noktası bul."""
    cut = text.rfind("\n\n", low, high)
    if cut != -1:
        return cut
    for m in reversed(list(re.finditer(r"[.!?][\"')\]]?(?:\s|$)", text[low:high]))):
        return low + m.end()
    cut = text.rfind(" ", low, high)
    if cut != -1:
        return cut
    return high


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = _clean_text(text)
    chunks, start = [], 0
    while text and start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            end = _find_split(text, start + size // 2, end)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        nxt = max(end - overlap, start + 1)
        if nxt > start and nxt < end and text[nxt - 1] not in (" ", "\n"):
            sp = text.find(" ", nxt, end)
            if sp != -1 and sp + 1 < end:
                nxt = sp + 1
        start = nxt
    return [c for c in chunks if len(c) > 20]


def _page_tail(text: str, max_chars: int) -> str:
    """Sayfa geçişlerinde cümle kopmasını önlemek için sayfa sonundan kısa kuyruk al."""
    text = _clean_text(text)
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    sp = tail.find(" ")
    return tail[sp + 1:].strip() if sp != -1 else tail.strip()


class VectorStore:
    def __init__(self):
        import chromadb

        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self._col = self._new_collection()
        self._model = None
        self._model_lock = threading.Lock()

    def _new_collection(self):
        return self._client.get_or_create_collection("documents", metadata={"hnsw:space": "cosine"})

    @staticmethod
    def _prepare_for_embed(texts: list[str], kind: str) -> list[str]:
        if "e5" in settings.embedding_model.lower():
            prefix = "query: " if kind == "query" else "passage: "
            return [prefix + t for t in texts]
        return texts

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        with self._model_lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(settings.embedding_model)
        prepared = self._prepare_for_embed(texts, kind)
        return self._model.encode(prepared, normalize_embeddings=True, show_progress_bar=False,
                                  batch_size=32).tolist()

    def warmup(self) -> None:
        self.embed(["warmup"], kind="query")

    # --- indeksleme ---
    def add_pdf(self, path: Path, on_progress: Optional[Callable[[int, int], None]] = None) -> int:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        total = len(reader.pages)
        ids, docs, embed_docs, metas, count = [], [], [], [], 0
        prev_tail = ""

        def flush():
            nonlocal ids, docs, embed_docs, metas
            if ids:
                self._col.upsert(
                    ids=ids, documents=docs, metadatas=metas,
                    embeddings=self.embed(embed_docs, kind="passage"),
                )
            ids, docs, embed_docs, metas = [], [], [], []

        for pno, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            combined = f"{prev_tail} {raw}".strip() if prev_tail and raw.strip() else raw
            for i, ch in enumerate(chunk_text(combined, settings.chunk_size, settings.chunk_overlap)):
                ids.append(f"{path.name}:{pno}:{i}")
                docs.append(ch)
                embed_docs.append(f"[{path.name} | p.{pno}] {ch}")
                metas.append({"source": path.name, "page": pno, "seq": count})
                count += 1
            prev_tail = _page_tail(raw, settings.chunk_overlap) if raw.strip() else ""
            if len(ids) >= 128:
                flush()
            if on_progress:
                on_progress(pno, total)
        flush()
        return count

    def get_neighbors(self, source: str, seq: int, window: int = 1) -> list[dict]:
        """Aynı dokümandan komşu chunk'ları sıralı getirir (Small-to-Big bağlam genişletme)."""
        if window <= 0:
            return []
        lo, hi = max(0, seq - window), seq + window
        r = self._col.get(
            where={"$and": [{"source": source}, {"seq": {"$gte": lo}}, {"seq": {"$lte": hi}}]},
            include=["documents", "metadatas"],
        )
        items = [{"id": i, "content": d, "metadata": m}
                 for i, d, m in zip(r["ids"], r["documents"], r["metadatas"])]
        return sorted(items, key=lambda x: x["metadata"].get("seq", 0))

    def reset(self) -> None:
        self._client.delete_collection("documents")
        self._col = self._new_collection()

    def delete_source(self, name: str) -> None:
        self._col.delete(where={"source": name})

    # --- sorgu ---
    def count(self) -> int:
        return self._col.count()

    def sources(self) -> dict[str, int]:
        metas = self._col.get(include=["metadatas"])["metadatas"] or []
        return dict(Counter(m["source"] for m in metas))

    def all_documents(self) -> list[dict]:
        r = self._col.get(include=["documents", "metadatas"])
        return [{"id": i, "content": d, "metadata": m, "score": 0.0}
                for i, d, m in zip(r["ids"], r["documents"], r["metadatas"])]

    def search_many(self, queries: list[str], n: int) -> list[list[dict]]:
        total = self._col.count()
        if total == 0:
            return [[] for _ in queries]
        r = self._col.query(query_embeddings=self.embed(queries, kind="query"), n_results=min(n, total),
                            include=["documents", "metadatas", "distances"])
        return [[{"id": r["ids"][q][j], "content": r["documents"][q][j],
                  "metadata": r["metadatas"][q][j], "score": 1.0 - r["distances"][q][j]}
                 for j in range(len(r["ids"][q]))] for q in range(len(queries))]
