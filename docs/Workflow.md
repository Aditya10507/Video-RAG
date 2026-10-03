# Workflow — Video RAG, end to end

How a link becomes searchable video knowledge, and how a question becomes a
timestamped answer. Every diagram below mirrors the current code
(`backend/src/video_rag/`, `frontend/app.js`). Render the `mermaid` blocks on
GitHub or any Mermaid renderer to see the diagrams.

---

## 1. System overview

```mermaid
flowchart LR
    U[User browser<br/>frontend: index.html + app.js + styles.css] --> API[FastAPI<br/>api/app.py + api/routes.py]
    API --> ING[IngestService<br/>ingest/service.py]
    API --> ASK[QueryService<br/>ask/service.py]
    API --> JOBS[JobStore<br/>api/jobs.py]
    ING --> CAT[Catalog<br/>youtube_catalog / youtube_data_api]
    ING --> TR[Transcripts<br/>youtube_captions]
    ING --> EMB[Embedder<br/>Jina hosted API]
    ASK --> EMB
    ING --> VEC[(Vector store<br/>Qdrant Cloud)]
    ASK --> VEC
    ASK --> LLM[LLM<br/>Groq OpenAI-compatible]
    ASK --> RER[Reranker<br/>Jina / lexical]
    ING --> REG[(SQLite registry<br/>data/registry.db)]
    ASK --> REG
    TRS[POST /translate] --> LLM
```

Key rule: the **vector store is the source of truth** for answers; the
**registry is bookkeeping** (Library lists, chat history, statuses). One
process serves both the API and the page (`VIDEO_RAG_FRONTEND_DIR=frontend`),
so there is no CORS and no separate frontend server.

---

## 2. Indexing workflow (ingest a link)

```mermaid
flowchart TD
    A[Paste YouTube link<br/>video / playlist / channel] --> B[parse_url + course_id_for<br/>core/url_parsing.py]
    B --> C{Ambiguous?<br/>watch?v=X&list=Y}
    C -->|yes| D[400: choose single video or playlist]
    C -->|no| E[catalog.expand<br/>video list + titles + durations]
    E --> F[Register course + videos<br/>status: ingesting / pending]
    F --> G{force=true?}
    G -->|yes| H[Index every video]
    G -->|no| I[Per video: count_chunks in vector store?]
    I -->|chunks exist| J[Skip: no transcript, no embedding call<br/>job progress bumps]
    I -->|none| H
    H --> K[Fetch transcript<br/>caption API, retries]
    K --> L{Transcript ok?}
    L -->|no| M[Mark failed + error, continue]
    L -->|yes| N[Chunk words<br/>~60s windows, 15s overlap,<br/>never split a sentence]
    N --> O{Chunks non-empty?}
    O -->|no| M
    O -->|yes| P[Embed chunk texts<br/>Jina, batched]
    P --> Q[Upsert chunks + vectors<br/>tagged with course_id]
    Q --> R[Mark video indexed]
    J --> S
    M --> S
    R --> S[All videos done]
    S --> T[Course ready or failed<br/>report: requested / indexed / failed / skipped / chunk_count]
```

Notes:

- Skips are per-video, so re-pasting a playlist after a partial failure only
  indexes what is missing. `force` (checkbox / `--force` / API flag)
  re-indexes everything, e.g. after a pipeline bump.
- Registry writes never fail a job: every registry call is try/except+log.
- Progress: total is published right after catalog expansion
  (`JobStore.set_total`); the frontend polls `GET /jobs/{id}` every 1.5s.

---

## 3. Ingest sequence (who talks to whom)

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI
    participant J as JobStore
    participant I as IngestService
    participant Y as YouTube + captions
    participant E as Jina embeddings
    participant V as Qdrant
    participant R as SQLite registry

    B->>A: POST /ingest {url, force}
    A->>J: create job (202 + job_id)
    A-->>B: 202 {job_id}
    A->>I: ingest(url, jobs, job_id, force)
    I->>Y: expand playlist → video list
    I->>R: upsert course + videos (ingesting/pending)
    I->>J: set_total(n)
    loop each video (concurrency 6)
        I->>V: count_chunks(course, video)
        alt already indexed and not force
            I->>J: bump (skipped)
        else needs indexing
            I->>Y: fetch transcript
            I->>E: embed_texts(chunks)
            I->>V: upsert(course, chunks, vectors)
            I->>R: mark indexed/failed
            I->>J: bump
        end
    end
    I->>R: set course ready/failed
    I->>J: finish(report, course_id)
    loop frontend poll 1.5s
        B->>A: GET /jobs/{job_id}
        A-->>B: {status, completed, total, report?}
    end
