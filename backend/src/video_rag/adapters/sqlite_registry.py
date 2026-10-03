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
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
"""


class SqliteRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        con.executescript(SCHEMA)
        self._restore_original_video_columns(con)
        con.commit()
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
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        return con

    def list_courses(self) -> list[dict]:
        con = self._con()
        rows = con.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
        con.close()
        return [dict(r) for r in rows]

    def list_course_videos(self, course_id: str) -> list[dict]:
        con = self._con()
        rows = con.execute(
            "SELECT v.video_id, v.title, v.status, v.duration_seconds FROM videos v "
            "JOIN course_videos cv ON cv.video_id=v.video_id "
            "WHERE cv.course_id=? ORDER BY cv.position",
            (course_id,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    # --- Write side (used by ingestion) -------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def upsert_course(
        self, course_id: str, source_url: str, title: str, video_count: int
    ) -> None:
        now = self._now()
        con = self._con()
        con.execute(
            "INSERT INTO courses (course_id, source_url, title, video_count, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 'ingesting', ?, ?)"
            " ON CONFLICT(course_id) DO UPDATE SET source_url=excluded.source_url,"
            " title=excluded.title, video_count=excluded.video_count,"
            " status='ingesting', updated_at=excluded.updated_at",
            (course_id, source_url, title, video_count, now, now),
        )
        con.commit()
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
        con.execute(
            "INSERT INTO videos (video_id, title, duration_seconds, status, error,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(video_id) DO UPDATE SET title=excluded.title,"
            " duration_seconds=excluded.duration_seconds, status=excluded.status,"
            " error=excluded.error, updated_at=excluded.updated_at",
            (video_id, title, duration_sec, status, error, now),
        )
        con.commit()
        con.close()

    def link_video_to_course(
        self, course_id: str, video_id: str, position: int
    ) -> None:
        con = self._con()
        con.execute(
            "INSERT OR IGNORE INTO course_videos (course_id, video_id, position)"
            " VALUES (?, ?, ?)",
            (course_id, video_id, position),
        )
        con.commit()
        con.close()

    def set_course_status(self, course_id: str, status: str) -> None:
        con = self._con()
        con.execute(
            "UPDATE courses SET status=?, updated_at=? WHERE course_id=?",
            (status, self._now(), course_id),
        )
        con.commit()
        con.close()

    def mark_video_status(
        self, video_id: str, status: str, error: str = ""
    ) -> None:
        con = self._con()
        con.execute(
            "UPDATE videos SET status=?, error=?, updated_at=? WHERE video_id=?",
            (status, error, self._now(), video_id),
        )
        con.commit()
        con.close()
