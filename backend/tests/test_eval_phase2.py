"""Deterministic scorers: hand-computed values plus every edge case."""

from video_rag.eval.scorers import retrieval as R
from video_rag.eval.scorers import system as S
from video_rag.eval.scorers import timestamps as T
from video_rag.eval.scoring import score_result


def _chunk(vid, start, end, rank):
    return {"video_id": vid, "start_s": start, "end_s": end, "rank": rank}


def test_overlap_and_relevance():
    assert R.overlap(_chunk("v", 10, 20, 1), {"video_id": "v", "start_s": 15, "end_s": 25}) == 5.0
    assert R.overlap(_chunk("v", 10, 20, 1), {"video_id": "w", "start_s": 15, "end_s": 25}) == 0.0
    assert R.overlap(_chunk("v", 10, 20, 1), {"video_id": "v", "start_s": 20, "end_s": 30}) == 0.0
    chunks = [_chunk("v", 10, 20, 1), _chunk("v", 100, 110, 2)]
    assert R.relevant_flags(chunks, [{"video_id": "v", "start_s": 15, "end_s": 25}], 1.0) == [
        True, False,
    ]
    # Exactly at the threshold counts.
    assert R.relevant_flags(chunks, [{"video_id": "v", "start_s": 19, "end_s": 25}], 1.0)[0] is True


def test_recall_hit_mrr_precision():
    gt = [
        {"video_id": "v", "start_s": 0, "end_s": 60},
        {"video_id": "v", "start_s": 500, "end_s": 560},
        {"video_id": "w", "start_s": 0, "end_s": 60},
    ]
    chunks = [
        _chunk("v", 10, 70, 1),  # covers gt[0]
        _chunk("x", 0, 5, 2),  # covers nothing
        _chunk("v", 510, 570, 3),  # covers gt[1]
    ]
    assert R.segment_coverage(chunks, gt, 1.0, 8) == 2 / 3
    assert R.segment_coverage(chunks, gt, 1.0, 1) == 1 / 3
    assert R.segment_coverage([], gt, 1.0, 8) == 0.0
    flags = R.relevant_flags(chunks, gt, 1.0)
    assert R.hit_at_k(flags, 8) == 1.0
    assert R.hit_at_k([False, False], 8) == 0.0
    assert R.mrr(chunks, gt, 1.0, 8) == 1.0
    assert R.mrr(chunks[1:], gt, 1.0, 8) == 1.0 / 3
    assert R.mrr(chunks[1:2], gt, 1.0, 8) == 0.0
    assert R.precision_at_k([True, False, True, False], 4) == 0.5
    assert R.precision_at_k([], 8) is None


def test_timestamp_hit_rate():
    gt = [{"video_id": "v", "start_s": 100, "end_s": 160}]
    assert T.timestamp_hit_rate([{"video_id": "v", "start_s": 150, "end_s": 155}], gt, 3.0) == 1.0
    # Outside even widened (97..163): miss.
    assert T.timestamp_hit_rate([{"video_id": "v", "start_s": 200, "end_s": 205}], gt, 3.0) == 0.0
    # Wrong video: miss. No citations: 0. Empty GT (unanswerable): None.
    assert T.timestamp_hit_rate([{"video_id": "w", "start_s": 100, "end_s": 110}], gt, 3.0) == 0.0
    assert T.timestamp_hit_rate([], gt, 3.0) == 0.0
    assert T.timestamp_hit_rate([{"video_id": "v", "start_s": 1, "end_s": 2}], [], 3.0) is None


def test_system_metrics():
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert S.percentile([4.0, 1.0, 3.0, 2.0], 95) == 4.0
    assert S.percentile([], 95) is None
    pricing = {"m": {"input_per_1k": 0.5, "output_per_1k": 1.0}}
    usage = {"input_tokens": 2000, "output_tokens": 500, "model": "m"}
    assert S.cost_usd(usage, pricing) == 1.0 + 0.5
    assert S.cost_usd(usage, {}) is None


def test_bootstrap_ci_marks_noise():
    from video_rag.eval.aggregates import bootstrap_ci

    clear = bootstrap_ci([0.8, 0.9, 0.85], [0.4, 0.5, 0.45])
    assert clear["significant"] is True and clear["within_noise"] is False
    assert clear["n_paired"] == 3
    noisy = bootstrap_ci([0.8, 0.4], [0.5, 0.7])
    assert noisy["within_noise"] is True and noisy["significant"] is False
    empty = bootstrap_ci([], [])
    assert empty["delta"] is None and empty["within_noise"] is True


def test_score_result_answerable_and_refusal():
    cfg = {"k": 8, "k_max": 8, "min_overlap_s": 1.0, "timestamp_tolerance_s": 3.0}
    golden = {"answerable": True,
              "gt_segments": [{"video_id": "v", "start_s": 10, "end_s": 70}]}
    retrieved = [
        {"video_id": "v", "start_s": 10.0, "end_s": 70.0, "rank": 1, "text": "t"},
        {"video_id": "v", "start_s": 80.0, "end_s": 90.0, "rank": 2, "text": "t"},
    ]
    cites = [{"video_id": "v", "start_s": 12.0, "end_s": 70.0}]
    out = score_result(golden, retrieved, cites, "answer", False,
                       {"total": 1200.0}, {}, cfg, {})
    assert out["recall_at_k"][0] == 1.0
    assert out["hit_at_k"][0] == 1.0
    assert out["mrr"][0] == 1.0
    assert out["context_precision_at_k"][0] == 0.5
    assert out["timestamp_hit_rate"][0] == 1.0
    assert out["false_abstain_rate"][0] == 0.0
    assert out["latency_ms"][0] == 1200.0
    assert "cost_usd" not in out  # no price entry: NULL, never zero
    refused = score_result(golden, [], [], "", True, {}, {}, cfg, {})
    assert refused["recall_at_k"][0] == 0.0
    assert refused["timestamp_hit_rate"][0] == 0.0
    assert refused["false_abstain_rate"][0] == 1.0
    assert "latency_ms" not in refused
    unans = score_result({"answerable": False, "gt_segments": []}, [], [], "",
                         True, {}, {}, cfg, {})
    assert unans == {"abstain_accuracy": (1.0, {"abstained": True})}
    unans_bad = score_result({"answerable": False, "gt_segments": []}, [], [],
                             "made up", False, {}, {}, cfg, {})
    assert unans_bad["abstain_accuracy"][0] == 0.0