```

---

## 4. Ask workflow (one chat question)

```mermaid
flowchart TD
    A[Type message in chat input] --> B[Validate<br/>course set, ≤500 chars,<br/>video belongs to course]
    B --> C[Load last 3 turns<br/>from messages table]
    C --> D{Follow-up?<br/>≤4 words or pronoun/more/example hint}
    D -->|yes| E[Rewrite to standalone question<br/>1 small LLM call, fallback: original]
    D -->|no| F[Use question as-is<br/>no extra LLM call]
    E --> G[Embed query]
    F --> G
    G --> H[Dense search top 30<br/>course-filtered + optional video filter]
    G --> I[Keyword search top 30]
    H --> J[Dedupe + rerank to top 8]
    I --> J
    J --> K[Coverage gate<br/>on rerank scores]
    K -->|covered| L[Synthesize 4-6 sentences<br/>ONLY from excerpts<br/>in the question's language]
    K -->|not covered| M[Refuse with localized message<br/>+ what the course covers]
    K -->|gray zone| N[LLM verify: does top excerpt<br/>EXPLAIN or merely mention?]
    N -->|explains| L
    N -->|mentions| O[Partial answer + caveat]
    L --> P[Pick winning video, refine timestamp<br/>best sentence minus 3s lead-in]
    O --> P
    P --> Q[Primary citation + up to 3 also-mentioned<br/>youtube_url with t= seconds]
    Q --> R[Save turn: question, rewritten,<br/>answer, status, score, citations]
    M --> R
    R --> S[Render assistant bubble<br/>badge + answer + timestamp link]
    S --> T[Click timestamp → player seeks<br/>to that exact second]
```

History is used for **rewriting only** — old answers are never fed as answer
context, so every answer stays grounded in video excerpts plus its citation.

---

## 5. Coverage gate (the refusal rule)

```mermaid
flowchart TD
    A[Rerank scores<br/>top1 + rest] --> B{top1 >= HIGH<br/>default 0.55}
    B -->|yes| C[covered → answer]
    B -->|no| D{top1 < LOW<br/>default 0.15}
    D -->|yes| E[not_covered → refuse]
    D -->|no| F[gray zone → verify]
    F --> G{Top excerpt explains?}
    G -->|yes| C
    G -->|no| H[partial → answer with caveat]
```

The refusal is a rule in code, not a model instruction — that is what makes
it reliable. `HIGH`/`LOW` are placeholders: calibrate on labelled questions
before trusting refusal behaviour.

---

## 6. Answer streaming sequence

`POST /ask/stream` runs the **same** retrieval + gate as `/ask`; only the
LLM call streams. The frontend falls back to plain `/ask` with a smooth
reveal if the stream endpoint is unreachable.

```mermaid
sequenceDiagram
    participant B as Browser bubble
    participant A as FastAPI
    participant Q as QueryService
    participant L as LLM

    B->>A: POST /ask/stream {question, course_id, video_id?}
    A->>Q: checks + rewrite + retrieve + gate
    alt not_covered
        Q-->>B: data: {"result": {status: not_covered, ...}}
    else covered / verify
        Q->>L: stream_complete(prompt)
        loop tokens
            L-->>Q: delta
            Q-->>B: data: {"token": "..."}
        end
        Q-->>B: data: {"result": {answer, status, primary_source, ...}}
    end
    B-->>B: data: [DONE]
```

---

## 7. Translation workflow (per-answer icon)

```mermaid
flowchart TD
    A[Click translate icon<br/>on any answer bubble] --> B[Pick language<br/>inline select]
    B --> C[POST /translate<br/>{answer: ORIGINAL text, target_language}]
    C --> D{Valid + model replies?}
    D -->|no| E[Show API error in bubble<br/>answer unchanged]
    D -->|yes| F[Replace bubble text<br/>with translated answer]
    F --> G[Pick another language →<br/>re-translates from ORIGINAL]
    G --> B
    B -->|placeholder| H[Restore original text]
```

Citations are never translated — they stay separate so timestamps and links
survive in every language.

---

## 8. Chat history and Library data flow

- **One thread per course**: a single video is `course_id = video:{id}`; a
  playlist is `course_id = {playlist_id}`. Pasting an indexed link loads its
  existing thread; `Clear` deletes it (`DELETE messages`).
- **Write path**: every `/ask` and `/ask/stream` result (including refusals)
  is saved by `QueryService._save_turn` — never fatal on failure.
- **Read path**: opening a course calls `GET /courses/{id}/messages?limit=100`
  and renders user-right / assistant-left bubbles, each with its badge and
  timestamp link.
- **Library path**: `GET /courses` + `GET /courses/{id}/videos` join registry
  rows with real vector counts — a video counts as indexed only when its
  chunks exist in the store. Picking a video jumps home with that course +
  video filter applied.

---

## 9. Storage map

```mermaid
flowchart LR
    subgraph SQLite [data/registry.db — metadata + chat]
        C[(courses<br/>course_id, source_url, title,<br/>video_count, status)]
        V[(videos<br/>video_id, title, duration,<br/>status, error)]
        CV[(course_videos<br/>course_id, video_id, position)]
        M[(messages<br/>course_id, video_id, question,<br/>rewritten, answer, status,<br/>score, citations JSON)]
    end
    subgraph QDRANT [Qdrant Cloud — vectors]
        P[(chunks collection<br/>1024-dim vector + payload:<br/>course_id, video_id, title,<br/>text, sentences, seconds)]
    end
    C --- CV --- V
```

Jobs (`JobStore`) are the only ephemeral state (retention 3600s). Everything
else survives restarts.

---

## 10. Edge cases at a glance

| Situation | Behaviour |
| --- | --- |
| `watch?v=X&list=Y` link | `ambiguous` → 400, user picks single video or playlist |
| Re-paste indexed link | Skipped per video, previous chat reopens, zero embedding calls |
| Video has no captions | Marked `failed`, skipped, rest of course still indexes |
| Empty question / >500 chars | Rejected before any retrieval or LLM call |
| `video_id` outside course | 400 `video is not part of this course` |
| Rewrite LLM fails | Falls back to the original question |
| Stream drops mid-answer | Falls back to plain `/ask` + smooth reveal |
| Registry write fails | Logged only; job and answer continue (vectors decide) |
| Slow translate overtaken | Stale response dropped via per-bubble token |
