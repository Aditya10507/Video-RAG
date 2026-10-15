"""Judge scorers (fake client) + failure-cause rules + judge pass. Offline."""

import json

from video_rag.config import Settings
from video_rag.eval.db import connect
from video_rag.eval.scorers import failure_cause as FC
from video_rag.eval.scorers.judge import Judge
from video_rag.eval.scoring import score_judge_run

CFG = {"k": 8, "min_overlap_s": 1.0, "failure_correctness_threshold": 0.6,
       "faithfulness_threshold": 0.5, "boundary_coverage_threshold": 0.5,
       "asr_fuzzy_ratio": 0.8}


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, max_tokens=None):
        _ = (messages, max_tokens)
        self.calls += 1
        out = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


def _judge(tmp_path, script):
    settings = Settings(judge_model="fake-judge", judge_api_key="k")
    j = Judge(settings, str(tmp_path / "eval.db"), {})
    j.client = FakeClient(script)
    return j


def test_judge_parses_and_caches(tmp_path):
    j = _judge(tmp_path, ['{"score": 0.75, "reasoning": "ok"}'])
    score, detail = j.correctness("a", "b")
    assert score == 0.75 and detail["reasoning"] == "ok"
    score2, _ = j.correctness("a", "b")  # cache: no new client call
    assert score2 == 0.75 and j.client.calls == 1


def test_judge_retries_malformed_then_raises(tmp_path):
    j = _judge(tmp_path, ["nope", "still nope", "nah"])
    try:
        j.correctness("a", "b")
    except Exception as e:
        assert "retries" in str(e)
    else:
        raise AssertionError("should raise")
    assert j.client.calls == 3


def test_judge_rejects_off_rubric(tmp_path):
    j = _judge(tmp_path, ['{"score": 0.9, "reasoning": "x"}'])
    try:
        j.correctness("a", "b")
    except Exception:
        return
    raise AssertionError("should raise")


def _gold(**kw):
    base = {"answerable": True, "requires_visual": False, "key_terms": [],
            "gt_segments": [{"video_id": "v", "start_s": 0, "end_s": 60}],
            "question": "q?"}
    base.update(kw)
    return base


def _chunk(vid="v", start=0.0, end=60.0, mod="transcript"):
    return {"video_id": vid, "start_s": start, "end_s": end, "modality": mod,
            "text": "t", "score": 0.9, "rank": 1}


def _scores(**kw):
    base = {"abstained": False, "timestamp_hit_rate": 1.0, "faithfulness": 0.9}
    base.update(kw)
    return base


def test_cause_rules_in_order():
    gt2 = [{"video_id": "v", "start_s": 0, "end_s": 60},
           {"video_id": "w", "start_s": 0, "end_s": 60}]
    full = [_chunk()]
    cases = [
        ({"answerable": False}, [], {}, "", "failed_to_abstain"),
        ({"requires_visual": True}, full, {}, "", "visual_not_indexed"),
        ({}, [], {}, "", "retrieval_miss"),
        ({"gt_segments": gt2}, [_chunk("v", 0, 60), _chunk("x", 0, 60)], {},
         "", "incomplete_retrieval"),
        ({}, [_chunk("v", 0, 10)], {}, "", "chunk_boundary_split"),
        ({}, full, {"timestamp_hit_rate": 0.0}, "", "wrong_timestamp"),
        ({"key_terms": ["Dijkstra"]}, full, {}, "dykstra method", "asr_error"),
        ({}, full, {"faithfulness": 0.2}, "", "hallucination"),
        ({}, full, {}, "", "generation_error"),
        ({"answerable": False, "key_terms": []}, [], {"abstained": True},
         "", "unclassified"),
        # Precedence: visual beats retrieval_miss.
        ({"requires_visual": True}, [], {}, "", "visual_not_indexed"),
    ]
    for gold_kw, retrieved, score_kw, text, expected in cases:
        gold = _gold(**gold_kw)
        got = FC.classify(gold, retrieved, _scores(**score_kw), text, CFG)
        assert got == expected, (gold_kw, got)


