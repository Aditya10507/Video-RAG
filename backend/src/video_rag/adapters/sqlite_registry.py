"""Courses and videos bookkeeping. Zero-config, resumable jobs."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
  course_id TEXT PRIMARY KEY, source_url TEXT NOT NULL, title TEXT,
  video_count INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending', syllabus_json TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, title TEXT, duration_seconds INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending', transcript_source TEXT, pipeline_version TEXT,
  error TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS course_videos (
  course_id TEXT, video_id TEXT, position INTEGER,
  PRIMARY KEY (course_id, video_id));
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id TEXT NOT NULL, video_id TEXT,
  question TEXT NOT NULL, rewritten_question TEXT,
  answer TEXT, status TEXT, top_score REAL DEFAULT 0,
  primary_source_json TEXT, also_mentioned_json TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_messages_course ON messages(course_id, created_at);
"""


class SqliteRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=30.0)
        try:
            con.executescript(SCHEMA)
            self._restore_original_video_columns(con)
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _restore_original_video_columns(con: sqlite3.Connection) -> None:
        columns = {row[1] for row in con.execute("PRAGMA table_info(videos)")}
        for current_name, original_name in (
            ("duration_sec", "duration_seconds"),
            ("indexed_at", "updated_at"),
        ):
            if original_name not in columns and current_name in columns:
                con.execute(f"ALTER TABLE videos RENAME COLUMN {current_name} TO {original_name}")
                columns.remove(current_name)
                columns.add(original_name)

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=30.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        return con

    def list_courses(self) -> list[dict]:
        con = self._con()
        try:
            rows = con.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def list_course_videos(self, course_id: str) -> list[dict]:
        con = self._con()
        try:
            rows = con.execute(
                "SELECT v.video_id, v.title, v.status, v.duration_seconds FROM videos v "
                "JOIN course_videos cv ON cv.video_id=v.video_id "
                "WHERE cv.course_id=? ORDER BY cv.position",
                (course_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def list_all_course_videos(self) -> dict[str, list[dict]]:
        con = self._con()
        try:
            rows = con.execute(
                "SELECT cv.course_id, v.video_id, v.title, v.status, v.duration_seconds"
                " FROM videos v JOIN course_videos cv ON cv.video_id=v.video_id"
                " ORDER BY cv.course_id, cv.position"
            ).fetchall()
            out: dict[str, list[dict]] = {}
            for r in rows:
                d = dict(r)
                out.setdefault(d.pop("course_id"), []).append(d)
            return out
        finally:
            con.close()

    # --- Write side (used by ingestion) -------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def upsert_course(
        self, course_id: str, source_url: str, title: str, video_count: int
    ) -> None:
        now = self._now()
        con = self._con()
        try:
            con.execute(
                "INSERT INTO courses (course_id, source_url, title, video_count, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'ingesting', ?, ?)"
                " ON CONFLICT(course_id) DO UPDATE SET source_url=excluded.source_url,"
                " title=excluded.title, video_count=excluded.video_count,"
                " status='ingesting', updated_at=excluded.updated_at",
                (course_id, source_url, title, video_count, now, now),
            )
            con.commit()
        finally:
            con.close()

    def upsert_video(
        self,
        video_id: str,
        title: str,
        duration_sec: int,
        status: str = "pending",
        error: str = "",
    ) -> None:
        now = self._now()
        con = self._con()
        try:
            con.execute(
                "INSERT INTO videos (video_id, title, duration_seconds, status, error,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(video_id) DO UPDATE SET title=excluded.title,"
                " duration_seconds=excluded.duration_seconds, status=excluded.status,"
                " error=excluded.error, updated_at=excluded.updated_at",
                (video_id, title, duration_sec, status, error, now),
            )
            con.commit()
        finally:
            con.close()

    def link_video_to_course(
        self, course_id: str, video_id: str, position: int
    ) -> None:
        con = self._con()
        try:
            con.execute(
                "INSERT INTO course_videos (course_id, video_id, position)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(course_id, video_id) DO UPDATE SET position=excluded.position",
                (course_id, video_id, position),
            )
            con.commit()
        finally:
            con.close()

    def set_course_status(self, course_id: str, status: str) -> None:
        con = self._con()
        try:
            con.execute(
                "UPDATE courses SET status=?, updated_at=? WHERE course_id=?",
                (status, self._now(), course_id),
            )
            con.commit()
        finally:
            con.close()

    def mark_video_status(
        self, video_id: str, status: str, error: str = ""
    ) -> None:
        con = self._con()
        try:
            con.execute(
                "UPDATE videos SET status=?, error=?, updated_at=? WHERE video_id=?",
                (status, error, self._now(), video_id),
            )
            con.commit()
        finally:
            con.close()

    # --- Chat history: one row per Q&A turn, keyed by course_id -------------
    # Single thread per course (1 video -> 1 chat, 1 playlist -> 1 chat).
    # Writes never raise: a failed save must not break an answer.

    def save_message(
        self,
        course_id: str,
        question: str,
        answer: str = "",
        status: str = "",
        top_score: float = 0.0,
        primary_source: dict | None = None,
        also_mentioned: list | None = None,
        video_id: str | None = None,
        rewritten_question: str = "",
    ) -> int:
        import json as _json

        con = self._con()
        try:
            cur = con.execute(
                "INSERT INTO messages (course_id, video_id, question, rewritten_question,"
                " answer, status, top_score, primary_source_json, also_mentioned_json,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    course_id,
                    video_id,
                    question,
                    rewritten_question or "",
                    answer or "",
                    status or "",
                    float(top_score or 0.0),
                    _json.dumps(primary_source) if primary_source else "",
                    _json.dumps(also_mentioned or []),
                    self._now(),
                ),
            )
            con.commit()
            return int(cur.lastrowid or 0)
        finally:
            con.close()

    def list_messages(self, course_id: str, limit: int = 100) -> list[dict]:
        import json as _json

        con = self._con()
        try:
            rows = con.execute(
                "SELECT * FROM messages WHERE course_id=? ORDER BY created_at ASC, id ASC"
                " LIMIT ?",
                (course_id, max(1, int(limit or 100))),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                raw_primary = d.pop("primary_source_json", "") or ""
                raw_also = d.pop("also_mentioned_json", "") or ""
                try:
                    d["primary_source"] = _json.loads(raw_primary) if raw_primary else None
                except Exception:
                    d["primary_source"] = None
                try:
                    d["also_mentioned_in"] = _json.loads(raw_also) if raw_also else []
                except Exception:
                    d["also_mentioned_in"] = []
                out.append(d)
            return out
        finally:
            con.close()

    def clear_messages(self, course_id: str) -> int:
        con = self._con()
        try:
            cur = con.execute("DELETE FROM messages WHERE course_id=?", (course_id,))
            con.commit()
            return int(cur.rowcount or 0)
        finally:
            con.close()
