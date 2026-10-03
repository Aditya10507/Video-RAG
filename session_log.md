# Session Log — 2026-09-23

## 1. Channel ingest 404 (`catalog_unavailable` / `URL returned no usable metadata`)

**Symptom**
- Backend log: `[youtube:tab] Career_with_yogita/videos: Unable to download API page: HTTP Error 404`
- Job: `catalog_unavailable`, frontend: `Indexing failed: URL returned no usable metadata`

**Root cause**
- `backend/src/video_rag/core/url_parsing.py:143` parses `@Career_with_yogita/videos` to `CHANNEL, channel_ref="Career_with_yogita"` (strips `@`, ignores tab — intended so `@foo` and `@foo/videos` share `course_id`).
- `backend/src/video_rag/adapters/youtube_catalog.py:82 _build_target_url()` rebuilt it as `https://www.youtube.com/Career_with_yogita/videos` (missing `@`). Same for `/channel/UC...` → `/UC.../videos`, `/c/`, `/user/`.
- yt-dlp 404s; with `ignoreerrors=True` it returns `None`, so `_extract_info()` raises `CatalogUnavailableError("URL returned no usable metadata")`.

**Fix — `backend/src/video_rag/adapters/youtube_catalog.py:82`**
- `CHANNEL` now passes `source_url` through untouched (scheme-normalized), preserving `@handle` / `/channel/` / `/c/` / `/user/`.
- Appends `/videos` only when no explicit tab (`/videos`, `/shorts`, `/streams`, `/live`, `/featured`, `/playlists`, `/community`, `/about`) is present, so bare `@foo` still lists uploads via flat extraction.
- Removed now-unused `_CHANNEL_URL_BASE`.
- Verified: `@Career_with_yogita/videos` → same URL (was `/Career_with_yogita/videos` → 404); `/channel/UC...` preserved; video/playlist paths unchanged.

## 2. YouTube Data API v3 catalog provider (new, opt-in)

**Scope decision:** API key alone can replace listing/titles/durations but CANNOT download captions for videos you don't own (`captions.download` needs OAuth) and CANNOT serve audio. So Data API covers catalog only; transcripts (`youtube-transcript-api`) and ASR audio (`yt-dlp`) are unchanged.

**New file — `backend/src/video_rag/adapters/youtube_data_api.py`**
- `YoutubeDataApiCatalogProvider(api_key, max_videos, timeout_seconds)` implements `CatalogProvider` via `httpx` (no new dependency):
  - `VIDEO`: `videos.list` → title, `parse_iso8601_duration()` (`PT12M43S` → 763), `liveBroadcastContent`.
  - `PLAYLIST`: `playlists.list` (title) + `playlistItems.list` paginated (skip `Deleted/Private video`) + batched `videos.list` (50 ids/call) for durations.
  - `CHANNEL`: `channels.list` by `id=` (`/channel/UC...`), `forHandle=@...` (`@handle`, then `/c/` retry), `forUsername=` (legacy `/user/`), `search.list` (100 quota units, last resort for `/c/`) → uploads playlist → same expansion as playlist.
  - `_get()` maps 400/403 key errors, quota-exceeded, and private/deleted/region-locked to `CatalogUnavailableError` with actionable messages.
- `build_data_api_catalog(settings)` validates the key is set.

**Config — `backend/src/video_rag/config.py`**
- New `catalog_backend: str = "ytdlp"` (`ytdlp` | `youtube_data_api`) and `youtube_api_key: str | None = None`.
- `from_env()`: `VIDEO_RAG_CATALOG_BACKEND`, `VIDEO_RAG_YOUTUBE_API_KEY`.
- `__post_init__()`: `youtube_data_api` without a key raises `ConfigurationError`.

**Wiring — `backend/src/video_rag/container.py`**
- New `_build_catalog_provider(settings)`; `youtube_data_api` → `build_data_api_catalog()`, else `YtDlpCatalogProvider`. Logs `Catalog backend: youtube_data_api`.

**Env — `.env.example`, `.env`**
- Created `.env` from `.env.example` (did not exist).
- Added `VIDEO_RAG_CATALOG_BACKEND` + `VIDEO_RAG_YOUTUBE_API_KEY` to both.
- Current `.env`: `CATALOG_BACKEND=youtube_data_api`, `YOUTUBE_API_KEY=<user-pasted key, not recorded here>`.

## 3. Backend restart

- Old server on `:8000` was PID `4620` (`C:\...\Python311\python.exe`); stopped via `Stop-Process -Id 4620 -Force`.
- `cli config` confirmed `catalog_backend: youtube_data_api`, `youtube_api_key: ***` (masked).
- Restarted detached: `PYTHONPATH=backend/src VIDEO_RAG_FRONTEND_DIR=frontend .venv\Scripts\python.exe -m video_rag.cli serve` → PID `17176` listening on `127.0.0.1:8000`.
- Startup log confirms `Catalog backend: youtube_data_api`, `Container ready (memory/hashing/lexical, transcripts: captions)`. Logs: `data\backend.log`, `data\backend.err.log`.

## 4. Verification

- `backend/tools/run_tests.py`: **91 passed, 0 failed**.
- `ruff check` on `youtube_data_api.py`, `config.py`, `container.py`, `youtube_catalog.py`: clean (fixed one `F821` walrus-scope bug in `_fetch_videos` by rewriting as an explicit loop).
- Manual `_build_target_url` check for `@handle`, `/channel/`, `/c/`, `/user/`, bare-host, video, playlist URLs.
- `parse_iso8601_duration`: `PT12M43S→763`, `PT1H1M1S→3661`, `PT45S→45`, `P1DT2H→93600`, invalid→0.

## 5. Deliberately NOT changed

- Transcripts/timestamps pipeline (`youtube_captions.py`, `whisper_asr.py`, `transcript_cleaning.py`, `chunking.py`, `timestamps.py`, `ranking.py`) — Data API key cannot replace these.
- `url_parsing.py` channel_ref stripping (keeps stable `course_id`).
- Frontend (polls `/jobs`, no SSE change needed).
- `requirements.txt` (uses existing `httpx`).