def test_cause_refuses_without_thresholds():
    try:
        FC.classify(_gold(), [], _scores(), "", {})
    except ValueError as e:
        assert "thresholds missing" in str(e)
    else:
        raise AssertionError("should raise")


class StubJudge:
    model = "stub-judge"

    def faithfulness(self, answer, chunks):
        _ = (answer, chunks)
        return 1.0, {"claims": []}

    def relevancy(self, question, answer):
        _ = (question, answer)
        return 0.75, {}

    def correctness(self, answer, reference):
        _ = (answer, reference)
        return 1.0, {"reasoning": "match"}

    def context_recall(self, reference, chunks):
        _ = (reference, chunks)
        return 1.0, {}

    def citation_accuracy(self, answer, cited):
        _ = (answer, cited)
        return 1.0, {}


class BadJudge(StubJudge):
    def correctness(self, answer, reference):
        _ = (answer, reference)
        return 0.0, {"reasoning": "nope"}

    def faithfulness(self, answer, chunks):
        _ = (answer, chunks)
        return 0.1, {}


def _seed_db(tmp_path):
    db = str(tmp_path / "eval.db")
    con = connect(db)
    con.execute("INSERT INTO datasets (id, name, version_hash, n_questions, created_at)"
                " VALUES ('d', 'g', 'h', 1, 't')")
    con.execute("INSERT INTO golden_questions (dataset_id, id, question, reference_answer,"
                " question_type, answerable, requires_visual, key_terms, gt_segments, split)"
                " VALUES ('d', 'q1', 'q?', 'ref', 't', 1, 0, '[]',"
                " '[{\"video_id\": \"v\", \"start_s\": 0, \"end_s\": 60}]', 'dev')")
    con.execute("INSERT INTO eval_runs (id, status, config, n_total)"
                " VALUES ('r', 'running', '{}', 1)")
    return db, con


def test_judge_pass_writes_scores_and_no_cause(tmp_path):
    db, con = _seed_db(tmp_path)
    chunk = json.dumps([{"chunk_id": "v:0", "video_id": "v", "start_s": 0.0,
                         "end_s": 60.0, "text": "ref text here", "score": 0.9, "rank": 1}])
    con.execute("INSERT INTO eval_results (run_id, question_id, status, scoring_status,"
                " answer, citations, retrieved, timings_ms, usage)"
                " VALUES ('r', 'q1', 'done', 'done', 'ref text here',"
                " '[{\"video_id\": \"v\", \"start_s\": 0, \"end_s\": 60,"
                " \"chunk_id\": \"v:0\"}]', ?, '{}', '{}')", (chunk,))
    con.commit()
    gold = {"q1": {"id": "q1", "question": "q?", "reference_answer": "ref",
                   "question_type": "t", "answerable": True, "requires_visual": False,
                   "key_terms": [], "gt_segments": [{"video_id": "v", "start_s": 0, "end_s": 60}]}}
    scored, errors = score_judge_run(con, "r", gold, StubJudge(), CFG)
    assert (scored, errors) == (1, 0)
    metrics = {m: v for m, v in con.execute(
        "SELECT metric, value FROM eval_scores").fetchall()}
    assert metrics["answer_correctness"] == 1.0
    assert metrics["faithfulness"] == 1.0
    assert con.execute("SELECT failure_cause FROM eval_results").fetchone()[0] is None
    con.close()


def test_judge_pass_marks_failed_cause(tmp_path):
    db, con = _seed_db(tmp_path)
    con.execute("INSERT INTO eval_results (run_id, question_id, status, scoring_status,"
                " answer, citations, retrieved, timings_ms, usage)"
                " VALUES ('r', 'q1', 'done', 'done', 'something else', '[]', '[]', '{}', '{}')")
    con.commit()
    gold = {"q1": {"id": "q1", "question": "q?", "reference_answer": "ref",
                   "question_type": "t", "answerable": True, "requires_visual": False,
                   "key_terms": [], "gt_segments": [{"video_id": "v", "start_s": 0, "end_s": 60}]}}
    scored, _ = score_judge_run(con, "r", gold, BadJudge(), CFG)
    assert scored == 1
    assert con.execute("SELECT failure_cause FROM eval_results").fetchone()[0] == "retrieval_miss"
    con.close()
