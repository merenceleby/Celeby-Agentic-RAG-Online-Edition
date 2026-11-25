import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import router
from config import settings
from services.agent import NotIndexedError, RAGAgent
from services.auth import COOKIE_NAME, AuthService
from services.bm25_search import BM25Index
from services.cache import Cache
from services.chat_history import ChatHistory
from services.db import init_db
from services.feedback import Feedback
from services.indexer import Indexer
from services.llm import GemmaClient, LLMError
from services.metrics import Metrics
from services.rate_limiter import LLMBudget, RateLimitError
from services.reranker import Reranker
from services.retrieval import Retrieval
from services.vector_store import VectorStore

log = logging.getLogger("celeby")


def _warm_models(app: FastAPI, store: VectorStore, reranker: Reranker) -> None:
    """Embedding ve reranker modellerini açılışta indirip yükler (ilk soru yavaş kalmasın)."""
    try:
        store.warmup()
        reranker.warmup()
    except Exception:  # noqa: BLE001
        log.exception("Model warm-up failed; models will load on first use")
    finally:
        app.state.warmup["ready"] = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    store, reranker = VectorStore(), Reranker()
    bm25 = BM25Index(store)
    budget = LLMBudget(settings.llm_rpm, settings.llm_rpd, settings.usage_db,
                       tpm=settings.llm_tpm, est_tokens=settings.llm_est_tokens)
    llm = GemmaClient()
    app.state.store, app.state.bm25, app.state.budget, app.state.llm = store, bm25, budget, llm
    app.state.history, app.state.metrics, app.state.feedback = ChatHistory(), Metrics(), Feedback()
    app.state.auth = AuthService()
    app.state.indexer = Indexer(store, bm25)
    app.state.agent = RAGAgent(llm, Retrieval(store, bm25, reranker), budget, Cache())
    app.state.warmup = {"ready": False}
    threading.Thread(target=_warm_models, args=(app, store, reranker), daemon=True).start()
    yield


PUBLIC_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}

app = FastAPI(title="Celeby Agentic RAG: Online Edition", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    if (
        settings.auth_enabled
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path not in PUBLIC_PATHS
    ):
        svc = getattr(request.app.state, "auth", None) or AuthService()
        if not svc.verify_token(request.cookies.get(COOKIE_NAME)):
            return JSONResponse(status_code=401, content={"error": "unauthorized", "message": "Giriş yapmanız gerekiyor."})
    return await call_next(request)


app.include_router(router)


@app.exception_handler(RateLimitError)
async def _rate_limited(_: Request, e: RateLimitError):
    return JSONResponse(status_code=429, headers={"Retry-After": str(e.retry_after)},
                        content={"error": "rate_limited", "kind": e.kind,
                                 "retry_after": e.retry_after, "message": e.message})


@app.exception_handler(NotIndexedError)
async def _not_indexed(_: Request, e: NotIndexedError):
    return JSONResponse(status_code=409, content={"error": "not_indexed", "message": str(e)})


@app.exception_handler(LLMError)
async def _llm_error(_: Request, e: LLMError):
    return JSONResponse(status_code=502, content={"error": "llm_error", "message": str(e)})


@app.get("/api/health")
async def health(request: Request):
    warm = getattr(request.app.state, "warmup", {"ready": True})
    return {"status": "ok", "model": settings.llm_model, "models_ready": warm["ready"]}
