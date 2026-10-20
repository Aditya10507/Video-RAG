"""SQLite store + writer for live per-query traces (data/eval.db).

Tables live beside the offline eval tables (same file, same backup story). All
writes are plain sqlite3 with parameters, connection-per-operation, WAL —
matching the registry and eval-store conventions.

Best-effort rule: LiveTracer methods swallow their own errors (warning log) so
tracing can never break an answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  question TEXT NOT NULL,
  effective_question TEXT,
  course_id TEXT NOT NULL,
  video_id TEXT,
  source TEXT,
  status TEXT,
  scoring_status TEXT DEFAULT 'pending',
  message TEXT, answer TEXT, top_score REAL,
  gate TEXT, verify_verdict TEXT, lang TEXT,
  covers TEXT, primary_source TEXT, also_mentioned TEXT,
  timings_ms TEXT, usage TEXT,
  scoring_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_queries_id ON live_queries(id);
CREATE TABLE IF NOT EXISTS live_query_chunks (
  query_id INTEGER NOT NULL REFERENCES live_queries(id),
  chunk_key TEXT NOT NULL,
  idx INTEGER,
  video_id TEXT, title TEXT,
  start_s REAL, end_s REAL,
  text TEXT,
  dense_score REAL, keyword_score REAL,
  union_rank INTEGER, rerank_score REAL,
  sent_to_llm INTEGER DEFAULT 0,
  relevance REAL, relevance_detail TEXT,
  PRIMARY KEY (query_id, chunk_key)
);
CREATE INDEX IF NOT EXISTS idx_live_chunks_query ON live_query_chunks(query_id);
CREATE TABLE IF NOT EXISTS live_query_scores (
  query_id INTEGER NOT NULL REFERENCES live_queries(id),
  metric TEXT NOT NULL,
  value REAL,
  detail TEXT,
  judge_model TEXT, prompt_version TEXT,
  status TEXT NOT NULL DEFAULT 'done',
  error TEXT,
  PRIMARY KEY (query_id, metric)
);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect_live(db_path: str | Path) -> sqlite3.Connection:
    """Create-if-missing connection to the eval DB with the live tables."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30.0)
    con.row_factory = sqlite3.Row
    with contextlib.suppress(sqlite3.Error):
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(live_query_scores)")}
    if "status" not in columns:
        con.execute(
            "ALTER TABLE live_query_scores ADD COLUMN status TEXT NOT NULL DEFAULT 'done'"
        )
    if "error" not in columns:
        con.execute("ALTER TABLE live_query_scores ADD COLUMN error TEXT")
    return con


def _chunk_key_str(key: tuple) -> str:
    return f"{key[0]}:{key[1]:.3f}"


