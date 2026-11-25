"""Arka planda PDF indeksleme (ilerleme takibiyle). Aynı anda tek iş çalışır."""
import threading
import time
from pathlib import Path

from config import settings


class Indexer:
    def __init__(self, store, bm25):
        self.store, self.bm25 = store, bm25
        self._lock = threading.Lock()
        self._state = self._fresh()

    @staticmethod
    def _fresh() -> dict:
        return {"running": False, "file": None, "file_index": 0, "file_total": 0, "page": 0, "pages": 0,
                "done": [], "error": None, "finished_at": None}

    def status(self) -> dict:
        with self._lock:
            return {**self._state, "done": list(self._state["done"])}

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def pending(self, reset: bool = False) -> list[Path]:
        pdfs = sorted(settings.docs_dir.glob("*.pdf"))
        if reset:
            return pdfs
        done = set(self.store.sources())
        return [p for p in pdfs if p.name not in done]

    def start(self, paths: list[Path], reset: bool = False) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {**self._fresh(), "running": True, "file_total": len(paths)}
        threading.Thread(target=self._run, args=(list(paths), reset), daemon=True).start()
        return True

    def _run(self, paths: list[Path], reset: bool) -> None:
        try:
            if reset:
                self.store.reset()
                self.bm25.invalidate()
            for i, p in enumerate(paths, start=1):
                self._set(file=p.name, file_index=i, page=0, pages=0)
                if not reset:
                    self.store.delete_source(p.name)  # aynı isimli dosya yeniden yüklenirse temiz değişim
                n = self.store.add_pdf(p, on_progress=lambda pg, tot: self._set(page=pg, pages=tot))
                self.bm25.invalidate()
                with self._lock:
                    self._state["done"].append({"file": p.name, "chunks": n})
        except Exception as e:  # noqa: BLE001
            self._set(error=f"{type(e).__name__}: {e}")
        finally:
            self._set(running=False, file=None, finished_at=time.time())