---

# Session Log — 2026-09-25

## 1. Project walkthrough + backend start

- Explored the repo (backend `core/` + `adapters/` + `ingest/` + `ask/` + `api/`, 3-file frontend, `docs/ARCHITECTURE.md`) and explained it in simple terms.
- Started API+frontend detached on `127.0.0.1:8000` (`PYTHONPATH=backend/src`, `VIDEO_RAG_FRONTEND_DIR=frontend`). Links: `/` app, `/docs`, `/health`.

## 2. LLM simplified — fallback removed, key required

- `adapters/llm.py`: deleted `DisabledLanguageModel`, retries/backoff. One `LanguageModel` class doing direct `POST {base}/chat/completions` (`complete()` + `complete_json()`); `build_language_model()` fails fast without `VIDEO_RAG_LLM_API_KEY`.
- `ask/service.py`: `_synthesize()` and `_verify_explanation()` always call the LLM; deleted `_extractive_answer()`, `is_enabled` checks, try/except fallbacks.
- `core/ports.py`: removed `is_enabled` from `LanguageModel` protocol. `config.py`: removed `llm_enabled` (LLM documented as required). `text_processing.py`: terminology refiner no longer gated on `is_enabled`.
- `fakes.py` (`StubLanguageModel`), `tests/offline.py`, `cli.py` smoke now use the stub; `api/schemas.py` + `routes.py`: `/health` returns `llm_model` instead of `language_model_enabled`; `.env.example` updated.
- Verified: `pytest` + stdlib runner green, `smoke: PASSED`. Old server (PID 8772) stopped; new code needs a key or startup raises `ConfigurationError`.

## 3. Groq key wired (`gsk_…`, full value only in local `.env`)

- `.env`: `LLM_BASE_URL=https://api.groq.com/openai/v1`, key set as given.
- Probed `GET /models` with the key: 11 models visible; `llama-3.3-70b-versatile` → 404 (retired); `openai/gpt-oss-120b/20b`, `allam-2-7b` → 403 `model_permission_blocked_project`. Only `qwen/qwen3.8-27b` → 200, so `LLM_MODEL=qwen/qwen3.8-27b`.
- Verified live LLM call returns `LLM-OK`; server restarted (PID 3952, then 4292) and `/health` shows the model.

## 4. Debugged "variable" question refused on Python video

- Registry had 3 single-video courses: `video:kqtD5dpn9C8` (72 healthy chunks), `video:q3AuP01daL4` (72 chunks but mojibake `?? ????` text — caption decoding broken), `video:K5KVEU3aaeQ` (0 chunks, embedding failed).
- Retrieval probe: good course answers (`0.68 @ 10:49` for "what is a variable in python"); short "what is variable" scores ~0.23 (gray zone, LLM-verified up to answered). User had asked the 0-chunk course → honest `not_covered, top 0.0`.
- Told user to ask `video:kqtD5dpn9C8` and use fuller questions (thresholds `0.55/0.15` are placeholders).

## 5. Storage model confirmed (permanent, not temporary)

- SQLite `data/registry.db`: courses/videos metadata only. Qdrant Cloud collection `chunks`: 1024-dim vectors + payload (text, sentences, timestamps). Both survive restarts. Only job records are ephemeral (`JOB_RETENTION_SECONDS=3600`).

## 6. Library feature — list indexed courses/videos, search one video

- Backend: `CourseRegistry.list_courses()` / `list_course_videos()` (+ `sqlite_registry.py` SQL); `VectorStore.count_chunks()` + optional `video_id` filter on `search_dense`/`search_keyword` (memory + Qdrant `_scope_filter`); `QueryService.ask(..., video_id=None)` with membership check; `GET /courses`, `GET /courses/{id}/videos`; `AskRequest.video_id`; CLI `ask -v/--video`.
- Frontend: course buttons + per-course video chips, `video-id` input sent with `/ask`.
- Tests: `test_library.py` (listing + counts) + 2 video-scope tests in `test_ask_service.py` → **122 passed, 0 failed**; ruff clean on touched files; one new mypy invariance note in `vector_store_qdrant.py:295` fixed via `list[Any]` annotation.

## 7. Cache folders

- Deleted `.\.ruff_cache`, `.\backend\.ruff_cache`, `.\backend\.pytest_cache` (regenerated on next run). Explained `.mypy_cache`; left in place.

## 8. Enterprise sidebar shell + Home/Library views

- `index.html`: `#app-shell` with empty `<aside id="sidebar">` (collapse `«`, drag `#sidebar-resizer`, expand `»`), CSS vars `--sidebar-width/min/max`, `localStorage` persistence, `Esc` to collapse, overlay mode under 760px.
- Added nav (Home first/default, Library second). Home = vertical 2-col grid: left `1. Index a course` + watch card below (sources/timestamps/player/covers); right Ask column (revealed after first chunks land, or on reload if chunks exist) + answer-text card. Library = real list rows (`● title … i/n videos · N chunks … >`), expandable videos with status/chunks + `Ask >` jumping home with selection.
- Fixed self-introduced bug: `main { display:flex }` overrode `[hidden]` so both views rendered — added global `[hidden] { display:none !important; }` guard.
- `frontend/README.md` updated. Verified: HTML parses, `node --check` clean, live page contains all new IDs, no stale references.

## 9. Cross-course isolation (Q&A, no code change)

- Confirmed by code + live probe: retrieval hard-filters `course_id` (Qdrant filter / memory check), so asking course A about course B's topic refuses (`NOT_COVERED`) or at most `PARTIAL` — never cites the other video. Same-video-in-two-courses dedup edge noted.

## 10. Channel ingest `channel:Career_with_yogita` — 79/141 indexed, 62 failed

