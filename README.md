# Celeby Agentic RAG: Online Edition

A self-correcting, multilingual Agentic RAG system built for the **Gemini API free tier** (configured with `gemini-3.5-flash-lite`, compatible with other Gemini and Gemma models).
Embeddings, hybrid retrieval, and cross-encoder re-ranking run locally on your machine — only the language model calls go online, guarded by a built-in rate/quota budget.

## How it works

```
Question (+ chat history) → rewrite (3 variations, multilingual) → hybrid search (vector + BM25) → RRF
                          → cross-encoder re-rank → overlap dedupe → ±1 neighbor context expansion
                          → generate answer → validate against sources → retry once if unsupported
                          → answer + cited-first sources
```

| Mode | Model calls | What it does |
|------|-------------|--------------|
| **Quality** | 3 (up to 5 with a retry) | Rewrites the question (resolving follow-ups and multilingual terms), runs hybrid search (vector + BM25) + RRF + cross-encoder re-ranking, answers with neighbor-expanded context, then validates the answer against sources. |
| **Fast** | 1 | Runs local hybrid search (vector + BM25) + RRF + cross-encoder re-ranking, then streams the answer token-by-token. Quick, not self-verified. |

There is no direct-to-LLM mode: every answer is grounded in your indexed documents.

## Key RAG & UX features

- **Multilingual local retrieval (TR + EN):** Uses `intfloat/multilingual-e5-small` (512 tokens, with automatic `query:` / `passage:` prefixes) and `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` — both baked into the backend Docker image at build time for instant startup.
- **Smart chunking & Small-to-Big context:** Paragraph- and sentence-aware chunking (`1000` chars, `200` overlap) with cross-page tail carry so sentences spanning page breaks are preserved. Search runs on sharp individual chunks while the LLM receives `±1` neighbor-expanded context; source cards display the exact matched passage.
- **Overlap deduplication & noise filtering:** Drops near-duplicate overlapping chunks and filters low-scoring re-ranker noise before prompting.
- **Citation-safe chat history:** Keeps the last 4 turns within the same chat (fully isolated across different chats) and strips prior `[n]` markers from history so follow-up questions never confuse new citations.
- **Cited-first Sources UI:** Sources appear only after the answer finishes, collapsed by default, with cited passages listed first, an `Other retrieved sources` divider below, and clickable inline citation pills (including multi-citations like `[1, 2, 4]`).
- **Resilient LLM client:** Automatic exponential backoff with jitter on transient `503` / `500` spikes, native `application/json` output for Gemini models, and optional `LLM_FALLBACK_MODEL`.

## Free tier budget

All model calls go through one gate (`services/rate_limiter.py`):

- Sliding one-minute window (`LLM_RPM`) and a persistent daily counter (`LLM_RPD`) that resets at midnight Pacific time.
- Optional per-minute token limit (`LLM_TPM`), estimated per call.
- A question reserves all the calls it needs **before** it starts. If the budget is not there, the API answers `429` with `retry_after` and the UI shows a countdown. Nothing is half-run.
- The retry step is optional: if the budget cannot cover it, the answer is returned marked as *unverified*.
- Identical Quality questions are served from Redis cache and cost no model calls.

