"""Read-side aggregates over eval.db. AVG/COUNT skip NULLs (missing is not zero)."""

from __future__ import annotations

import json

from .scorers import retrieval as R
from .scorers import system as S


def aggregates(con, run_id: str) -> dict:
    """{metric: {value, n}} means over defined values only."""
    out = {}
    for metric, value, n in con.execute(
        "SELECT s.metric, AVG(s.value), COUNT(s.value) FROM eval_scores s"
        " JOIN eval_results r ON r.id = s.result_id"
        " WHERE r.run_id = ? GROUP BY s.metric",
        (run_id,),
    ).fetchall():
        out[metric] = {"value": value, "n": n}
    return out


def latency_p(con, run_id: str, pcts=(50, 95)) -> dict:
    vals = [
        r[0] for r in con.execute(
            "SELECT s.value FROM eval_scores s JOIN eval_results r ON r.id = s.result_id"
            " WHERE r.run_id = ? AND s.metric = 'latency_ms' AND s.value IS NOT NULL",
            (run_id,),
        ).fetchall()
    ]
    return {f"p{p}": S.percentile(vals, p) for p in pcts}


def run_row(con, run_id: str) -> dict | None:
    r = con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(r) if r else None


def effective_status(run: dict, stale_after_s: int) -> str:
    """running + silent heartbeat -> stalled (display-only; see mark_stale_failed)."""
    import time

    if run.get("status") != "running":
        return run.get("status", "")
    try:
        hb = time.mktime(time.strptime(run.get("heartbeat_at", ""), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return "stalled"
    return "stalled" if time.time() - hb > stale_after_s else "running"


def mark_stale_failed(db_path: str, stale_after_s: int) -> int:
    """Startup recovery: flip silent running runs to failed. Returns count."""
    from .db import connect, now

    con = connect(db_path)
    try:
        ids = [r["id"] for r in con.execute(
            "SELECT * FROM eval_runs WHERE status = 'running'").fetchall()
            if effective_status(dict(r), stale_after_s) == "stalled"]
        for rid in ids:
            con.execute(
                "UPDATE eval_runs SET status = 'failed', error = 'stalled: no heartbeat',"
                " finished_at = ?, rev = rev + 1 WHERE id = ?", (now(), rid))
        con.commit()
        return len(ids)
    finally:
        con.close()


def trend(con, metrics: list[str], last_n: int) -> list[dict]:
    runs = con.execute(
        "SELECT id, label, started_at FROM eval_runs WHERE status = 'completed'"
        " ORDER BY started_at DESC LIMIT ?", (max(1, last_n),)).fetchall()
    out = []
    for r in reversed(runs):
        agg = aggregates(con, r["id"])
        out.append({"run_id": r["id"], "label": r["label"],
                    "metrics": {m: agg.get(m, {"value": None, "n": 0}) for m in metrics}})
    return out


def compare(a_agg: dict, b_agg: dict) -> dict:
    """Per metric: both sides + delta (current - comparison). No CI yet (Phase 7)."""
    out = {}
    for metric in sorted(set(a_agg) | set(b_agg)):
        a = a_agg.get(metric, {"value": None, "n": 0})
        b = b_agg.get(metric, {"value": None, "n": 0})
        both = a["value"] is not None and b["value"] is not None
        delta = (a["value"] - b["value"]) if both else None
        out[metric] = {"current": a, "comparison": b, "delta": delta,
                       "significant": False}
    return out


def paired_values(con, run_id: str, metric: str) -> dict:
    """Per-question non-null values for one metric: {question_id: value}."""
    return {
        qid: v for qid, v in con.execute(
            "SELECT r.question_id, s.value FROM eval_scores s"
            " JOIN eval_results r ON r.id = s.result_id"
            " WHERE r.run_id = ? AND s.metric = ? AND s.value IS NOT NULL",
            (run_id, metric)).fetchall()
    }


def bootstrap_ci(a_vals: list, b_vals: list, b: int = 1000, seed: int = 42) -> dict:
    """Paired bootstrap 95% CI for mean(a - b). Deterministic seed."""
    import random

    n = len(a_vals)
    if n == 0:
        return {"delta": None, "ci": None, "significant": False, "n_paired": 0,
                "within_noise": True}
    rng = random.Random(seed)  # noqa: S311 - determinism is the point here, not secrecy
    base = [x - y for x, y in zip(a_vals, b_vals, strict=True)]
    point = sum(base) / n
    dist = sorted(sum(rng.choice(base) for _ in range(n)) / n for _ in range(max(1, b)))

    def at(p: float) -> float:
        return dist[max(0, min(len(dist) - 1, int(p * len(dist))))]

    lo, hi = at(0.025), at(0.975)
    significant = lo > 0 or hi < 0
    return {"delta": point, "ci": {"lo": lo, "hi": hi}, "significant": significant,
            "n_paired": n, "within_noise": not significant}


def compare_detailed(con, run_id: str, other_id: str, b: int = 1000) -> dict:
    """Compare with paired-bootstrap CIs over questions present in both runs."""
    a_agg, b_agg = aggregates(con, run_id), aggregates(con, other_id)
    out = {}
    for metric in sorted(set(a_agg) | set(b_agg)):
        a = a_agg.get(metric, {"value": None, "n": 0})
        c = b_agg.get(metric, {"value": None, "n": 0})
        av = paired_values(con, run_id, metric)
        cv = paired_values(con, other_id, metric)
        shared = sorted(set(av) & set(cv))
        boot = bootstrap_ci([av[q] for q in shared], [cv[q] for q in shared], b)
        both = a["value"] is not None and c["value"] is not None
        entry = {"current": a, "comparison": c,
                 "delta": (a["value"] - c["value"]) if both else None}
        entry.update({k: v for k, v in boot.items() if k != "delta"})
        out[metric] = entry
    return out


def breakdown(con, run_id: str, by: str, metric: str) -> list[dict]:
    """Per-group {value, n} for by=question_type|split. Unknown groups -> []."""
    col = {"question_type": "q.question_type", "split": "q.split"}.get(by)
    if col is None:
        return []
    return [
        {"group": g, "value": v, "n": n}
        for g, v, n in con.execute(
            f"SELECT {col}, AVG(s.value), COUNT(s.value) FROM eval_scores s"  # noqa: S608
            " JOIN eval_results r ON r.id = s.result_id"
            " JOIN eval_runs ru ON ru.id = r.run_id"
            " JOIN golden_questions q ON q.dataset_id = ru.dataset_id AND q.id = r.question_id"
            " WHERE r.run_id = ? AND s.metric = ? AND s.value IS NOT NULL"
            f" GROUP BY {col} ORDER BY {col}",  # noqa: S608
            (run_id, metric),
        ).fetchall()
    ]


def failures(con, run_id: str) -> dict:
    rows = con.execute(
        "SELECT failure_cause, COUNT(*) FROM eval_results"
        " WHERE run_id = ? AND failure_cause IS NOT NULL GROUP BY failure_cause",
        (run_id,)).fetchall()
    by_cause = {c: n for c, n in rows}
    total = con.execute(
        "SELECT COUNT(*) FROM eval_results WHERE run_id = ?", (run_id,)).fetchone()[0]
    return {"by_cause": by_cause, "total_failed": sum(by_cause.values()), "total": total}


def trace_result(con, run_id: str, result_id: int, url_template: str) -> dict | None:
    """Full per-question trace with relevant/hit flags and video links."""
    r = con.execute(
        "SELECT * FROM eval_results WHERE id = ? AND run_id = ?", (result_id, run_id)
    ).fetchone()
    if r is None:
        return None
    run = run_row(con, run_id) or {}
    q = con.execute(
        "SELECT * FROM golden_questions WHERE dataset_id = ? AND id = ?",
        (run.get("dataset_id"), r["question_id"])).fetchone()
    q = dict(q) if q else {}
    gt = json.loads(q.get("gt_segments") or "[]")
    chunks = json.loads(r["retrieved"] or "[]")
    flags = R.relevant_flags(chunks, gt, 1.0)
    cites = json.loads(r["citations"] or "[]")
    for c in cites:
        seg = {"video_id": c.get("video_id"), "start_s": c.get("start_s", 0),
               "end_s": c.get("end_s", c.get("start_s", 0))}
        c["hit"] = any(
            seg["video_id"] == g.get("video_id")
            and min(seg["end_s"], float(g.get("end_s", 0)))
            - max(seg["start_s"], float(g.get("start_s", 0))) > 0 for g in gt)
        c["url"] = url_template.format(video_id=c.get("video_id"),
                                       start_s=int(float(c.get("start_s", 0))))
    scores = [
        {"metric": m, "value": v, "detail": json.loads(d or "{}"),
         "judge_model": jm, "prompt_version": pv}
        for m, v, d, jm, pv in con.execute(
            "SELECT metric, value, detail, judge_model, prompt_version"
            " FROM eval_scores WHERE result_id = ?", (result_id,)).fetchall()
    ]
    return {
        "result_id": result_id, "question_id": r["question_id"],
        "question": q.get("question", ""), "reference_answer": q.get("reference_answer"),
        "question_type": q.get("question_type"), "answerable": bool(q.get("answerable")),
        "gt_segments": gt, "answer": r["answer"], "status": r["status"],
        "failure_cause": r["failure_cause"], "error": r["error"],
        "timings_ms": json.loads(r["timings_ms"] or "{}"),
        "usage": json.loads(r["usage"] or "{}"),
        "chunks": [{**c, "relevant": f} for c, f in zip(chunks, flags, strict=True)],
        "citations": cites, "scores": scores,
    }
