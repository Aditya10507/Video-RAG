"""Eval database: SQLite tables + migrations. Raw sqlite3, like the app registry."""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
  id TEXT PRIMARY KEY, name TEXT, version_hash TEXT,
  n_questions INT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS golden_questions (
  dataset_id TEXT REFERENCES datasets(id), id TEXT, question TEXT NOT NULL,
  reference_answer TEXT NULL, question_type TEXT NOT NULL,
  answerable INT NOT NULL, requires_visual INT NOT NULL DEFAULT 0,
  key_terms TEXT NULL, gt_segments TEXT NOT NULL, split TEXT NULL,
  PRIMARY KEY (dataset_id, id)
);
CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY, label TEXT NULL, dataset_id TEXT, split_filter TEXT NULL,
  status TEXT NOT NULL, config TEXT NOT NULL, config_hash TEXT, git_sha TEXT,
  judge_model TEXT NULL, started_at TEXT, finished_at TEXT, heartbeat_at TEXT,
  updated_at TEXT, rev INT NOT NULL DEFAULT 0, n_total INT,
  n_answered INT DEFAULT 0, n_scored INT DEFAULT 0,
  cancel_requested INT DEFAULT 0, error TEXT NULL
);
CREATE TABLE IF NOT EXISTS eval_results (
  id INTEGER PRIMARY KEY, run_id TEXT REFERENCES eval_runs(id), question_id TEXT,
  status TEXT NOT NULL, scoring_status TEXT NOT NULL, answer TEXT NULL,
  citations TEXT NULL, retrieved TEXT NULL, timings_ms TEXT NULL,
  usage TEXT NULL, cost_usd REAL NULL, failure_cause TEXT NULL,
  error TEXT NULL, answered_at TEXT NULL,
  UNIQUE(run_id, question_id)
);
CREATE TABLE IF NOT EXISTS eval_scores (
  result_id INTEGER REFERENCES eval_results(id), metric TEXT,
  value REAL NULL, detail TEXT NULL, judge_model TEXT NULL,
  prompt_version TEXT NULL, PRIMARY KEY (result_id, metric)
);
CREATE TABLE IF NOT EXISTS judge_cache (
  key TEXT PRIMARY KEY, response TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_run ON eval_results(run_id);
CREATE INDEX IF NOT EXISTS idx_scores_metric ON eval_scores(metric, result_id);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30.0)
    con.row_factory = sqlite3.Row
    with contextlib.suppress(sqlite3.Error):
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def bump(con: sqlite3.Connection, run_id: str) -> None:
    con.execute(
        "UPDATE eval_runs SET rev = rev + 1, updated_at = ? WHERE id = ?",
        (now(), run_id),
    )


def set_run(con: sqlite3.Connection, run_id: str, **fields) -> None:
    fields["updated_at"] = now()
    # Keys are code-controlled kwargs, never user input.
    keys = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE eval_runs SET {keys} WHERE id = ?", (*fields.values(), run_id))  # noqa: S608
    con.execute("UPDATE eval_runs SET rev = rev + 1 WHERE id = ?", (run_id,))
    con.commit()


def new_id() -> str:
    return uuid.uuid4().hex