def _loads_or_none(raw):
    """JSON-decode a stored blob; empty/corrupt -> None (missing is not zero)."""
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class LiveTracer:
    """Records one row per live question, chunk rows per retrieval, then
    schedules post-answer judge scoring. Every public method is safe to call
    with a half-broken environment: failures log a warning and return."""

    def __init__(self, db_path: str | Path, llm=None):
        self.db_path = str(db_path)
        self.llm = llm
        self.log = logging.getLogger(__name__)

    def open(self, meta: dict) -> int | None:
        try:
            con = connect_live(self.db_path)
            try:
                cur = con.execute(
                    "INSERT INTO live_queries (created_at, question, course_id, video_id,"
                    " source, status) VALUES (?, ?, ?, ?, ?, 'running')",
                    (
                        _now(),
                        meta.get("question", ""),
                        meta.get("course_id", ""),
                        meta.get("video_id"),
                        meta.get("source", "ask"),
                    ),
                )
                con.commit()
                return int(cur.lastrowid or 0)
            finally:
                con.close()
        except Exception as e:
            self.log.warning("live trace open failed: %s", e)
            return None

    def set_retrieval(self, query_id: int | None, capture: dict | None) -> None:
        """Write one row per retrieved chunk: dense/keyword scores, pre-rerank
        union rank, rerank score, post-rerank rank. Sent flags come later
        (finish) because refusals never prompt the LLM."""
        if query_id is None or not capture:
            return
        try:
            con = connect_live(self.db_path)
            try:
                con.execute("DELETE FROM live_query_chunks WHERE query_id = ?", (query_id,))
                for idx, key in enumerate(capture["rerank_order"], start=1):
                    c = capture["chunks"][key]
                    con.execute(
                        "INSERT INTO live_query_chunks (query_id, chunk_key, idx, video_id,"
                        " title, start_s, end_s, text, dense_score, keyword_score,"
                        " union_rank, rerank_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            query_id,
                            _chunk_key_str(key),
                            idx,
                            key[0],
                            c.video_title,
                            float(c.start_sec),
                            float(c.end_sec),
                            c.text,
                            capture["dense_scores"].get(key),
                            capture["keyword_scores"].get(key),
                            capture["union_rank"].get(key),
                            capture["rerank_scores"].get(key),
                        ),
                    )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self.log.warning("live trace chunk write failed: %s", e)

    def finish(
        self,
        query_id: int | None,
        out: dict,
        capture: dict | None,
        *,
        effective: str,
        lang: str,
        gate: str | None,
        verify_verdict: str | None,
        timings: dict,
        usage: dict | None,
    ) -> None:
        if query_id is None:
            return
        try:
            con = connect_live(self.db_path)
            try:
                con.execute(
                    "UPDATE live_queries SET effective_question = ?, lang = ?, gate = ?,"
                    " verify_verdict = ?, status = ?, message = ?, answer = ?, top_score = ?,"
                    " covers = ?, primary_source = ?, also_mentioned = ?, timings_ms = ?,"
                    " usage = ? WHERE id = ?",
                    (
                        effective,
                        lang,
                        gate,
                        verify_verdict,
                        out.get("status"),
                        out.get("message"),
                        out.get("answer"),
                        out.get("top_score", 0.0),
                        json.dumps(out.get("course_covers") or []),
                        json.dumps(out.get("primary_source")) if out.get("primary_source") else "",
                        json.dumps(out.get("also_mentioned_in") or []),
                        json.dumps(timings),
                        json.dumps(usage or {}),
                        query_id,
                    ),
                )
                # Chunks were already written at retrieval time; only sent flags
                # depend on the final status (refusals never prompt the LLM).
                answered = out.get("status") != "not_covered" and (out.get("answer") or "").strip()
                if capture and answered:
                    for key in capture["rerank_order"][:6]:
                        con.execute(
                            "UPDATE live_query_chunks SET sent_to_llm = 1"
                            " WHERE query_id = ? AND chunk_key = ?",
                            (query_id, _chunk_key_str(key)),
                        )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self.log.warning("live trace finish failed: %s", e)
            return
        self._schedule_scoring(query_id, effective or out.get("question", "") or "")

    def fail(self, query_id: int | None, error: str) -> None:
        if query_id is None:
            return
        try:
            con = connect_live(self.db_path)
            try:
                con.execute(
                    "UPDATE live_queries SET status = 'error', scoring_status = 'error',"
                    " scoring_error = ? WHERE id = ?",
                    (str(error)[:300], query_id),
                )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self.log.warning("live trace fail-mark failed: %s", e)

    def _schedule_scoring(self, query_id: int, question: str) -> None:
        try:
            from .scoring import schedule

            schedule(self.llm, self.db_path, query_id, question)
        except Exception as e:
            self.log.warning("live scoring schedule failed: %s", e)


# --- Read side ------------------------------------------------------------


