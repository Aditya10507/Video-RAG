"""Eval REST API + SSE (spec section 9). Aggregates are computed on read from eval.db."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..eval import aggregates as A
from ..eval.db import connect
from .routes import err
from .security import api_key_header, check_key

router = APIRouter(prefix="/api")
_cancels: dict[str, threading.Event] = {}
_recovered = False


def _settings(request: Request):
    return request.app.state.container.settings


def _container(request: Request):
    return request.app.state.container


def _eval_db(request: Request) -> str:
    return str(Path(_settings(request).data_dir) / "eval.db")


def _yaml(request: Request, name: str) -> dict:
    root = Path(__file__).resolve().parents[4] / "backend" / "eval"
    return yaml.safe_load((root / name).read_text(encoding="utf-8")) or {}


def _check(request: Request, key) -> None:
    check_key(_settings(request), key)


def _ensure_recovery(request: Request) -> None:
    global _recovered
    if _recovered:
        return
    _recovered = True
    A.mark_stale_failed(_eval_db(request), 60)


def _run_dict(con, run_id: str, stale_after: int = 60) -> dict | None:
    run = A.run_row(con, run_id)
    if run is None:
        return None
    run["config"] = json.loads(run.get("config") or "{}")
    run["display_status"] = A.effective_status(run, stale_after)
    return run


@router.get("/health")
def health(request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    try:
        con = connect(_eval_db(request))
        con.execute("SELECT 1").fetchone()
        con.close()
        return {"status": "ok", "eval_db": "ok"}
    except Exception as e:
        return JSONResponse({"status": "degraded", "eval_db": str(e)[:200]}, status_code=500)


@router.get("/config/dashboard")
def dashboard_config(request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    return {
        "metrics": _yaml(request, "metrics.yaml").get("metrics", {}),
        "targets": _yaml(request, "targets.yaml"),
        "dashboard": _yaml(request, "dashboard.yaml"),
    }


@router.get("/datasets")
def datasets(request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    _ensure_recovery(request)
    con = connect(_eval_db(request))
    try:
        return {"datasets": [dict(r) for r in con.execute(
            "SELECT * FROM datasets ORDER BY created_at DESC").fetchall()]}
    finally:
        con.close()


@router.get("/runs")
def runs(request: Request, x_api_key=Depends(api_key_header),
         limit: int = 20, status: str | None = None):
    _check(request, x_api_key)
    _ensure_recovery(request)
    con = connect(_eval_db(request))
    try:
        q = "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?"
        args: tuple = (max(1, min(limit, 100)),)
        if status:
            q = "SELECT * FROM eval_runs WHERE status = ? ORDER BY started_at DESC LIMIT ?"
            args = (status, args[0])
        out = []
        for r in con.execute(q, args).fetchall():
            d = dict(r)
            d["config"] = json.loads(d.get("config") or "{}")
            d["display_status"] = A.effective_status(d, 60)
            out.append(d)
        return {"runs": out}
    finally:
        con.close()


@router.post("/runs", status_code=202)
def start_run(body: dict, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    _ensure_recovery(request)
    from ..eval.runner import run_dataset as _run

    db_path = _eval_db(request)
    con = connect(db_path)
    try:
        busy = con.execute(
            "SELECT id FROM eval_runs WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if busy:
            return err("run_busy", f"run {busy[0]} is still active", 409)
        dataset_path = body.get("dataset_path") or ""
        dataset_id = body.get("dataset_id") or ""
        if dataset_id and not dataset_path:
            row = con.execute("SELECT * FROM datasets WHERE id = ?",
                              (dataset_id,)).fetchone()
            if row is None:
                return err("bad_request", f"unknown dataset_id {dataset_id}", 400)
            return err("bad_request", "dataset_id reruns need dataset_path for now", 400)
        if not dataset_path or not Path(dataset_path).exists():
            return err("bad_request", "dataset_path must point at a golden .jsonl file", 400)
    finally:
        con.close()
    c = _container(request)
    cfg = _yaml(request, "eval.yaml")
    pricing = (_yaml(request, "pricing.yaml") or {}).get("models", {})
    from ..eval import datasets as DS
    from ..eval.db import new_id

    rows, errors = DS.load(dataset_path)
    if errors:
        return err("bad_request", "dataset invalid:\n" + "\n".join(errors[:10]), 400)
    run_id = new_id()
    cancel = threading.Event()
    _cancels[run_id] = cancel

    def work():
        try:
            _run(c.settings,
                 {"embedder": c.embedder, "store": c.store,
                  "reranker": c.reranker, "llm": c.llm},
                 dataset_path, body.get("label") or Path(dataset_path).name,
                 body.get("split"), body.get("overrides") or {}, db_path, cfg,
                 cancel, None, body.get("course_id"), pricing)
        finally:
            _cancels.pop(run_id, None)

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        run = A.run_row(con, run_id)
        if run is None:
            return err("not_found", "unknown run", 404)
        con.execute("UPDATE eval_runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
        con.commit()
        ev = _cancels.get(run_id)
        if ev is not None:
            ev.set()
        return {"run_id": run_id, "cancel_requested": True}
    finally:
        con.close()


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        run = _run_dict(con, run_id)
        if run is None:
            return err("not_found", "unknown run", 404)
        run["aggregates"] = A.aggregates(con, run_id)
        run["latency"] = A.latency_p(con, run_id)
        return run
    finally:
        con.close()


@router.get("/runs/{run_id}/compare/{other_id}")
def compare_runs(run_id: str, other_id: str, request: Request,
                 x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        if A.run_row(con, run_id) is None or A.run_row(con, other_id) is None:
            return err("not_found", "unknown run", 404)
        return {"run_id": run_id, "comparison_id": other_id,
                "metrics": A.compare_detailed(con, run_id, other_id)}
    finally:
        con.close()


@router.get("/runs/{run_id}/breakdown")
def breakdown(run_id: str, request: Request, x_api_key=Depends(api_key_header),
              by: str = "question_type", metric: str = "answer_correctness"):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        if A.run_row(con, run_id) is None:
            return err("not_found", "unknown run", 404)
        return {"run_id": run_id, "by": by, "metric": metric,
                "groups": A.breakdown(con, run_id, by, metric)}
    finally:
        con.close()


@router.get("/runs/{run_id}/failures")
def failures(run_id: str, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        if A.run_row(con, run_id) is None:
            return err("not_found", "unknown run", 404)
        return {"run_id": run_id, **A.failures(con, run_id)}
    finally:
        con.close()


@router.get("/runs/{run_id}/results")
def results(run_id: str, request: Request, x_api_key=Depends(api_key_header),
            sort: str = "answer_correctness", order: str = "asc",
            limit: int = 20, offset: int = 0, cause: str | None = None,
            question_type: str | None = None):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        run = A.run_row(con, run_id)
        if run is None:
            return err("not_found", "unknown run", 404)
        rows = con.execute(
            "SELECT r.*, q.question, q.question_type, q.answerable FROM eval_results r"
            " JOIN golden_questions q ON q.dataset_id = ? AND q.id = r.question_id"
            " WHERE r.run_id = ?", (run["dataset_id"], run_id)).fetchall()
        scores: dict[int, dict] = {}
        for m, v, rid in con.execute(
            "SELECT metric, value, result_id FROM eval_scores WHERE result_id IN"
            " (SELECT id FROM eval_results WHERE run_id = ?)", (run_id,)).fetchall():
            scores.setdefault(rid, {})[m] = v
        out = []
        for r in rows:
            d = dict(r)
            d["scores"] = scores.get(r["id"], {})
            if cause and d.get("failure_cause") != cause:
                continue
            if question_type and d.get("question_type") != question_type:
                continue
            out.append(d)
        # None-safe sort: scored rows first in the requested direction, Nones last.
        scored = [d for d in out if d["scores"].get(sort) is not None]
        missing = [d for d in out if d["scores"].get(sort) is None]
        scored.sort(key=lambda d: d["scores"][sort], reverse=(order != "asc"))
        out = scored + missing
        total = len(out)
        return {"run_id": run_id, "total": total,
                "results": out[offset:offset + max(1, min(limit, 100))]}
    finally:
        con.close()


@router.get("/results/{result_id}")
def result_trace(result_id: int, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        r = con.execute("SELECT * FROM eval_results WHERE id = ?", (result_id,)).fetchone()
        if r is None:
            return err("not_found", "unknown result", 404)
        template = _yaml(request, "dashboard.yaml").get(
            "video_url_template", "https://www.youtube.com/watch?v={video_id}&t={start_s}s")
        trace = A.trace_result(con, r["run_id"], result_id, template)
        if trace is None:
            return err("not_found", "unknown result", 404)
        return trace
    finally:
        con.close()


@router.get("/trend")
def trend(request: Request, x_api_key=Depends(api_key_header),
          metrics: str = "recall_at_k,faithfulness,answer_correctness", last: int = 10):
    _check(request, x_api_key)
    con = connect(_eval_db(request))
    try:
        names = [m.strip() for m in metrics.split(",") if m.strip()][:8]
        return {"trend": A.trend(con, names, max(1, min(last, 50)))}
    finally:
        con.close()


@router.get("/runs/{run_id}/export.csv")
def export_csv(run_id: str, request: Request, x_api_key=Depends(api_key_header)):
    _check(request, x_api_key)
    from fastapi.responses import PlainTextResponse

    con = connect(_eval_db(request))
    try:
        run = A.run_row(con, run_id)
        if run is None:
            return err("not_found", "unknown run", 404)
        rows = con.execute(
            "SELECT r.question_id, q.question, q.question_type, q.answerable,"
            " r.status, r.answer, r.failure_cause FROM eval_results r"
            " JOIN golden_questions q ON q.dataset_id = ? AND q.id = r.question_id"
            " WHERE r.run_id = ? ORDER BY r.id", (run["dataset_id"], run_id)).fetchall()
        scores: dict[str, dict] = {}
        for qid, m, v in con.execute(
            "SELECT r.question_id, s.metric, s.value FROM eval_scores s"
            " JOIN eval_results r ON r.id = s.result_id WHERE r.run_id = ?",
            (run_id,)).fetchall():
            scores.setdefault(qid, {})[m] = v
        metric_names = sorted({m for s in scores.values() for m in s})
        lines = ["question_id,question,type,answerable,status,failure_cause,"
                 + ",".join(metric_names)]
        for r in rows:
            s = scores.get(r["question_id"], {})
            cells = [r["question_id"], _csv(r["question"]), _csv(r["question_type"]),
                     str(int(r["answerable"] or 0)), r["status"], r["failure_cause"] or ""]
            cells += ["" if s.get(m) is None else str(round(float(s[m]), 4)) for m in metric_names]
            lines.append(",".join(cells))
        return PlainTextResponse("\n".join(lines), media_type="text/csv")
    finally:
        con.close()


def _csv(text: str | None) -> str:
    t = (text or "").replace('"', '""')
    return f'"{t}"' if ("," in t or '"' in t or "\n" in t) else t


def poll_revs(db_path: str, since: dict[str, int]) -> dict[str, dict]:
    """One poll: runs whose rev changed since the snapshot. Testable unit."""
    con = connect(db_path)
    try:
        cur = {r["id"]: r["rev"] for r in con.execute(
            "SELECT id, rev FROM eval_runs").fetchall()}
    finally:
        con.close()
    changed = {}
    for rid, rev in cur.items():
        if since.get(rid) != rev:
            con = connect(db_path)
            try:
                r = A.run_row(con, rid) or {}
            finally:
                con.close()
            changed[rid] = {"rev": rev, "status": r.get("status"),
                            "n_total": r.get("n_total"), "n_answered": r.get("n_answered"),
                            "n_scored": r.get("n_scored"), "updated_at": r.get("updated_at")}
    return changed


async def eval_event_stream(db_path: str):
    """Infinite SSE body. Ends cleanly on cancellation (client disconnect)."""
    yield ": connected\n\n"
    since: dict[str, int] = {}
    quiet = 0
    try:
        while True:
            try:
                changed = await asyncio.to_thread(poll_revs, db_path, since)
            except Exception:
                changed = {}
            for rid, info in changed.items():
                since[rid] = info["rev"]
                yield f"data: {json.dumps({'run_id': rid, **info})}\n\n"
            quiet += 1
            if quiet >= 15:
                yield ": heartbeat\n\n"
                quiet = 0
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass


@router.get("/events")
async def events(request: Request, x_api_key=Depends(api_key_header)):
    key = x_api_key or request.query_params.get("api_key")
    try:
        check_key(_settings(request), key)
    except HTTPException:
        return err("unauthorized", "invalid API key", 401)
    return StreamingResponse(eval_event_stream(_eval_db(request)),
                             media_type="text/event-stream")
