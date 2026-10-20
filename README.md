
# DEMO VIDEO 
https://github.com/user-attachments/assets/08c7bd36-4dec-46e4-ab7d-d90318545c54
# EVALUATION VIDEO
https://github.com/user-attachments/assets/325b0ae6-4491-4388-bee1-2d83d4ec2d70

# Video RAG for course playlists

Ask a question about a video course and get a written answer plus the exact
video and timestamp where the topic is explained. If the course does not cover
the topic, the system says so instead of inventing an answer.

```
Question: where can we use hashmaps
Status:   answered
Source:   Hash Tables Explained at 12:43
          https://www.youtube.com/watch?v=abc12345678&t=763s

Question: how does kubernetes autoscaling work
Status:   not_covered
          The topic is not covered in the given course
```

## Demo

<!-- Replace the link below with your demo video once uploaded (see options:
     1) YouTube link + thumbnail, 2) GitHub user-attachments MP4 upload).
     Example: [![Watch the demo](docs/demo-thumbnail.png)](https://www.youtube.com/watch?v=YOUR_ID) -->

No demo video linked yet — paste an ingest link, ask a question, and record
the timestamp-seek flow to fill this section.

## The problem it solves

Students learn from YouTube playlists but rarely take notes, so revision means
scrubbing through hours of video looking for one explanation. Course search on
video platforms matches titles, not spoken content.

This project indexes what is actually said in every video of a playlist, then
answers questions from that content only, always with a timestamp to jump to.

## Folder map

Every file in the project is listed here. Nothing else is hidden anywhere.

```
video-rag/
  backend/            Python service: ingestion, retrieval, API, tests
    src/video_rag/    the application package
    tests/            the test suite
    tools/            test runner that works without pytest
    eval/             threshold calibration harness (add labelled questions here)
    requirements.txt  runtime dependencies
    requirements-dev.txt  test and lint dependencies
    pyproject.toml    package, pytest, ruff and mypy settings
  frontend/           the web page: three files, no build step, no framework
    index.html        page structure (Home + Library views, chat panel)
    styles.css        styling
    app.js            calls the API and renders answers (chat, streaming, player seek)
  docs/               written documentation
    ARCHITECTURE.md   layers, diagrams and design decisions
    Workflow.md       end-to-end workflows that mirror the current code
  .env.example        every setting with comments; copy to .env
  .gitignore          keeps secrets and local data out of git
  README.md           this file
```

Inside `backend/src/video_rag/` the layout mirrors the architecture:

```
core/           pure logic with no external dependencies: url parsing,
                chunking, ranking, fusion, timestamps, cleaning,
                coverage gate, answer language
core/ports.py   the interfaces every adapter must satisfy
adapters/       everything that touches the outside world: youtube
                (yt-dlp + Data API v3), captions, embedders (Jina +
                hashing fallback), rerankers (Jina + lexical), vector
                stores (Qdrant + memory), sqlite registry + chat, llm
ingest/         use case one: index a course
ask/            use case two: answer one question about one course
api/            http layer: app, routes, request models, auth, jobs
container.py    builds the objects and picks adapters from the settings
config.py       every setting, read from the environment in one place
cli.py          command line entry point
```

## Quick start on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r backend\requirements-dev.txt
Copy-Item .env.example .env
$env:PYTHONPATH = "backend\src"
.\.venv\Scripts\python.exe -m video_rag.cli smoke
.\.venv\Scripts\python.exe -m video_rag.cli serve
```

Run these commands from the project folder in PowerShell. Before using real
videos, edit `.env` and set `VIDEO_RAG_JINA_API_KEY` (multilingual embeddings)
and `VIDEO_RAG_LLM_API_KEY` (answer writing). The smoke check runs offline and
needs no keys. `serve` starts the app; open <http://127.0.0.1:8000>, paste a
playlist link, wait for indexing, ask a question, and click the timestamp to
watch that moment.

Optional: set `VIDEO_RAG_YOUTUBE_API_KEY` when
`VIDEO_RAG_CATALOG_BACKEND=youtube_data_api`. Every setting is commented in
`.env.example`; the architecture and failure handling are in
`docs/ARCHITECTURE.md` and the step-by-step flows in `docs/Workflow.md`.

On Linux or macOS the same tasks are three commands:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
PYTHONPATH=backend/src .venv/bin/python -m video_rag.cli smoke
VIDEO_RAG_FRONTEND_DIR=frontend PYTHONPATH=backend/src .venv/bin/python -m video_rag.cli serve
```

## How it works, in four layers

1. **Input.** A pasted link is parsed into a video, playlist or channel. The
   playlist is expanded into a video list with ids, titles and durations.
2. **Transcripts.** The fast caption API (`youtube-transcript-api`, human >
    auto captions, retries with backoff) is the practical source. A yt-dlp
    subtitle fallback exists in the code path but its parser is not
    configured, so videos without any caption-API tracks are marked failed
    and skipped. Word level
    timings are kept untouched: they are the source of truth for every
    timestamp shown later.
3. **Storage.** Transcripts are split into overlapping windows of about a
   minute that never cross a sentence boundary. Each chunk stores its video id,
   its start second and its text, then is embedded and written to the vector
   store with the course id attached.
4. **Answering.** A question is embedded, the top chunks for that one course are
   retrieved and reranked, and a threshold decides the outcome: answer, answer
   with a caveat, or refuse. The winning chunk supplies the video link and the
   timestamp.

The refusal is a rule in code, not a request to a model, which is what makes it
reliable. `docs/ARCHITECTURE.md` has the diagrams and the reasoning.

## Technology stack

| Concern | What this system uses |
| --- | --- |
| Language / runtime | Python ≥3.11 (`backend/src/video_rag/`) |
| HTTP API | FastAPI + uvicorn, single process serves API + static frontend on `:8000` |
| Course listing | yt-dlp flat expand by default; opt-in YouTube Data API v3 (`VIDEO_RAG_CATALOG_BACKEND`) |
| Transcripts | `youtube-transcript-api` (human > auto, retries); yt-dlp subtitle parser present but unconfigured |
| Chunking | Custom sentence-aware ~60 s windows / ~15 s overlap (`core/chunking.py`) |
| Embeddings | Jina `jina-embeddings-v3`, 1024-dim multilingual hosted API; offline `HashingEmbedder` for tests/smoke only |
| Vector store | Qdrant Cloud (`qdrant-client`, one `chunks` collection filtered by `course_id`); local JSON `MemoryVectorStore` for dev/tests |
| Retrieval | Dense top-30 + keyword top-30, unioned + deduped, reranked to top-8 |
| Reranker | Jina `jina-reranker-v2-base-multilingual`; offline `LexicalReranker` fallback |
| Answer writing | Required OpenAI-compatible LLM (Groq `qwen/qwen3.8-27b` in the live `.env`); backend refuses to start without `VIDEO_RAG_LLM_API_KEY` |
| Registry + chat | SQLite (`data/registry.db`: `courses`, `videos`, `course_videos`, `messages`) |
| Frontend | Plain HTML/CSS/JS, no framework, no build (`index.html`, `app.js`, `styles.css`); YouTube IFrame embed seeks to citations; answers stream over SSE |
| Tests / lint | pytest (69 tests, fake adapters, offline) + 4 `node:test` frontend checks, stdlib `backend/tools/run_tests.py`, `cli smoke`, ruff + mypy configured |
| Eval | Offline runner + dashboard: `backend/eval/golden_set.jsonl` → `data/eval.db` → `/eval` (recall/MRR/timestamps, LLM-judge faithfulness/correctness, failures, trend, traces, CSV, SSE). Thresholds `0.55/0.15` still uncalibrated placeholders |

Defaults (`VIDEO_RAG_CATALOG_BACKEND=ytdlp`, `VIDEO_RAG_VECTOR_BACKEND=memory`, lexical rerank) run
offline for tests/smoke. The live setup uses Data API + Qdrant Cloud + Jina + Groq (see `.env.example`).

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/ingest` | Start indexing a video, playlist or channel. Returns a job id. |
| GET | `/jobs/{job_id}` | Poll indexing progress. |
| GET | `/jobs/{job_id}/stream` | Same progress as server sent events. |
| POST | `/ask` | Ask one question about one course. |
| POST | `/ask/stream` | Same answer as `/ask`, streamed token by token over SSE. |
| POST | `/translate` | Translate a generated answer into a selected language using the configured LLM. |
| GET | `/courses` | List indexed courses with video and chunk counts. |
| GET | `/courses/{course_id}/videos` | List the videos of one course. |
| GET | `/courses/{course_id}/syllabus` | List what the course contains (currently its video titles). |
| GET | `/courses/{course_id}/messages?limit=` | Read the saved chat thread for one course. |
| DELETE | `/courses/{course_id}/messages` | Clear the chat thread (the "Clear" button). |
| GET | `/health` | Liveness plus which backends are active. |
| GET | `/api/live/recent` | Recent live queries with scores (for the dashboard). |
| GET | `/api/live/query/{id}` | Full per-query trace: chunks, before/after scores, judge scores. |
| GET | `/api/live/events` | SSE stream of new live queries and landed judge scores. |

Interactive documentation is served at `/docs` outside production.

`POST /translate` accepts `{"answer": "...", "target_language": "Hindi"}`.
It uses the same configured language-model API key and API-key authentication
as the other protected routes; citations remain displayed separately from the
translated answer.

## Configuration

Every setting is an environment variable prefixed with `VIDEO_RAG_`, documented
with comments in `.env.example`. Copy that file to `.env` to change anything.
The defaults use local adapters (memory store, hashing embedder, lexical rerank)
so tests and `smoke` run offline with no keys; real indexing and answering need
`VIDEO_RAG_JINA_API_KEY` and the required `VIDEO_RAG_LLM_API_KEY`.

The two settings that matter most are the coverage thresholds
`VIDEO_RAG_THRESHOLD_HIGH` and `VIDEO_RAG_THRESHOLD_LOW`. They decide when the
system answers and when it refuses. The shipped values are placeholders. Label a few questions in `backend/eval/`
and extend `eval/run_eval.py` to sweep thresholds against them.

## Tests

```powershell
$env:PYTHONPATH = "backend\src"
.\.venv\Scripts\python.exe -m pytest backend\tests
```

69 tests cover URL parsing, chunk boundaries, timestamp refinement, retrieval and
ranking, the coverage gate, the answering service (video scoping, streaming,
answer-language mirroring), the library listing, the registry rows ingestion
writes, the translation endpoint, the refusal behaviour, and the offline eval
stack (dataset validation, DB/runner, deterministic scorers, judge fakes,
failure causes, eval API incl. SSE). They use fake adapters, so the suite runs
offline in a few seconds and never calls YouTube. Frontend formatting rules
have 4 extra stdlib `node:test` checks (`node --test frontend/eval-lib.test.js`).

`.\.venv\Scripts\python.exe -m video_rag.cli smoke` is the end to end check: it
indexes two fake videos, asks a covered question and an uncovered one, and
asserts both outcomes.

## Evaluation

Golden questions live in `backend/eval/golden_set.jsonl` (one JSON object per
line: `id`, `question`, `reference_answer`, `question_type`, `answerable`,
`requires_visual`, `key_terms`, `gt_segments: [{video_id, start_s, end_s}]`,
`split`, plus this repo's `course_id` scope). Validate and run offline-first:

```powershell
$env:PYTHONPATH = "backend\src"
.\.venv\Scripts\python.exe -m video_rag.cli eval-validate backend\eval\golden_set.jsonl
.\.venv\Scripts\python.exe -m video_rag.cli eval-run --dataset backend\eval\golden_set.jsonl --label first
.\.venv\Scripts\python.exe -m video_rag.cli eval-rescore --run <run_id>   # re-score after a prompt change
.\.venv\Scripts\python.exe backend\tools\check_no_mock_data.py           # no dummy data guard
```

`eval-run` executes every question through the real pipeline and stores
answers, retrieved chunks, citations, timings and token usage in
`data/eval.db`; deterministic metrics (recall, hit, MRR, precision, timestamp
hit rate, abstain rules, latency, cost) score immediately, and LLM-judge
metrics (faithfulness, relevancy, correctness, context recall, citation
accuracy) follow when `VIDEO_RAG_JUDGE_MODEL` is set — ideally a different
model from the generator.

## Live evaluation dashboard

`/eval` is now a **live, per-query dashboard** (it replaced the old batch-only
page, which survives as the "Offline check" tab for golden-set correctness).
Every real `/ask` and `/ask/stream` is recorded as it happens — retrieval
scores **before and after reranking** (dense cosine, keyword score, pre-rerank
union rank, rerank score and rank per chunk), the top excerpts the LLM actually
saw, the answer with its citations, and post-answer judge scores
(faithfulness, answer relevancy, per-chunk retrieval relevance) computed
asynchronously so they never delay a response. Retrieved relevance is the
judge-estimated share of retrieved chunks marked relevant (precision-like), not
recall. Live quality scores are estimates, not correctness judgments; the same
LLM currently generates and judges answers, so same-model bias is a known
limitation. Each metric records its own status, error, judge model, and prompt
version so a partial judge failure is not presented as a valid score.

Live judging uses a bounded in-process queue (2 workers, up to 100 waiting jobs).
When full, the query is still answered and its dashboard row explicitly reports
that scoring was skipped. Traces persist in `data/eval.db`; configure access,
backup, and retention according to your deployment's privacy requirements.

Set `VIDEO_RAG_LIVE_TRACE=false` to turn recording off. Endpoints:
`GET /api/live/recent`, `GET /api/live/query/{id}`, and the SSE stream
`GET /api/live/events`. Storage lives beside the offline eval tables in
`data/eval.db` (`live_queries`, `live_query_chunks`, `live_query_scores`).

To add a metric: define it in `backend/eval/metrics.yaml` (key, group,
format, direction), implement the scorer, write rows to `eval_scores` — the
API and dashboard pick it up with no UI changes.

## Deployment

The app runs directly from the venv: set the required keys in `.env`
(`VIDEO_RAG_JINA_API_KEY`, `VIDEO_RAG_LLM_API_KEY`) and start it with
`$env:PYTHONPATH = "backend\src"` followed by
`.\.venv\Scripts\python.exe -m video_rag.cli serve`.
Qdrant Cloud holds the vectors, so no local services are needed.

## Honest limitations

- Embeddings come from the Jina hosted API (multilingual), so real indexing
  and asking need network and a `JINA_API_KEY`. Without one the system boots
  with a toy hashing embedder that is only good for tests. Retrieval, ranking,
  refusal and timestamps do not depend on any local model.
- A language model key is required, not optional: the backend refuses to start
  without `LLM_API_KEY`. It writes the answers, verifies the gray zone and
  localizes refusals.
- Transcripts come from the caption API. The yt-dlp subtitle fallback exists
  in the code path but its parser is not configured, so videos without any
  caption-API tracks are skipped. A one hour video with captions indexes in
  well under a minute.