Check the live limits for your project in [Google AI Studio](https://aistudio.google.com/rate-limit) and set `LLM_MODEL`, `LLM_RPM`, `LLM_RPD` (and optionally `LLM_TPM` / `LLM_FALLBACK_MODEL`) in `.env`.

## Privacy

Questions, recent same-chat turns, and the text passages sent as context **leave your machine** and go to Google's API. This edition is not suitable for confidential or regulated documents. Review Google's Gemini API free-tier terms before uploading sensitive files.

## Quick start (Docker)

Everything runs in containers, so nothing is installed on your computer except Docker.
Requirements: Docker with Compose, and a Gemini API key from Google AI Studio.

```bash
cp .env.example .env          # set GEMINI_API_KEY, LLM_MODEL, LLM_RPM, LLM_RPD
docker compose up -d --build
```

Open **http://localhost:5173**. API docs are at http://localhost:8000/docs.

The first backend build downloads the local embedding and re-ranker models (~940 MB total) into the image layer so subsequent code rebuilds stay fast and container startups are immediate. Upload a PDF (indexing runs in the background with a progress bar) and ask a question.

```bash
docker compose logs -f backend      # watch the backend
docker compose up -d --build        # rebuild after changing code
docker compose down                 # stop (data is kept)
docker compose down -v              # stop and delete the index, chat history and metrics
```

PDFs live in `backend/data/documents` on your computer. Files you copy there by hand show up on the Documents panel after you choose **Index new files**. The vector index, chat history, and metrics are stored in Docker volumes.

### Development mode

```bash
docker compose -f docker-compose.dev.yml up
```

The backend reloads when you edit Python files and the frontend runs the Vite dev server with hot reload. Use it instead of the normal compose file, not together with it.

### Running without Docker

Backend: `cd backend && pip install -r requirements.txt && uvicorn main:app --reload`.
Frontend: `cd frontend && npm install && npm run dev` (proxies `/api` to localhost:8000).

## Lite Evaluation (no model calls)

The Admin page has a Lite Evaluation section that costs nothing on the free tier:

- **Retrieval check.** Give it test questions with the expected source and page and it reports Hit@1, Hit@3, Hit@5, and MRR for vector-only search vs. hybrid search with re-ranking. Questions live in `backend/data/eval/questions.json`:
  ```json
  [{"question": "Who is Nagini?", "source": "book.pdf", "pages": [651]}]
  ```
  Writing them by hand is the most reliable. The Generate button can also draft questions from random passages (one model call per question, limited by your per-minute budget).
- **Answer checks.** For the answers saved in your chats it estimates *grounding* (how many answer sentences are close to a cited passage), *relevancy* (answer vs question), and *citation validity* (do the `[n]` markers point to real sources), using the local embedding model.

These are proxies, not an LLM judge. They highlight answers worth re-reading rather than proving correctness.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q          # fast: uses fake services, no models or Docker needed
ruff check .
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/query` | Answer a question (`mode`: `quality` or `fast`, optional `chat_id`) |
| POST | `/api/query-stream` | Fast mode as a server-sent event stream (`chat`, `sources`, `token`, `done`, `error`) |
| GET | `/api/quota` | Calls used this minute and today |
| GET / DELETE | `/api/chats`, `/api/chats/{id}` | Chat history |
| POST / GET / DELETE | `/api/feedback` | Rate answers, list feedback, reset |
| GET / DELETE | `/api/metrics`, `/api/metrics/recent` | Usage and quality figures, reset |
| GET / POST / DELETE | `/api/documents`, `/api/upload`, `/api/index`, `/api/index/status` | Manage PDFs and background indexing |
| GET / POST | `/api/eval/questions`, `/generate`, `/retrieval`, `/answers`, `/latest` | Lite Evaluation |
| GET | `/api/health` | Health check and model readiness |

Errors: `429` rate limited (`kind`: `minute`, `day` or `provider`, plus `retry_after`), `409` nothing indexed or indexing already running, `502` model error.

## Project structure

```
backend/
  main.py  config.py  requirements.txt  requirements-dev.txt  pytest.ini  ruff.toml
  api/routes.py
  services/
    agent.py          LangGraph agent (Quality), Fast streaming, chat history
    llm.py            Gemini API client with retry/backoff, fallback & JSON mode
    rate_limiter.py   per-minute, per-day and token budget with reservations
    retrieval.py  vector_store.py  bm25_search.py  reranker.py  indexer.py
    evaluation.py     Lite Evaluation
    cache.py  chat_history.py  feedback.py  metrics.py  db.py
  tests/              pytest suite (fake services)
  data/documents/     your PDFs
  data/eval/          evaluation questions
  Dockerfile
frontend/             React + Vite, served by nginx in Docker
  Dockerfile  nginx.conf
docker-compose.yml    backend + frontend + redis
docker-compose.dev.yml   hot-reload development setup
```

## Known limitations

- Lightweight free-tier models can still make mistakes, and the validation step is a second opinion from the same model, not proof. Check the cited pages for anything important.
- Google can adjust free-tier RPM/RPD limits dynamically per project.
- The app is built for single-user / personal use. A public deployment would need per-user auth and rate limits.
- Indexing large PDFs on CPU can take several minutes. The optional token limit is estimated per call, not measured.

## License

Apache 2.0
