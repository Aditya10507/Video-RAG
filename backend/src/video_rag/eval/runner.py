"""Run lifecycle: golden questions through the adapter into eval.db.

One failing question never aborts the run. CLI and the API share this code.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import datasets
from .adapter import VideoRagAdapter
from .db import bump, connect, new_id, now, set_run
from .scorers.failure_cause import check_thresholds
from .scorers.judge import Judge
from .scoring import score_judge_run, score_run


def _git_sha() -> str:
    if shutil.which("git") is None:
        return ""
    try:
        # Plain "git" is intentional (shutil.which guard above).
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, timeout=5  # noqa: S607
        ).strip()
    except Exception:
        return ""


def _snapshot(settings, eval_cfg: dict, overrides: dict) -> dict:
    return {
        "chunk_window_s": settings.chunk_window_seconds,
        "chunk_overlap_s": settings.chunk_overlap_seconds,
        "retrieve_top_k": settings.retrieve_top_k,
        "rerank_top_k": settings.rerank_top_k,
        "threshold_high": settings.threshold_high,
        "threshold_low": settings.threshold_low,
        "embedding_model": settings.jina_model,
        "vector_backend": settings.vector_backend,
        "llm_model": settings.llm_model,
        "eval": eval_cfg,
        "overrides": overrides or {},
        "citation_source": "pipeline",
    }


def run_dataset(
    settings,
    components: dict,
    dataset_path: str,
    label: str,
    split: str | None,
    overrides: dict,
    db_path: str,
    eval_cfg: dict,
    cancel: threading.Event | None = None,
    on_progress=None,
    course_default: str | None = None,
    pricing: dict | None = None,
) -> str:
    rows, errors = datasets.load(dataset_path)
    if errors:
        raise ValueError("dataset invalid:\n" + "\n".join(errors))
    if split:
        rows = [r for r in rows if (r.get("split") or None) == split]
    if not rows:
        raise ValueError(f"dataset has no questions for split={split!r}")
    # Fail fast before spending API calls: judge needs a model, and failure
    # causes need their thresholds (spec section 7 — no guessed values).
    judge, judge_warning = None, ""
    if (settings.judge_model or "").strip():
        missing = check_thresholds(eval_cfg)
        if missing:
            raise ValueError(
                "cannot run with judge: thresholds missing: " + ", ".join(missing)
            )
        judge = Judge(settings, db_path, eval_cfg)
        judge_warning = judge.warning
    con = connect(db_path)
    version = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()[:16]
    ds_id = f"ds_{version}"
    con.execute(
        "INSERT OR IGNORE INTO datasets (id, name, version_hash, n_questions, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (ds_id, dataset_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1], version, len(rows), now()),
    )
    for r in rows:
        con.execute(
            "INSERT OR IGNORE INTO golden_questions (dataset_id, id, question,"
            " reference_answer, question_type, answerable, requires_visual,"
            " key_terms, gt_segments, split) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ds_id, r["id"], r["question"], r.get("reference_answer"),
                r.get("question_type", ""), int(bool(r.get("answerable"))),
                int(bool(r.get("requires_visual", False))),
                json.dumps(r.get("key_terms") or []), json.dumps(r.get("gt_segments") or []),
                r.get("split"),
            ),
        )
    run_id = new_id()
    config = _snapshot(settings, eval_cfg, overrides)
    config["judge_model"] = (settings.judge_model or "").strip()
    config["judge_warning"] = judge_warning
    con.execute(
        "INSERT INTO eval_runs (id, label, dataset_id, split_filter, status, config,"
        " config_hash, git_sha, started_at, heartbeat_at, updated_at, n_total)"
        " VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id, label, ds_id, split, json.dumps(config),
            hashlib.sha256(json.dumps(config).encode()).hexdigest()[:16],
            _git_sha(), now(), now(), now(), len(rows),
        ),
    )
    for r in rows:
        con.execute(
            "INSERT OR IGNORE INTO eval_results (run_id, question_id, status, scoring_status)"
            " VALUES (?, ?, 'pending', 'pending')",
            (run_id, r["id"]),
        )
    con.commit()
    adapter = VideoRagAdapter(
        settings, components["embedder"], components["store"],
        components["reranker"], components["llm"],
    )
    course_of = {}
    for r in rows:
        cid = r.get("course_id") or course_default
        if not cid:
            raise ValueError(f"golden row {r['id']}: no course_id and no --course given")
        course_of[r["id"]] = cid
    done = 0

    def one(row: dict) -> dict:
        t0 = time.perf_counter()
        try:
            resp = adapter.query(
                row["question"], course_of[row["id"]], overrides or None
            )
            return {"answer": resp.answer, "status": "done", "resp": resp,
                    "ms": (time.perf_counter() - t0) * 1000}
        except Exception as e:
            return {"answer": "", "status": "error", "error": str(e)[:500],
                    "ms": (time.perf_counter() - t0) * 1000}

    workers = max(1, int(eval_cfg.get("concurrency", 4)))
    timeout = float(eval_cfg.get("question_timeout_s", 180))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(one, r): r for r in rows}
            for fut in concurrent.futures.as_completed(futs):
                if cancel is not None and cancel.is_set():
                    for f in futs:
                        f.cancel()
                    set_run(con, run_id, status="cancelled", finished_at=now())
                    return run_id
                row, got = futs[fut], None
                try:
                    got = fut.result(timeout=timeout)
                except Exception as e:
                    got = {"answer": "", "status": "error",
                           "error": f"timeout/worker: {e}"[:500], "ms": timeout * 1000}
                _store_result(con, run_id, row, got, course_of)
                done += 1
                set_run(con, run_id, n_answered=done, heartbeat_at=now())
                if on_progress:
                    on_progress(run_id, done, len(rows))
        gold_by_id = {r["id"]: r for r in rows}
        score_run(con, run_id, gold_by_id, eval_cfg, pricing or {})
        if judge is not None:
            score_judge_run(con, run_id, gold_by_id, judge, eval_cfg,
                            cancel=cancel,
                            workers=int(eval_cfg.get("judge_concurrency", 2)))
        set_run(con, run_id, status="completed", finished_at=now(), heartbeat_at=now())
    except Exception as e:
        set_run(con, run_id, status="failed", error=str(e)[:500], finished_at=now())
        raise
    finally:
        con.close()
    return run_id


def rescore_run(db_path: str, run_id: str, settings, eval_cfg: dict) -> tuple[int, int]:
    """Recompute judge scores for a finished run (e.g. after a prompt change)."""
    missing = check_thresholds(eval_cfg)
    if missing:
        raise ValueError("cannot rescore: thresholds missing: " + ", ".join(missing))
    judge = Judge(settings, db_path, eval_cfg)  # raises clearly without a model/key
    con = connect(db_path)
    try:
        run = con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        gold_by_id = {}
        for q in con.execute(
            "SELECT * FROM golden_questions WHERE dataset_id = ?", (run["dataset_id"],)
        ).fetchall():
            gold_by_id[q["id"]] = {
                "id": q["id"], "question": q["question"],
                "reference_answer": q["reference_answer"],
                "question_type": q["question_type"],
                "answerable": bool(q["answerable"]),
                "requires_visual": bool(q["requires_visual"]),
                "key_terms": json.loads(q["key_terms"] or "[]"),
                "gt_segments": json.loads(q["gt_segments"] or "[]"),
            }
        return score_judge_run(con, run_id, gold_by_id, judge, eval_cfg)
    finally:
        con.close()


def _store_result(con, run_id: str, row: dict, got: dict, course_of: dict) -> None:
    resp = got.get("resp")
    retrieved = (
        [
            {"chunk_id": c.chunk_id, "video_id": c.video_id, "start_s": c.start_s,
             "end_s": c.end_s, "text": c.text, "score": c.score, "rank": c.rank,
             "modality": c.modality}
            for c in resp.retrieved
        ]
        if resp
        else []
    )
    citations = (
        [{"video_id": c.video_id, "start_s": c.start_s, "end_s": c.end_s,
          "chunk_id": c.chunk_id} for c in resp.citations]
        if resp
        else []
    )
    usage = dict(resp.usage) if resp else {}
    con.execute(
        "UPDATE eval_results SET status = ?, scoring_status = 'pending', answer = ?,"
        " citations = ?, retrieved = ?, timings_ms = ?, usage = ?,"
        " answered_at = ?, error = ? WHERE run_id = ? AND question_id = ?",
        (
            got["status"], got.get("answer", ""), json.dumps(citations),
            json.dumps(retrieved), json.dumps(resp.timings_ms if resp else {}),
            json.dumps(usage), now(), got.get("error"), run_id, row["id"],
        ),
    )
    con.commit()
    bump(con, run_id)
    con.commit()
