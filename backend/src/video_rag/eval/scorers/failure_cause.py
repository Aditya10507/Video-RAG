"""Failure-cause classifier (heuristic — always shown as 'likely cause')."""

from __future__ import annotations

import difflib

from . import retrieval as R

REQUIRED_KEYS = (
    "failure_correctness_threshold",
    "faithfulness_threshold",
    "boundary_coverage_threshold",
    "asr_fuzzy_ratio",
)


def check_thresholds(cfg: dict) -> list[str]:
    """Keys the classifier needs. Non-empty means the runner must refuse."""
    return [k for k in REQUIRED_KEYS if cfg.get(k) is None]


def _fuzzy_present(term: str, text: str, ratio: float) -> tuple[bool, bool]:
    """(verbatim_present, fuzzy_present) for one key term."""
    low, words = text.lower(), text.lower().split()
    if term.lower() in low:
        return True, True
    n = max(1, len(term.split()))
    grams = [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
    best = max(
        (difflib.SequenceMatcher(None, term.lower(), g).ratio() for g in grams),
        default=0.0,
    )
    return False, best >= ratio


def classify(golden: dict, retrieved: list, scores: dict, top_k_text: str, cfg: dict) -> str:
    """First matching rule wins (spec order). `scores` holds computed values."""
    missing = check_thresholds(cfg)
    if missing:
        raise ValueError(f"failure-cause thresholds missing: {', '.join(missing)}")
    answerable = bool(golden.get("answerable"))
    abstained = bool(scores.get("abstained", False))
    gt = golden.get("gt_segments") or []
    k = int(cfg.get("k", 8))
    ov = float(cfg.get("min_overlap_s", 1.0))
    top = retrieved[:k]
    flags = R.relevant_flags(top, gt, ov) if answerable else []
    hit = 1.0 if any(flags) else 0.0
    recall = sum(1 for g in gt if any(R.overlap(c, g) >= ov for c in top)) / len(gt) if gt else 0.0
    ts = scores.get("timestamp_hit_rate")
    faith = scores.get("faithfulness")
    if not answerable and not abstained:
        return "failed_to_abstain"
    if golden.get("requires_visual") and not any(
        c.get("modality") in ("frame_caption", "ocr") for c in top
    ):
        return "visual_not_indexed"
    if answerable and hit == 0:
        return "retrieval_miss"
    videos = {g.get("video_id") for g in gt}
    if answerable and (len(gt) >= 2 or len(videos) >= 2) and 0 < recall < 1:
        return "incomplete_retrieval"
    if answerable and any(flags):
        cov = float(cfg["boundary_coverage_threshold"])
        for g in gt:
            dur = float(g.get("end_s", 0)) - float(g.get("start_s", 0))
            covered = sum(
                max(0.0, min(float(c.get("end_s", 0)), float(g.get("end_s", 0)))
                    - max(float(c.get("start_s", 0)), float(g.get("start_s", 0))))
                for c in top if R.overlap(c, g) >= ov
            )
            if dur > 0 and covered / dur < cov:
                return "chunk_boundary_split"
    if answerable and hit == 1 and ts is not None and ts == 0:
        return "wrong_timestamp"
    for term in golden.get("key_terms") or []:
        verbatim, fuzzy = _fuzzy_present(term, top_k_text, float(cfg["asr_fuzzy_ratio"]))
        if not verbatim and fuzzy:
            return "asr_error"
    if faith is not None and faith < float(cfg["faithfulness_threshold"]):
        return "hallucination"
    if answerable and recall == 1:
        return "generation_error"
    return "unclassified"
