"""Eval datasets validator + DB/runner, offline with fakes."""

import json

from video_rag.adapters.fakes import FakeEmbedder, FakeStore, StubLanguageModel
from video_rag.adapters.reranker import LexicalReranker
from video_rag.config import Settings
from video_rag.core.models import Chunk
from video_rag.eval import datasets
from video_rag.eval.db import connect
from video_rag.eval.runner import run_dataset


def test_validator_accepts_golden_shape(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(
        '{"id": "q1", "question": "q?", "reference_answer": "a",'
        ' "question_type": "t", "answerable": true,'
        ' "gt_segments": [{"video_id": "v", "start_s": 1.0, "end_s": 2.0}]}\n'
        '{"id": "q2", "question": "q?", "reference_answer": null,'
        ' "question_type": "t", "answerable": false, "gt_segments": []}\n',
        encoding="utf-8",
    )
    rows, errors = datasets.load(p)
    assert errors == [] and len(rows) == 2


def test_validator_lists_line_errors(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(
        '{"id": "q1", "question": "q?"}\n'
        "not json\n"
        '{"id": "q1", "question": "dup?", "question_type": "t",'
        ' "answerable": false, "gt_segments": [{"video_id": "v"}]}\n'
        '{"id": "q3", "question": "q?", "question_type": "t", "answerable": true,'
        ' "reference_answer": "", "gt_segments": []}\n',
        encoding="utf-8",
    )
    rows, errors = datasets.load(p)
    assert len(rows) == 3
    assert any("line 1: missing" in e for e in errors)
    assert any("line 2: invalid JSON" in e for e in errors)
    assert any("duplicate id" in e for e in errors)
    assert any(
        "start_s < end_s" in e or "video_id" in e or "numbers" in e for e in errors
    )
    assert any("needs >= 1 gt_segment" in e for e in errors)
    assert datasets.load(tmp_path / "nope.jsonl")[1]


def _components():
    chunks = [
        Chunk(video_id="v1", course_id="c1", video_title="T",
              start_sec=10.0, end_sec=70.0,
              text="a hashmap gives constant time lookup for caching"),
    ]
    store = FakeStore(list(chunks))
    return {"embedder": FakeEmbedder(), "store": store,
            "reranker": LexicalReranker(), "llm": StubLanguageModel()}


def test_runner_stores_two_questions(tmp_path):
    ds = tmp_path / "g.jsonl"
    ds.write_text(
        '{"id": "q1", "question": "where are hashmaps used", "course_id": "c1",'
        ' "reference_answer": "caching", "question_type": "t", "answerable": true,'
        ' "gt_segments": [{"video_id": "v1", "start_s": 10.0, "end_s": 70.0}]}\n'
        '{"id": "q2", "question": "kubernetes autoscaling?", "course_id": "c1",'
        ' "reference_answer": null,'
        ' "question_type": "t", "answerable": false, "gt_segments": []}\n',
        encoding="utf-8",
    )
    settings = Settings(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    db = str(tmp_path / "eval.db")
    cfg = {"concurrency": 1, "question_timeout_s": 60}
    run_id = run_dataset(settings, _components(), str(ds), "t", None, {},
                         db, cfg)
    con = connect(db)
    run = con.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    assert run["status"] == "completed"
    assert run["n_answered"] == 2 and run["rev"] >= 2
    rows = con.execute(
        "SELECT * FROM eval_results WHERE run_id = ? ORDER BY question_id", (run_id,)
    ).fetchall()
    assert [r["status"] for r in rows] == ["done", "done"]
    assert json.loads(rows[0]["retrieved"])[0]["video_id"] == "v1"
    assert json.loads(rows[0]["timings_ms"])["total"] >= 0
    scored = con.execute(
        "SELECT metric, value FROM eval_scores ORDER BY result_id, metric"
    ).fetchall()
    by_metric = {r["metric"]: r["value"] for r in scored}
    assert by_metric["recall_at_k"] == 1.0
    assert by_metric["context_precision_at_k"] == 1.0
    assert by_metric["abstain_accuracy"] == 1.0
    assert con.execute(
        "SELECT n_scored FROM eval_runs WHERE id = ?", (run_id,)
    ).fetchone()[0] == 2
    con.close()