- Log breakdown: 57× `transcript_unavailable` + 5× `embedding_failed` (Jina 429/credit).
- Correction after deeper look: system DOES use autogenerated captions (Hindi `auto_captions` fetched, e.g. 240–421 cues). 59 videos hit `Caption request throttled` late in the job; retries exhausted, yt-dlp fallback then `Subtitle download failed` — same throttle wall. So most failures are retryable, not caption-less.
- Advice given: re-run ingest after cooldown (indexed are skipped), lower `INGEST_CONCURRENCY` 6→3 for big channels.

---

# Session Log — 2026-09-26

## 1. Backend + frontend started

- Verified `.venv`, `.env`, `frontend/index.html` present; port 8000 free; `cli config` confirmed `catalog_backend: youtube_data_api`, `vector_backend: qdrant` (Cloud), `llm: qwen/qwen3.8-27b` via Groq (secrets masked).
- Started detached (`PYTHONPATH=backend/src`, `VIDEO_RAG_FRONTEND_DIR=frontend`, logs to `data/backend.log|err.log`); `/health` 200 `ok`, `/` serves the page.
- Links: app <http://127.0.0.1:8000>, docs <http://127.0.0.1:8000/docs>, health <http://127.0.0.1:8000/health>. Server restarted twice more during the session (language feature, language refactor); health re-verified each time.

## 2. Frontend cleanup (copy removal, selection lock, layout)

- `frontend/index.html`: removed index hint (`Paste a YouTube playlist...`), ask hint (`Answers come only...`), header tagline (`Ask a question...`), and the whole `topbar-right` block (`#health` badge + `#api-key` input, per screenshot).
- `frontend/app.js`: `request()` now reads the key via `getApiKey()` (input if present, else `sessionStorage`, so protected deployments still auth silently); `refreshHealth()` and key wiring are null-guarded no-ops when the elements are absent.
- `frontend/styles.css`: `body { user-select: none }`; text re-enabled only for `#answer-text`, `#answer-message`, `#covers-list`, `input`, `textarea` (RAG answers copyable, all chrome not). Topbar collapsed to a single title row; deleted `.tagline/.topbar-right/.key-input` + their media-query rules; `.card h2` bottom margin `6px→14px` to keep rhythm without hints.
- Verified: `node --check` clean, HTML parses, no stale `health/api-key/tagline` references in HTML/CSS, live `/`, `/styles.css`, `/app.js` all serve the new code.

## 3. Answer language mirrors the query (Hinglish fix)

