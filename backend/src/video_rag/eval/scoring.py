"""Deterministic per-result scoring (Phase 2). Judge metrics arrive in Phase 3."""

from __future__ import annotations

import json

from .db import bump, now
from .scorers import retrieval as R
from .scorers import system as S
from .scorers import timestamps as T
from .scorers.failure_cause import classify


def score_result(golden: dict, retrieved: list, citations: list, answer: str,
                 abstained: bool, timings: dict, usage: dict,
                 cfg: dict, pricing: dict) -> dict:
    """Returns {metric: (value, detail)}. Only defined values are returned."""
    _ = answer  # text is judge input (Phase 3); deterministic rules use abstained
    out: dict = {}
    k = int(cfg.get("k", 8))
    k_max = int(cfg.get("k_max", k))
    ov = float(cfg.get("min_overlap_s", 1.0))
    tol = float(cfg.get("timestamp_tolerance_s", 3.0))
    gt = golden.get("gt_segments") or []
    if golden.get("answerable"):
        flags = R.relevant_flags(retrieved, gt, ov)
        covered = sum(
            1 for g in gt if any(R.overlap(c, g) >= ov for c in retrieved[:k])
        )
        out["recall_at_k"] = (covered / len(gt) if gt else 0.0,
                              {"covered": covered, "of": len(gt)})
        out["hit_at_k"] = (R.hit_at_k(flags, k), {})
        rank = next(
            (c.get("rank", 0) for c in retrieved[:k_max]
             if any(R.overlap(c, g) >= ov for g in gt)), 0,
        )
        out["mrr"] = (1.0 / rank if rank else 0.0, {"rank": rank})
        prec = R.precision_at_k(flags, k)
        if prec is not None:
            rel = sum(1 for f in flags[:k] if f)
            out["context_precision_at_k"] = (prec, {"relevant": rel, "of": len(flags[:k])})
        ts = T.timestamp_hit_rate(citations, gt, tol)
        if ts is not None:
            hits = round(ts * len(citations)) if citations else 0
            out["timestamp_hit_rate"] = (ts, {"hits": hits, "of": len(citations)})
        out["false_abstain_rate"] = (1.0 if abstained else 0.0, {"abstained": abstained})
    else:
        out["abstain_accuracy"] = (1.0 if abstained else 0.0, {"abstained": abstained})
    total = (timings or {}).get("total")
    if total is not None:
        out["latency_ms"] = (float(total), dict(timings))
    cost = S.cost_usd(usage or {}, pricing or {})
    if cost is not None:
        out["cost_usd"] = (cost, dict(usage or {}))
    return out


def score_run(con, run_id: str, gold_by_id: dict, cfg: dict, pricing: dict) -> int:
    """Score every answered result deterministically. Returns n scored."""
    n = 0
    rows = con.execute(
        "SELECT * FROM eval_results WHERE run_id = ? AND status = 'done'", (run_id,)
    ).fetchall()
    for r in rows:
        golden = gold_by_id.get(r["question_id"], {})
        scored = score_result(
            golden, json.loads(r["retrieved"] or "[]"),
            json.loads(r["citations"] or "[]"), r["answer"] or "",
            not (r["answer"] or "").strip(),
            json.loads(r["timings_ms"] or "{}"), json.loads(r["usage"] or "{}"),
            cfg, pricing,
        )
        for metric, (value, detail) in scored.items():
            con.execute(
                "INSERT OR REPLACE INTO eval_scores (result_id, metric, value, detail)"
                " VALUES (?, ?, ?, ?)",
                (r["id"], metric, value, json.dumps(detail)),
            )
        cost = dict(scored).get("cost_usd", (None,))[0]
        con.execute(
            "UPDATE eval_results SET scoring_status = 'done', cost_usd = ? WHERE id = ?",
            (cost, r["id"]),
        )
        n += 1
    con.execute("UPDATE eval_runs SET n_scored = ?, heartbeat_at = ? WHERE id = ?",
                (n, now(), run_id))
    bump(con, run_id)
    con.commit()
    return n