def _scores_map(con: sqlite3.Connection, qids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not qids:
        return out
    for query_id in qids:
        for r in con.execute(
            "SELECT query_id, metric, value, detail, judge_model, prompt_version, status, error"
            " FROM live_query_scores WHERE query_id = ?",
            (query_id,),
        ):
            try:
                d = json.loads(r["detail"] or "{}")
            except ValueError:
                d = {}
            out.setdefault(r["query_id"], {})[r["metric"]] = {
                "value": r["value"],
                "detail": d,
                "judge_model": r["judge_model"],
                "prompt_version": r["prompt_version"],
                "status": r["status"],
                "error": r["error"],
            }
    return out


def recent(db_path: str | Path, limit: int = 30) -> list[dict]:
    con = connect_live(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM live_queries ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 50)),)
        ).fetchall()
        scores = _scores_map(con, [r["id"] for r in rows])
        out = []
        for r in rows:
            d = dict(r)
            d.pop("covers", None)
            d.pop("primary_source", None)
            d["scores"] = scores.get(r["id"], {})
            n_chunks = con.execute(
                "SELECT COUNT(*) FROM live_query_chunks WHERE query_id = ?", (r["id"],)
            ).fetchone()[0]
            d["chunk_count"] = n_chunks
            out.append(d)
        return out
    finally:
        con.close()


def detail(db_path: str | Path, query_id: int) -> dict | None:
    con = connect_live(db_path)
    try:
        q = con.execute("SELECT * FROM live_queries WHERE id = ?", (query_id,)).fetchone()
        if q is None:
            return None
        chunks = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM live_query_chunks WHERE query_id = ? ORDER BY idx", (query_id,)
            ).fetchall()
        ]
        scores = _scores_map(con, [query_id]).get(query_id, {})
        d = dict(q)
        for key in ("covers", "primary_source", "also_mentioned", "timings_ms", "usage"):
            d[key] = _loads_or_none(d.get(key))
        d["chunks"] = chunks
        d["scores"] = scores
        return d
    finally:
        con.close()


def poll_live(
    db_path: str | Path, since_id: int, watched: dict[int, str]
) -> tuple[list[dict], int, dict[int, str]]:
    """Emit each query's asked, completed, and scored transitions once."""
    con = connect_live(db_path)
    try:
        new = con.execute(
            "SELECT id, status, scoring_status FROM live_queries WHERE id > ? ORDER BY id",
            (since_id,),
        ).fetchall()
        events = []
        statuses = {int(r["id"]): r for r in new}
        for r in new:
            qid = int(r["id"])
            events.append({"query_id": qid, "phase": "asked"})
            watched[qid] = "asked"
        if new:
            since_id = int(new[-1]["id"])

        active_ids = [qid for qid in watched if qid not in statuses]
        if active_ids:
            for qid in active_ids:
                row = con.execute(
                    "SELECT id, status, scoring_status FROM live_queries WHERE id = ?",
                    (qid,),
                ).fetchone()
                if row is not None:
                    statuses[qid] = row

        terminal_scoring = {"done", "partial", "error", "overloaded"}
        for qid, row in statuses.items():
            phase = watched.get(qid)
            if phase is None:
                continue
            if phase == "asked" and row["status"] != "running":
                events.append({"query_id": qid, "phase": "updated"})
                watched[qid] = "updated"
                phase = "updated"
            if row["scoring_status"] in terminal_scoring:
                events.append({"query_id": qid, "phase": "scored"})
                watched.pop(qid, None)
    finally:
        con.close()
    return events, since_id, watched


async def live_event_stream(db_path: str | Path):
    """Infinite SSE body for /api/live/events. Ends cleanly on cancellation."""
    yield ": connected\n\n"
    since_id = 0
    watched: dict[int, str] = {}
    quiet = 0
    poll_error_reported = False
    try:
        while True:
            try:
                events, since_id, watched = await asyncio.to_thread(
                    poll_live, db_path, since_id, watched
                )
                poll_error_reported = False
            except Exception:
                if not poll_error_reported:
                    log.exception("live event poll failed")
                    poll_error_reported = True
                events = []
            for e in events:
                yield f"data: {json.dumps(e)}\n\n"
            quiet = quiet + 1 if not events else 0
            if quiet >= 15:
                yield ": heartbeat\n\n"
                quiet = 0
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
