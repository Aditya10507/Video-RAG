# Frontend

Three files, no build step, no packages, no framework.

| File | Responsibility |
| --- | --- |
| `index.html` | The page structure: a sidebar with Home and Library views. Home holds index (left) plus ask/answer (right); Library lists indexed courses. |
| `styles.css` | All styling. Colours are CSS variables at the top of the file. |
| `app.js` | Calls the API and renders the result. |

## How to run it

Start the backend with the frontend folder attached and open one address:

```powershell
.\run.ps1 serve
```

Then open <http://127.0.0.1:8000>.

The API serves this folder as static files, so the page and the endpoints share
one origin. That means no CORS configuration, one process to deploy and one URL
to share in a portfolio.

## What the page does

1. On load it calls `GET /courses` and fills the Library; a health badge
   existed once but was removed from the header by design (the API is local,
   and failures surface in the status lines).
2. Home (left column) posts to `/ingest`, then polls `GET /jobs/<job_id>`
   every 1.5 seconds. The progress bar moves as each video finishes and
   lands at 100% when the job succeeds. When chunks land, the Ask column
   opens beside it.
3. Home (right column) asks through `POST /ask/stream` when the backend
   offers it: tokens arrive over SSE and the answer text grows live. If the
   endpoint is missing it falls back to plain `/ask` and reveals the full
   answer with a smooth animation instead. The video, its timestamps and
   the player render below the index card.
4. Library calls `GET /courses` and `GET /courses/<id>/videos` and renders
   them as expandable list rows. Picking a video jumps back to Home with
   the selection applied — no link pasting twice.

Clicking any source seeks the embedded player to that exact second. That is the
feature worth demonstrating first.

On a refusal the page also lists what the course does cover, which shows the
refusal was a deliberate decision rather than a failed search.

## Decisions worth explaining

- **Polling for job progress instead of server sent events.** The API exposes
  `/jobs/<job_id>/stream`, but a browser `EventSource` cannot send an
  `X-API-Key` header, so polling keeps the page working against a protected
  deployment. For answers the page does use true streaming via `POST
  /ask/stream` (fetch-based SSE), with the polling answer as fallback.
- **Answers mirror the question language.** English questions get English
  answers; Hinglish and Hindi questions get Hinglish and Devanagari answers.
  The language detector lives in the backend (`core/language.py`).
- **`textContent`, never `innerHTML`.** Video titles and transcript excerpts are
  external text. Writing them as text nodes makes script injection impossible.
- **One request helper.** Authentication, JSON encoding and error handling live
  in a single `request()` function, so every call behaves the same way.
- **The API key stays in session storage.** It survives a reload but not a
  closed browser, and it is never written into the project files.

## Pointing the page at a remote API

`app.js` uses a relative base URL, so it always talks to whichever host serves
it. To run the page separately from the API, set `API_BASE` at the top of
`app.js` to the API address and add that page origin to
`VIDEO_RAG_CORS_ORIGINS` in your `.env`.

## If you later want React

The client only depends on four endpoints and returns plain JSON, so a React or
Next.js version is a rewrite of this folder alone. Keep `/ingest`, `/jobs`,
`/ask` and `/health` and nothing in the backend has to change.