- Problem: `Python me variable ka matlab kay hai` was understood (multilingual Jina retrieval) but always answered in English — `ask/service.py` used a fixed-English synthesis prompt.
- New `backend/src/video_rag/core/language.py`: `detect_answer_language()` (Devanagari regex → `hindi`; romanised-Hindi markers → `hinglish`; else `english`), `language_directive()`, `language_label()`. Deliberately excludes `me`/`main`/`the` so `what is the main function` stays English.
- `ask/service.py`: language detected once per `ask()`, threaded into `_synthesize()` (directive appended to system prompt), `_build_refusal()` and partial message via new `_localize_message()` (English = zero-LLM fast path preserved; Hinglish/Hindi refusal spends one short translate call, falls back to English on model failure). Production reranker is multilingual Jina, so no retrieval change was needed.
- Tests: new `backend/tests/test_answer_language.py` (8 tests: detection incl. the user's example, directive contents, Hinglish synthesis directive via capturing model, English-refusal-zero-calls, Hinglish-refusal-localization). Noted while testing: the offline `LexicalReranker` dilutes long Hinglish queries (Hindi function words divide the keyword score), so synthesis tests use `hashmaps kahan use hote hain` (0.212 gray-zone → verified → covered). Suite: **130 passed, 0 failed**; ruff clean.

## 4. Language refactor — generic mirror, trimmed markers (user suggestion)

- Agreed: a generic `Reply in the same language and script as the Question` covers every language with no lists; kept one explicit sentence each for Hinglish-Roman and Hindi-Devanagari (the confirmed failure mode — English excerpts otherwise pull the model back to English) plus the tiny detector to preserve the English zero-call refusal path.
- `_STRONG_MARKERS` 80→15 words; all three directives now open with the generic mirror sentence. Detection-test expectations updated (`english` directive no longer asserts exact `Reply in English.`).
- Suite still **130 passed, 0 failed**; ruff clean; backend restarted, `/health` 200.

## 5. Qdrant multi-cluster Q&A (no code change)

- Asked: several free Qdrant clusters as mirrored backup VectorDBs for parallel ops. Answered: technically possible (`ReplicatedVectorStore` over N stores, fan-out writes, primary/round-robin reads; deterministic point IDs make backfill trivial) but recommended against — free tier is one cluster, writes get slower (slowest wins + drift repair), single-student QPS has no read bottleneck (~25ms). Recommended Cloud snapshots + `registry.db` backup instead; self-hosted Qdrant in `docker-compose.yml` if quota ever bites.

## 6. Project cleanup (~23 MB)

- Audited sizes: `.venv` 240 MB (runtime — kept), `.mypy_cache` 23.1 MB, `backend/` 1.3 MB, `data/` 0.1 MB (`registry.db` 88 KB — kept, plus 0 KB logs), `docs/` 0.1 MB. No dead source modules found (all `container.py`/`cli.py` imports traced).
- Deleted: `.mypy_cache/`, `backend/.ruff_cache/`, 19× `__pycache__`/`pytest_cache` dirs under `backend/`. Left `.venv`, `registry.db`, logs (server-running + empty). Server still `HEALTH_200` after.

## 7. Archify diagram + README import

- Installed `tt-a1i/archify` skill (`npx skills add tt-a1i/archify -g` → `~\.agents\skills\archify`, v2.17) and diagrammed the requested flow as a `sequence` diagram: Browser `GET /profile` → API → Redis `GET` → miss → PostgreSQL `SELECT` → rows → `SET + TTL` fill → `200 JSON`, with activation bars, phase bands, miss/hit-path cards.
- Workflow: authored `web-request-cache-miss.sequence.json`, `validate --quality showcase` passed 9/9 after one spacing repair; `deliver` froze spec (2,571 B) + artifact (~805 KB); `visual-check` passed at 1440×900–2048×1320 light/dark only after widening viewBox 820→1080 `spread` (narrow frame scaled 1.6× and overflowed; 1360 broke the 6px sublabel floor — 1080 is the proven sweet spot).
- Imported for GitHub push: `docs/diagrams/web-request-cache-miss.png` (1440 light capture, renders inline), `.html` (interactive), `.sequence.json` (reproducible spec); `README.md` gained a `## Diagrams` section + `diagrams/` folder-map entry. Noted in README that GitHub won't render the HTML inline (use local open or Pages). Nothing committed — user pushes.

---

# Session Log — 2026-09-27

## 1. Full codebase read + missing backend found

- Read every file line-by-line (frontend, docs, config, scripts, logs). `backend/` held only `Dockerfile`, `pyproject.toml`, `requirements*.txt` — `backend/src/video_rag/`, `tests/`, `tools/`, `eval/` all missing (0× `*.py` outside `.venv`). No installed `video_rag` package, nothing on `:8000`.
- Reconstructed project state from `README.md`, `docs/ARCHITECTURE.md` (4 layers, contracts, gate), `session_log.md` history (channel-404 fix, Data API catalog, LLM-required refactor, library, sidebar, Hinglish mirror, 130 green), plus live `.env` (Jina/Qdrant Cloud/Groq/YouTube keys).

## 2. Cleanup audit (no deletions)

- Safe to delete: `data/backend.log`, `data/backend.err.log` (regenerable, gitignored), `docs/diagrams/web-request-cache-miss.*` (~805 KB, unrelated Redis/Postgres demo stack vs real Qdrant/SQLite), `session_log.md` itself (dev scratch, not gitignored). Keep: `data/registry.db` (real index — delete only via `reset-data`), `.env` (live secrets), `.venv/`.

## 3. Streaming explained + frontend smooth reveal

- Streaming in this RAG = 3 uses: job progress (`GET /jobs/{id}/stream` SSE, frontend polls instead — `EventSource` can't send `X-API-Key`), LLM answer tokens, YouTube embed `?start=`.
- Implemented in `frontend/app.js`: `streamTextSmooth()` (rAF, adaptive `0.9–4.5 s`, ease-out, word-boundary snap, token-cancel, reduced-motion instant), `tryTrueStream()` (`POST /ask/stream`, `data: {"token"}` → `data: {"result"}` → `[DONE]`, fallback to `POST /ask`), `renderAnswerStreamed()` + `stopActiveStream()`. Verified `node --check`, HTML parse.

## 4. Streaming graphics added, then removed per feedback

- Added then fully removed: aurora gradient border, `stream-bar` (orb/track/shimmer), neon caret, breathing badge, `reveal-in` cascade (`index.html`, `styles.css`, `app.js` visual toggles). `grep` confirms zero `stream-bar|is-streaming|badge-live|reveal-in|answer-stream` refs left. Streaming kept as plain text reveal.

## 5. Temporary `stream_server.py` (created, verified, deleted)

- Built standalone FastAPI server (SQLite registry + Qdrant Cloud 545 pts + Jina-1024 + Groq `qwen/qwen3.8-27b`) with real `POST /ask/stream` SSE. Verified: `answered top 0.48`, 179 tokens streamed. Frontend consumed it live.
- User: no new file for a small feature — correct, streaming is just `stream:true` in one function. Kept only because `backend/src` was missing; deleted once the real backend was rebuilt (`Test-Path stream_server.py` = False, `:8000` still healthy).

## 6. Backend deletion investigation

- No `.git`, Recycle Bin empty for video files, no second copy on disk, Docker daemon down (no image to extract), no `.pyc` (cleaned 09-26), PowerShell history shows no `video-rag` delete (only `aion-bot`). `run.ps1 clean` deletes caches only; `reset-data` deletes `data/` only. Deletion happened after 09-26 (130-green still traced then). Cause undetermined — likely manual Explorer/VS Code delete, OneDrive sync, or AV quarantine. Original unrecoverable; rebuilt equivalent below.

## 7. Full backend rebuild (no new files for streaming)

- Recreated `backend/src/video_rag/`: `config.py` (+`.env` autoload via `dotenv`), `errors.py`, `logging_config.py`, `container.py` (catalog/embedder/store/reranker/LLM/registry wiring, Jina reranker when key present), `core/` (models, ports incl `stream_complete`, url_parsing with channel fix, chunking, transcript_cleaning, timestamps, ranking + plural stemming `hashmaps→hashmap/used→use`, fusion RRF, coverage gate, language mirror), `adapters/` (youtube_catalog, youtube_data_api + `parse_iso8601_duration`, youtube_captions, ytdlp_subtitles, text_processing, embeddings Jina+Hashing, reranker lexical+Jina, vector_store_memory+qdrant with `video_id` filter + `count_chunks`, sqlite_registry with `list_courses`/`list_course_videos`, llm `LanguageModel.complete/complete_json/stream_complete` + `build_language_model` fail-fast, fakes), `ingest/service.py`, `ask/service.py` (`ask(question, course_id, video_id)`, `ask_stream()` generator yielding `token`/`result`, verify-explain, localized refusal, video aggregation `max + λ·log`), `api/` (app factory + security + jobs + schemas with `video_id` + routes incl `POST /ask/stream` SSE via `service.ask_stream`), `cli.py` (smoke/serve/ask `-v`/ingest/config), `tests/` (6 files), `tools/run_tests.py` (tmp_path-aware), `eval/run_eval.py` stub.
- Streaming = method, not file: `llm.stream_complete(stream: True)` → `ask_stream()` → `routes.ask_stream`. Matches frontend `tryTrueStream` contract.
- Fixes during verify: lexical stemming (long English query scored 0.0 → covered), test thresholds `0.1/0.05` for offline data (prod `0.55/0.15` untouched), `run_tests.py` fixture handling, Hindi test via real Devanagari string, `.env` autoload (server failed `LLM_API_KEY required` + `ytdlp` default before fix).
- Green: `pytest backend/tests` **25 passed**, `run_tests.py` green, `cli smoke` PASSED (covered + refused). Ruff: functional clean; remaining notes are style-only (`B008` idiomatic FastAPI `Depends`, `S324/S110` non-security hashes/blanket excepts, `E501` long lines).
- Live: stopped temp server, `cli serve` on `:8000` — `/health` ok (qdrant, `qwen/qwen3.8-27b`), `/` + `/app.js` 200 with streaming client, `/ask` + `/ask/stream` verified `STREAM_OK` (Jina reranker: `partial top 0.29` → streamed `answered`, 91 tokens).

---

# Session Log — 2026-10-01

## 1. Working agreement reset (user feedback)

- User clarified the intended workflow twice: (1) "guide me to start the backend/frontend" meant teaching, not doing it for them; (2) "check the logs and tell me the cause" meant diagnose only, no code edits. Applied from now on: **guide and explain; user runs commands; ask before any edit/restart/probe.**

## 2. Backend + frontend started, live smoke

- Port 8000 free, `.venv` + `.env` verified; `cli config` showed healthy wiring: catalog `youtube_data_api`, vector `qdrant` (Cloud), Jina embeddings, Groq `qwen/qwen3.8-27b`, secrets masked.
- Started detached (`PYTHONPATH=backend/src VIDEO_RAG_FRONTEND_DIR=frontend`, logs to `data/backend.log|err.log`); `/health` 200. `/courses` showed 4 existing courses (channel `Career_with_yogita` 401 chunks; Python videos 72 chunks each).
- Live `/ask` on `video:kqtD5dpn9C8` ("what is a variable in python") → `answered`, top 0.68, citation `10:49`.

## 3. Ingest 404: root cause + fix (approved after diagnosis)

- Symptom: frontend "Indexing failed: YouTube API :404" for video `ix9cRaBkVe0`.
- Log evidence: `GET https://www.googleapis.com/youtube/v3/videos.list?...&id=ix9cRaBkVe0 → HTTP/1.1 404`.
- Root cause: `adapters/youtube_data_api.py` `_get()` passed RPC **method names** (`videos.list`, `playlists.list`, `playlistItems.list`, `channels.list`) as the REST path; real endpoints are resource names only (`/videos`, `/playlists`, `/playlistItems`, `/channels`). Every request 404'd → `CatalogUnavailableError` → job failed. Not key/video/network related.
- Fix (user-approved): 6 call sites renamed to resource paths. Restarted server (user later began restarting it themselves).
- Re-test: catalog call now `200 OK`; job then failed at the **next** stage — Jina embeddings **HTTP 429** (897 chunks, free-tier rate limit). Unresolved by choice; options given (wait/retry, lower `VIDEO_RAG_EMBEDDING_BATCH_SIZE`, add retry/backoff to `embeddings.py`).
- Security note: `data/backend.log` logs full googleapis URLs including the YouTube API key → advised key rotation + log masking as follow-up.

## 4. PowerShell execution-policy lesson (user-run, guided)

- User's `Set-ExecutionPolicy -Scope Process RemoteSigned` still blocked `run.ps1`: policy was fine, the file carried **Mark of the Web** (`Zone.Identifier`) — under `RemoteSigned`, marked files are treated as downloaded. Explained policy ladder, MOTW, verification via `Get-Item .\run.ps1 -Stream Zone.Identifier`, and the three fixes (Unblock-File, Process Bypass, `run.cmd` wrapper).
- Permanent fix requested and applied: `CurrentUser` was **already** `RemoteSigned` in registry (user's own command had persisted it); ran `Unblock-File` across the repo (skipping `.venv`/`.git`); verified MOTW gone and `run.ps1 help` runs clean. User then did the full manual cycle themselves (Stop-Process on old server + `run.ps1 serve`).

## 5. Progress bar fix

- Symptom: video indexed and answered fine, but the ingest progress bar stayed at 5% forever.
- Root cause (two-sided): `routes.py` created jobs with `total=0` and nothing ever published the real count (only known after `catalog.expand()` inside `IngestService.ingest`); frontend `updateProgress()` rendered a fixed 5% when `total===0` and never set 100% on completion.
- Fix: `JobStore.set_total(job_id, total)` (new) called by `ingest/service.py` right after expand; frontend computes real `completed/total`, shows 100% on `succeeded` (deliberately NOT on `failed`), and a 50% activity fallback only for legacy totals.
- Verified: `node --check` clean; pytest **25 passed**; simulated job through real `JobStore` + fakes → `{total:0}` → `{total:3,completed:3}` → `succeeded`.

## 6. favicon 404 noise explained (no change)

- All 404s in user's log tail were `GET /favicon.ico` — browsers auto-request it when no `<link rel="icon">` exists; project ships no favicon. Cosmetic only. Offered optional one-liner `href="data:,"` if the noise is unwanted.

## 7. Answer-language behaviour explained (no change)

- Answer language mirrors the **question**, not the video: Devanagari → Hindi; Hinglish markers (`ka/hai/kya/kahan/...`) → Hinglish Roman; else English. Hindi video + English question → English answer over Hindi transcripts (multilingual Jina retrieval). English refusals stay zero-LLM; Hinglish/Hindi refusals cost one translate call.

## 8. Documentation audit — docs matched to the current system

- Grepped all docs for stale claims; fixed surgically, preserving each doc's voice:
  - `README.md`: quick-start now names required keys (Jina + LLM; smoke is the only offline path); API table gained `/ask/stream`, `/courses`, `/courses/{id}/videos`; test count 85→**25**; eval described as harness-to-extend; "Honest limitations" rewritten (LLM required, yt-dlp fallback parser unconfigured, hashing embedder is test-only).
  - `docs/ARCHITECTURE.md`: bumped to **v1.1 Implemented** with an explicit "Implementation status (2026-10-01)" deviation list (LLM required; plain union instead of RRF; no 3-variant query rewrite; no transcript cache; punctuation/jargon adapters off; syllabus = video titles; ingest does not write registry rows; plain-JS frontend); stack table corrected (BM25/SPLADE → lexical scoring; Qdrant via URL; plain JS); tests 85→25.
  - `docs/INTERVIEW.md`: 85→25 tests; LLM-required honesty item; captions row notes unconfigured fallback parser.
  - `docs/TROUBLESHOOTING.md`: §4 referenced nonexistent `backend/tools/check_captions.py` → replaced with a working inline probe (verified live: 9,711 words for `kqtD5dpn9C8`) + Jina-429 pointer.
  - `docs/DEPLOY.md` + `docker-compose.yml`: compose now sets `VIDEO_RAG_JINA_API_KEY` + `VIDEO_RAG_LLM_API_KEY` (backend refuses to start without) with Groq example; DEPLOY says fill keys before first start.
  - `docs/WINDOWS.md`: §7 corrected (caption API is the practical source; yt-dlp fallback parser not configured).
  - `frontend/README.md`: removed health-badge claim (UI element was removed earlier); documented SSE answer streaming + fallback, working progress bar, language mirroring.
  - `.env.example`: "everything runs offline" → corrected (two required keys; offline = tests/smoke only); LLM section marked REQUIRED with Groq example.
- Left alone deliberately: `docs/diagrams/*` (labelled generic demo), `session_log.md` history entries.
- Verified: stale-pattern re-grep → zero hits; pytest still **25 passed**.
- Known gap documented but unfixed: `IngestService` still doesn't write `courses/videos/course_videos` rows, so fresh ingests won't appear in Library until pre-existing rows cover them.

---

# Session Log — 2026-10-02

## 1. Registry writes at ingest — the known gap closed

- **Gap:** `IngestService` never wrote `courses`/`videos`/`course_videos` rows, so the Library only listed rows from earlier runs (FR-17 partial, roadmap item #1).
- **`adapters/sqlite_registry.py`** — added the write side, all idempotent upserts, UTC-stamped: `upsert_course` (status `ingesting` on insert *and* update), `upsert_video`, `link_video_to_course` (`INSERT OR IGNORE` keeps the original position on re-runs), `set_course_status`, `mark_video_status`. UPDATE-only statements no-op safely on unknown ids.
- **`core/ports.py`** — `CourseRegistry` protocol gained the five write methods (structural typing: adapters need no inheritance).
- **`ingest/service.py`** — registry is now optional (`None` default; smoke passes none). `_register_course()` runs right after catalog expansion; per-video `_mark()` moves rows `pending → indexing → indexed/failed` (error truncated to 200 chars); course set to `ready`/`failed` at the end. Every registry call is try/except+log — a registry problem can never fail the job (vectors remain the source of truth for answering).
- **`api/routes.py` `/courses`** — `indexed: 1 if n else 0` placeholder replaced with a truthful per-video count (a video counts only when its chunks exist in the store); `GET /courses/{id}/videos` already counted per-video correctly.
- **Tests** — new `tests/test_ingest_registry.py` (4: rows written, failed video marked, registry-less ingest works, re-ingest leaves no duplicate rows) + 4 in `test_library.py` (write roundtrip, idempotent upserts, status updates, unknown-video no-op); `tools/run_tests.py` includes the new module. Suite: **pytest 33 passed**, stdlib runner **33 passed**, `cli smoke` PASSED, ruff clean on all touched files (remaining project-wide notes are the pre-existing B008/E501 style set). mypy strict baseline (154 pre-existing errors) unchanged — not a project gate.
- **Docs** — `README.md` (25→33 tests), `ARCHITECTURE.md` (implementation-status bullet rewritten, tests count), `EXPLANATION.md` (§5.3 registry step, §5.7 honesty box struck, FR-16/17 now ✅, §8.12/§13.2 write side, §20 item #1 struck, roadmap #1 done, test table + counts), `INTERVIEW.md` (25→33). Stale-pattern re-grep: zero hits.
- **Deliberately not changed:** `force` re-index flag still accepted-not-honored (roadmap #2); dedup/skip-if-indexed not added; frontend untouched (Library already renders whatever `/courses` returns).

---

# Session Log — 2026-10-03

## 1. Timestamp refinement actually refines now

- **Bug (three linked defects in the citation path):** `ask/service.py::_citations` called `refine(..., "")` with an empty query, so every sentence scored 0 and the **first** sentence always won (the `-1` sentinel accepted zero-overlap). The primary chunk was picked with `lexical_score("", c.text)` — all zeros → first chunk of the winning video, not the best. Secondary citations passed the **primary** chunk's `start_sec` as the sentence time, so "also mentioned" links pointed other videos at the primary's timestamp.
- **Fix — `core/timestamps.py`** `refine()`: sentences now score via `ranking.tokens()` (stemmed + alias-normalized overlap — the same normalizer retrieval uses, so `hashmaps`↔`hashmap`, `hash map`↔`hashmap` match); the sentinel moved from `-1` to `0`, so a zero-overlap sentence never moves the timestamp and a no-match falls back to the chunk start instead of the first sentence.
- **Fix — `ask/service.py`** `_citations(question, ranked)`: takes the real question (unused `lang` param dropped; `lexical_score` import removed), picks the best chunk of the winning video **by rerank score** (`max`, no reliance on sort order), and refines secondary citations inside **their own** chunk's sentences (`c.sentences or [{t: c.start_sec, s: c.text}]`). Both call sites (`ask`, `ask_stream`) updated.
- **Tests** — new `tests/test_timestamps.py` (7: best-sentence pick, stemming, `hash map` alias, no-match fallback to chunk start, empty sentences, label format, deep link) + `test_citation_lands_on_matching_chunk_and_sentence` in `test_ask_service.py` (two chunks in one video: intro chunk @ 60s vs hashmap chunk @ 300s with matching sentence @ 420s → primary must be `417.0` / `"6:57"`; fails on the old code, which returned the intro chunk @ 57s). `tools/run_tests.py` wired for the new module. Suite: **pytest 41 passed**, stdlib runner **41 passed, 0 failed**, `cli smoke` PASSED. Ruff on touched files: only the pre-existing B905/RET504/E501 notes in `ask/service.py` (lines 35/169/177 — untouched code); new/edited regions clean.
- **Docs** — test counts 33→41 in `README.md`, `docs/ARCHITECTURE.md`, `docs/INTERVIEW.md`, `docs/EXPLANATION.md` (5 spots + §17 table row for `test_timestamps.py` + `test_ask_service.py` row extended). Dated history entries left at 33.
- **Deliberately not changed:** `also_mentioned_in` still capped at 3 and first-wins on sentence ties (earliest explanation); `ScoredChunk`/`Citation` models still unused; thresholds still placeholders (roadmap #4).

## 2. Registry schema recovery and vector-backed library restoration

- **Incident:** The Library endpoint raised `sqlite3.OperationalError: no such column: v.duration_sec`. The existing `data/registry.db` used the original `videos.duration_seconds` and `videos.updated_at` columns, while the newer registry code expected `duration_sec` and `indexed_at`. An earlier compatibility fix renamed the existing database columns; that was reversed at the user's request.
- **Schema correction — `adapters/sqlite_registry.py`:** The canonical schema and all registry SQL now use the original `duration_seconds` and `updated_at` names. Registry startup detects a database containing the newer names and renames those columns back without dropping rows. `list_course_videos()` returns the native `duration_seconds` field. Tests cover restoration from the renamed schema and creation of a fresh registry with the original names.
- **Registry recovery:** Read-only inspection found two course IDs present in Qdrant but absent from SQLite: `video:ix9cRaBkVe0` (897 points; “Python Full Course for free”) and `video:VXU4LSAQDSc` (74 points; “Learn NumPy in 1 hour!”). Restored their course, video and course-video link rows from Qdrant payload metadata so they are visible in the Library. No vector writes/deletes were made; Qdrant remained at **1,578 points**. Final SQLite counts: **6 courses, 148 videos, 148 course-video links**.
- **Library count efficiency — `core/ports.py`, vector-store adapters, `api/routes.py`:** Added a batched course/video chunk-count operation to Qdrant, memory and fake stores. The courses endpoint now counts chunks by course/video in a single paginated Qdrant scroll rather than making a separate remote count per video; the course videos endpoint also uses one batch count. Existing per-course count behavior remains available.
- **Verification:** `/courses` returned 200 with all six registered courses; `/courses/{id}/videos` returned the restored videos and their existing chunk totals (897 and 74); the channel returned 143 video rows and 463 chunks. Exact Qdrant count before/after registry recovery: **1,578**. Full pytest suite: **43 passed**. Ruff `--select E,F --ignore E501` on the changed backend files passed; the broader configured Ruff selection still reports existing warnings in touched files (including `B905`, `S324`, `B008`, and long lines in the routes module).
- **Known existing data state:** `video:K5KVEU3aaeQ` remains registered but has no matching Qdrant chunks (0 points); it was not removed or altered by this recovery.

## 3. On-demand AI answer translation

- **Feature:** Added an answer-box language picker and translate icon. The controls appear only when a generated answer is present; users can select a target language and translate the whole answer without changing its source citations.
- **API — `api/schemas.py`, `api/routes.py`:** Added a validated `TranslateRequest` and a separate `POST /translate` endpoint. The route uses the existing language-model client and configured `VIDEO_RAG_LLM_API_KEY`, with the same `X-API-Key` authentication as other protected routes. Translation prompts preserve meaning, formatting, names, numbers, timestamps and URLs; failures and empty model responses are surfaced as API errors.
- **Frontend — `index.html`, `app.js`, `styles.css`:** Added an accessible language selector, translate icon, and status/error display. The existing answer is retained as the translation source, citations remain separate, and stale translation responses cannot overwrite a newer answer.
- **Docs/tests:** Documented the endpoint and request in `README.md`. Added `tests/test_translation_api.py` covering successful translation, shared API-key authentication, blank input and model failure. Updated test totals in README and architecture/explanation/interview docs.
- **Verification:** Full pytest suite **47 passed**; `node --check frontend/app.js` passed; Ruff `--select E,F --ignore E501` passed for the changed translation backend files. One third-party Starlette/httpx `TestClient` deprecation warning remains.

## 4. Bug-fix batch — 27 verified failures fixed, no feature loss

- **Scope:** Re-verified the 54-item bug list line-by-line against code. 27 confirmed as live failures (section A), rest downgraded to smells or admitted as misreads (rank breadth, ask/stream drift, fusion runtime effect, shorts IndexError mechanism, sleep event-loop claim, JobStore race). Only section-A items fixed below; behavior otherwise preserved.
- **Phase 1 URL (`core/url_parsing.py`):** `_is_host()` exact-or-suffix check (`notyoutube.com` now rejected); `_require()` rejects empty `youtu.be/`, `/shorts/` no-ID, `/watch` no `v/list`, empty `list=`. `course_id_for()` format untouched to avoid orphaning data.
- **Phase 2 Ingestion:** `ingest/service.py` empty-chunk videos now `failed/empty transcript` (was inflated `indexed`); registry `set_course_status` + final `count_chunks` via `to_thread` with safe fallback. `sqlite_registry.py` `link_video_to_course` upserts position, all ops `try/finally` + `WAL` + 30s timeout. `youtube_catalog.py` simplified channel `base+"/videos"`, logs `skipped` unavailable entries. `youtube_data_api.py` ISO adds `W` weeks, `_is_unavailable_title()` replaces `"eleted"` substring. `youtube_captions.py` preserves `human/auto_captions` via `is_generated`, no sleep on last retry. `transcript_cleaning.py` `words_with_times` nil-safe.
- **Phase 3 Retrieval:** `ask/service.py` `_verify_explanation` fail-closed (`False`→`partial`, was fail-open `True`→answered); dedupe key rounded to 3 decimals. `reranker.py` logs warning before lexical fallback. `embeddings.py` 3-attempt retry with backoff for 429/502/503/504. `vector_store_qdrant.py` `VECTOR_SIZE=1024` init check + upsert dim guard, `search_keyword` paginated to 5000 (was 500 truncate). `container.py` rejects unknown `VECTOR_BACKEND`, `HashingEmbedder(dim=embedding_dimension)` so dim matches Qdrant. `llm.py` `stream_complete` uses `self.timeout` (was hardcoded 90).
- **Phase 4 Storage:** `vector_store_memory.py` atomic save (tmp+`os.replace`), corrupt file backed up to `.corrupt.bak`, `upsert` single-pass O(n+m). `sqlite_registry.py` `__init__` safe close. `vector_store_qdrant.py` `_to_chunk` reads both `start_seconds/start_sec` for backward compat.
- **Phase 5 API/Ops:** `api/routes.py` `GET /jobs/{id}/stream` now authed (header or `?api_key=` for EventSource); `GET /courses` uses new `list_all_course_videos()` single query (no N+1). `api/jobs.py` `_purge()` on create. `api/security.py` `hmac.compare_digest`. `api/app.py` CORS only if configured + minimal per-IP sliding-window limit for `/ask//ingest//translate` (429 `rate_limited`). `config.py` validates `0<=LOW<=HIGH<=1`, positive top_k/dims/timeouts.
- **Deliberately not changed:** `ytdlp_subtitles` parser (still unconfigured, now honest), eval harness stub, frontend rewrite, `course_id_for` format, `ScoredChunk/Citation` models.
- **Verification:** `pytest backend/tests` **47 passed** after each phase; `cli smoke` PASSED. No new tests added; existing offline-fake suite still covers URL/chunking/coverage/timestamps/ask/library/registry/translate.

---

# Session Log — 2026-10-03 (chat interface session)

## 1. Chat threads stored in the backend DB (single thread per course)

- **Decision (user-confirmed):** 1 video → 1 chat, 1 playlist → 1 chat (`chat_id = course_id`, reusing `course_id_for()`); single thread + Clear (no multi-thread switcher); follow-ups are context-aware via query rewriting; old chats survive re-index; refusals/`not_covered` stored with status badge.
- **`adapters/sqlite_registry.py`** — new `messages` table (one row per Q&A turn: `course_id, video_id, question, rewritten_question, answer, status, top_score, primary_source_json, also_mentioned_json, created_at`, indexed on `(course_id, created_at)`) + `save_message()` / `list_messages()` / `clear_messages()`. Writes never raise: a failed save can't break an answer.
- **`ask/service.py`** — `_recent_turns()` (last 3 via registry, try/except), `_needs_rewrite()` (history present AND short/anaphoric: ≤4 words or `FOLLOWUP_HINTS`), `_rewrite_query()` (one small `llm.complete ≤100 tokens`, fallback to original), `_save_turn()`. History is used for **rewriting only**, never as answer context — retrieval/synthesis/citations still run on excerpts, grounding preserved. Both `ask()` and `ask_stream()` rewrite + save (incl. refusals).
- **`api/routes.py`** — new `GET /courses/{id}/messages?limit=` (capped 500) and `DELETE /courses/{id}/messages`. `POST /ingest` now forwards `force`. `cli.py` ingest gained `--force`.
- **Verification:** pytest **47 passed**, `cli smoke` PASSED, plus an offline fake-store check: ingest → `skipped:0`, re-ingest → `skipped:1` with no duplicate chunks; ask → 1 saved row; clear → 0 rows.

## 2. Ingest dedup — skip already-indexed videos (saves embedding calls)

- **`ingest/service.py`** — `ingest(..., force=False)`: after catalog expand + register, videos with `store.count_chunks(course_id, video_id) > 0` are skipped (store is truth, not registry; count failures fall through to re-index). Job progress still bumps per skip. Report gains `skipped`; `requested` still reflects the original total. `force=true` (checkbox/API/CLI) re-indexes everything (e.g. after a pipeline bump). Pasting an indexed link returns immediately and the frontend reopens its previous chat.

## 3. Chatbot UI — single chat panel, drag-resize, no button chrome

- **Round 1:** replaced the separate "Ask a question" box with one chat panel (header, scope label, thread, typing dots, pill input + send, disclaimer); bubbles user-right/blue, assistant-left/white with badge + timestamp link seeking the left player; optimistic user bubble + SSE streaming into the bubble; Clear top-right; fullscreen overlay with recent list.
- **Round 2 (senior-frontend pass, per feedback):** deleted the overlay, expand/collapse buttons, `+ New chat` (mislabeled clear), `⋮` menu, and the static `U`/`[User name]` placeholder — every visible control now maps to a real endpoint. Chat expands by **stretching its left border** (`#chat-resizer`, same pointer-capture pattern as the sidebar resizer, persisted as `video-rag.chat-width`, 340px min, screen-limited max, double-click toggles default ↔ widest); panel is `height: calc(100vh - 140px)` sticky so it fits the screen; resizer hides on the stacked ≤1020px layout. Removed the stale duplicate `.home-grid` block it would have overridden.
- **Verification:** `node --check` clean, zero `overlay/expand/New chat` references, pytest **47 passed**.

## 4. Bug-fix batch — duplicated question + lost translation UI

- **Duplicated user message:** `handleAsk` appended the user bubble optimistically, then `appendChatTurn(result)` appended it again. Replaced with `appendAssistantTurn()` (assistant side only); `appendChatTurn` deleted. History rendering was already correct.
- **Translation re-wired per answer:** the 2026-10-03 `POST /translate` feature survived in the backend but the bubble redesign left it with no visible control. Every generated answer bubble now carries the translate icon (same SVG/language list); click toggles an inline picker that translates from the stored original (never chained), restores the original on placeholder re-select, and drops stale responses via a per-bubble token. Refusals with no answer text get no icon. Citations untouched.
- **Verification:** `node --check` clean, pytest **47 passed**.