def _judge_one(judge, golden: dict, retrieved: list, citations: list,
               answer: str, abstained: bool, cfg: dict) -> dict:
    """Judge metrics for one result. Skipped populations are absent, never zero."""
    _ = cfg
    out: dict = {}
    answerable = bool(golden.get("answerable"))
    chunks = [{"title": "", "text": c.get("text", "")} for c in retrieved]
    if not abstained and answer.strip():
        faith, detail = judge.faithfulness(answer, chunks)
        out["faithfulness"] = (faith, detail)
        rel, detail = judge.relevancy(golden.get("question", ""), answer)
        out["answer_relevancy"] = (rel, detail)
    if answerable:
        ref = golden.get("reference_answer") or ""
        if answer.strip() and ref.strip():
            correct, detail = judge.correctness(answer, ref)
            out["answer_correctness"] = (correct, detail)
        elif not answer.strip():
            out["answer_correctness"] = (0.0, {"rule": "empty answer"})
        if ref.strip() and retrieved:
            recall, detail = judge.context_recall(ref, chunks)
            out["context_recall"] = (recall, detail)
        cited = [c for c in retrieved
                 if c.get("chunk_id") in {x.get("chunk_id") for x in citations}]
        if cited and answer.strip():
            acc, detail = judge.citation_accuracy(
                answer, [{"chunk_id": c.get("chunk_id"), "text": c.get("text", "")}
                         for c in cited])
            out["citation_accuracy"] = (acc, detail)
    return out


def score_judge_run(con, run_id: str, gold_by_id: dict, judge, cfg: dict,
                    cancel=None, workers: int = 2) -> tuple[int, int]:
    """Judge pass over answered results + failure causes. Returns (scored, errors)."""
    import concurrent.futures

    rows = con.execute(
        "SELECT * FROM eval_results WHERE run_id = ? AND status = 'done'", (run_id,)
    ).fetchall()
    scored, errors = 0, 0

    def one(r) -> tuple:
        golden = gold_by_id.get(r["question_id"], {})
        retrieved = json.loads(r["retrieved"] or "[]")
        citations = json.loads(r["citations"] or "[]")
        abstained = not (r["answer"] or "").strip()
        try:
            metrics = _judge_one(judge, golden, retrieved, citations,
                                 r["answer"] or "", abstained, cfg)
        except Exception as e:
            return (r["id"], None, f"judge: {e}"[:300])
        return (r["id"], (metrics, golden, retrieved, citations, abstained), "")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(one, r): r for r in rows}
        for fut in concurrent.futures.as_completed(futs):
            if cancel is not None and cancel.is_set():
                for f in futs:
                    f.cancel()
                break
            rid, payload, error = fut.result()
            if payload is None:
                errors += 1
                con.execute("UPDATE eval_results SET error = ? WHERE id = ?",
                            (error, rid))
                continue
            metrics, golden, retrieved, citations, abstained = payload
            for metric, (value, detail) in metrics.items():
                con.execute(
                    "INSERT OR REPLACE INTO eval_scores"
                    " (result_id, metric, value, detail, judge_model, prompt_version)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, metric, value, json.dumps(detail),
                     judge.model, "v1"),
                )
            cause = _maybe_cause(golden, retrieved, citations, abstained, metrics, cfg)
            if cause:
                con.execute("UPDATE eval_results SET failure_cause = ? WHERE id = ?",
                            (cause, rid))
            scored += 1
    con.execute("UPDATE eval_runs SET n_scored = ?, heartbeat_at = ? WHERE id = ?",
                (scored, now(), run_id))
    bump(con, run_id)
    con.commit()
    return scored, errors


def _maybe_cause(golden: dict, retrieved: list, citations: list, abstained: bool,
                 metrics: dict, cfg: dict) -> str | None:
    """Cause only for failed questions; needs correctness to decide failure."""
    correct = dict(metrics).get("answer_correctness", (None,))[0]
    answerable = bool(golden.get("answerable"))
    if answerable:
        if correct is None or correct >= float(cfg.get("failure_correctness_threshold", 0.6)):
            return None
    elif abstained:
        return None
    scores = {m: v for m, (v, _d) in metrics.items()}
    scores["abstained"] = abstained
    top_text = " ".join(c.get("text", "") for c in retrieved)
    try:
        return classify(golden, retrieved, scores, top_text, cfg)
    except ValueError:
        return "unclassified"
