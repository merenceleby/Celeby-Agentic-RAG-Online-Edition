import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    # --- LLM (Gemini API üzerinden Gemma / Gemini) ---
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gemma-4-31b-it")  # AI Studio'dan adı doğrula
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")  # 503/yoğunlukta otomatik yedek model
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    # --- Free tier bütçesi: gerçek limitin altında tut ---
    llm_rpm: int = _i("LLM_RPM", 9)       # dakikada en fazla istek (limit 10 ise 9)
    llm_rpd: int = _i("LLM_RPD", 1400)    # günlük (Pasifik saatiyle gece yarısı sıfırlanır)
    llm_tpm: int = _i("LLM_TPM", 0)       # dakikada token limiti; 0 = kapalı (AI Studio'da varsa gir)
    llm_est_tokens: int = _i("LLM_EST_TOKENS_PER_CALL", 3500)  # çağrı başına tahmini token

    # --- Local modeller (API kotası harcamaz, TR+EN çok dilli) ---
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    reranker_model: str = os.getenv("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")

    # --- RAG ---
    chunk_size: int = _i("CHUNK_SIZE", 1000)        # KARAKTER
    chunk_overlap: int = _i("CHUNK_OVERLAP", 200)   # KARAKTER
    top_k_retrieval: int = _i("TOP_K_RETRIEVAL", 30)
    top_k_bm25: int = _i("TOP_K_BM25", 20)
    top_k_rerank: int = _i("TOP_K_RERANK", 8)
    min_rerank_score: float = float(os.getenv("MIN_RERANK_SCORE", "0.15"))
    neighbor_window: int = _i("NEIGHBOR_WINDOW", 1)
    max_context_chars: int = _i("MAX_CONTEXT_CHARS", 12000)

    # --- Agent ---
    max_correction_attempts: int = _i("MAX_CORRECTION_ATTEMPTS", 1)
    num_query_variations: int = _i("NUM_QUERY_VARIATIONS", 3)

    # --- Cache ---
    enable_cache: bool = os.getenv("ENABLE_CACHE", "true").lower() == "true"
    cache_ttl: int = _i("CACHE_TTL", 3600)
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # --- Yollar ---
    docs_dir: Path = BASE_DIR / "data" / "documents"
    chroma_dir: Path = BASE_DIR / "chroma_db"
    state_dir: Path = Path(os.getenv("STATE_DIR", BASE_DIR / "state"))
    usage_db: Path = Path(os.getenv("STATE_DIR", BASE_DIR / "state")) / "usage.db"  # LLM kota sayacı
    app_db: Path = Path(os.getenv("STATE_DIR", BASE_DIR / "state")) / "app.db"  # sohbet, feedback, metrikler
    max_upload_mb: int = _i("MAX_UPLOAD_MB", 50)

    # --- Lite Eval ---
    eval_dir: Path = BASE_DIR / "data" / "eval"
    eval_support_threshold: float = float(os.getenv("EVAL_SUPPORT_THRESHOLD", "0.5"))

    # --- Giriş / Auth (lokalde kapalı, deploy ederken AUTH_ENABLED=true yap) ---
    auth_enabled: bool = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    auth_username: str = os.getenv("AUTH_USERNAME", "")
    auth_password: str = os.getenv("AUTH_PASSWORD", "")
    auth_secret: str = os.getenv("AUTH_SECRET", "")
    auth_max_attempts: int = _i("AUTH_MAX_ATTEMPTS", 2)  # 2'den fazla yanlışta (3. yanlışta) ban
    auth_session_days: int = _i("AUTH_SESSION_DAYS", 7)


settings = Settings()
settings.state_dir.mkdir(parents=True, exist_ok=True)
