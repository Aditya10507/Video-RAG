# Deployment

Three ways to run the product, from a laptop demo to a hosted deployment.

## 1. Local, one command

```powershell
.\run.ps1 setup
.\run.ps1 serve
```

Open <http://127.0.0.1:8000>. The web page and the API are served from the same
address. Data is written to `data\` in the project folder: a SQLite file with
the course registry and the local vector index.

Use this for demos and interviews. Nothing external is required.

## 2. Docker Compose, API plus a real vector database

```bash
cp .env.example .env
docker compose up --build
```

This starts two services:

| Service | What it is | Port |
| --- | --- | --- |
| `api` | The FastAPI backend, with the web page baked into the image | 8000 |
| `qdrant` | The production vector store | 6333 |

Before the first start, put real keys into the compose environment or your
`.env`: `VIDEO_RAG_JINA_API_KEY` (embeddings) and `VIDEO_RAG_LLM_API_KEY`
(answer writing — the backend refuses to start without one). Replace
`change-me` for `VIDEO_RAG_API_KEYS` as below.

The compose file already sets `VIDEO_RAG_VECTOR_BACKEND=qdrant` and points the
API at the Qdrant container, so the switch from the local index needs no code
change. Both services keep their data in named Docker volumes, so a restart does
not lose the index.

Before exposing this anywhere, change `VIDEO_RAG_API_KEYS` in the compose file
or your `.env` from `change-me` to a real value.

## 3. A single container on a cloud host

The image is self contained: it serves the API and the web page from one port
and needs one writable volume.

```bash
docker build -f backend/Dockerfile -t video-rag .
docker run -p 8000:8000 \
  -e VIDEO_RAG_ENVIRONMENT=production \
  -e VIDEO_RAG_API_KEYS=your-secret-key \
  -e VIDEO_RAG_LOG_JSON=true \
  -v video_rag_data:/app/data \
  video-rag
```

Any host that runs a container works: Render, Railway, Fly.io, Azure Container
Apps, Google Cloud Run or a plain virtual machine. Points to check on the host:

- Expose port 8000 and let the platform terminate TLS.
- Attach a persistent volume at `/app/data`, or set
  `VIDEO_RAG_VECTOR_BACKEND=qdrant` and use a managed Qdrant instance instead.
- Set the health check path to `/health`. The image already declares it.
- Indexing is long running. On platforms that scale to zero, index from the CLI
  or keep one instance warm while a job runs.

## Production checklist

The defaults are safe for local use. These are the switches to flip for a
public deployment.

| Setting | Value | Why |
| --- | --- | --- |
| `VIDEO_RAG_ENVIRONMENT` | `production` | Hides `/docs` and `/openapi.json`. |
| `VIDEO_RAG_API_KEYS` | a long random value | Enables the `X-API-Key` guard. Without keys the API is open. |
| `VIDEO_RAG_CORS_ORIGINS` | leave empty | Only needed if the page is hosted separately from the API. |
| `VIDEO_RAG_LOG_JSON` | `true` | Structured logs with a request id per call. |
| `VIDEO_RAG_RATE_LIMIT_PER_MINUTE` | keep or lower | Per client sliding window limit. |
| `VIDEO_RAG_VECTOR_BACKEND` | `qdrant` | The local index is single process only. |

What the service already does without extra work: an `X-Request-ID` on every
response, `nosniff`, `DENY` framing and `no-referrer` security headers, secrets
masked in the config output, one error shape for every failure, a non root
container user, and validation limits on every request field.

## After the first deployment

1. Index one real playlist and confirm the timestamps land within a few seconds
   of the explanation.
2. Ask a question the course does not cover and confirm the refusal.
3. Run `.\run.ps1 eval` against your labelled questions and set
   `VIDEO_RAG_THRESHOLD_HIGH` and `VIDEO_RAG_THRESHOLD_LOW` from the results.
   The shipped values are placeholders.
4. If many videos fail during indexing, lower
   `VIDEO_RAG_INGEST_CONCURRENCY` to 3.
