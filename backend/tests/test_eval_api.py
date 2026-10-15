"""Eval API: endpoints over a seeded temp DB. No network."""

from fastapi.testclient import TestClient

from video_rag.api.app import create_app
from video_rag.api.eval_routes import poll_revs
from video_rag.config import Settings
from video_rag.eval import aggregates as A
from video_rag.eval.db import connect


def _seed(tmp_path):
    settings = Settings(data_dir=str(tmp_path), llm_api_key="stub",
                        frontend_dir=str(tmp_path / "nope"))
    app = create_app(settings)
    db = str(tmp_path / "eval.db")
    con = connect(db)
    con.execute("INSERT INTO datasets (id, name, version_hash, n_questions, created_at)"
                " VALUES ('d1', 'g.jsonl', 'h', 2, 't')")
    con.execute("INSERT INTO golden_questions (dataset_id, id, question, reference_answer,"
                " question_type, answerable, requires_visual, key_terms, gt_segments, split)"
                " VALUES ('d1', 'q1', 'What is X?', 'X is Y', 'factual_lookup', 1, 0, '[]',"
                " '[{\"video_id\": \"v\", \"start_s\": 10, \"end_s\": 70}]', 'dev'),"
                " ('d1', 'q2', 'Kubernetes?', NULL, 'out_of_scope', 0, 0, '[]', '[]', 'dev')")
    for rid, label, started in (("r1", "first", "t1"), ("r2", "second", "t2")):
        con.execute("INSERT INTO eval_runs (id, label, dataset_id, status, config,"
                    " started_at, heartbeat_at, updated_at, n_total, n_answered, n_scored)"
                    " VALUES (?, ?, 'd1', 'completed', '{}', ?, ?, ?, 2, 2, 2)",
                    (rid, label, started, started, started))
    chunk = ('[{"video_id":"v","start_s":10,"end_s":70,"text":"X is Y",'
             '"score":0.9,"rank":1,"chunk_id":"v:10","modality":"transcript"}]')
    cite = ('[{"video_id":"v","start_s":12,"end_s":70,"chunk_id":"v:10"}]')
    con.execute("INSERT INTO eval_results (run_id, question_id, status, scoring_status,"
                " answer, citations, retrieved, timings_ms, usage)"
                " VALUES ('r1', 'q1', 'done', 'done', 'X is Y', ?, ?,"
                " '{\"total\": 1000}', '{\"model\": \"m\"}')", (cite, chunk))
    con.execute("INSERT INTO eval_results (run_id, question_id, status, scoring_status,"
                " answer, citations, retrieved, timings_ms, usage)"
                " VALUES ('r1', 'q2', 'done', 'done', '', '[]', '[]', '{\"total\": 200}', '{}')")
    con.execute("INSERT INTO eval_results (run_id, question_id, status, scoring_status,"
                " answer, citations, retrieved, timings_ms, usage)"
                " VALUES ('r2', 'q1', 'done', 'done', 'X is Y maybe', ?, ?,"
                " '{\"total\": 2000}', '{\"model\": \"m\"}')", (cite, chunk))
    r1 = con.execute("SELECT id FROM eval_results"
                       " WHERE run_id = 'r1' AND question_id = 'q1'").fetchone()[0]
    r2 = con.execute("SELECT id FROM eval_results WHERE run_id = 'r2'").fetchone()[0]
    con.executemany("INSERT INTO eval_scores (result_id, metric, value, detail)"
                    " VALUES (?, ?, ?, ?)",
                    [(r1, "recall_at_k", 1.0, "{}"),
                     (r1, "answer_correctness", 0.75, '{"reasoning": "ok"}'),
                     (r1, "latency_ms", 1000.0, "{}"),
                     (r2, "recall_at_k", 1.0, "{}"),
                     (r2, "answer_correctness", 0.5, "{}")])
    con.execute("UPDATE eval_results SET failure_cause = 'generation_error' WHERE id = ?",
                (r2,))
    con.execute("INSERT INTO eval_runs (id, label, dataset_id, status, config, started_at,"
                " heartbeat_at, updated_at, n_total) VALUES ('r3', 'live', 'd1', 'running',"
                " '{}', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z', 't', 2)")
    con.commit()
    con.close()
    return TestClient(app), db, r1


def test_health_config_datasets(tmp_path):
    client, _db, _r1 = _seed(tmp_path)
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config/dashboard").json()
    assert cfg["metrics"]["recall_at_k"]["group"] == "retrieval"
    assert cfg["targets"]["recall_at_k"] is None
    assert client.get("/api/datasets").json()["datasets"][0]["id"] == "d1"


def test_runs_and_detail(tmp_path):
    client, _db, _r1 = _seed(tmp_path)
    runs = client.get("/api/runs").json()["runs"]
    assert {r["id"] for r in runs} == {"r1", "r2", "r3"}
    done = client.get("/api/runs", params={"status": "completed"}).json()["runs"]
    assert done[0]["id"] in ("r1", "r2")
    detail = client.get("/api/runs/r1").json()
    assert detail["aggregates"]["recall_at_k"] == {"value": 1.0, "n": 1}
    assert detail["latency"] == {"p50": 1000.0, "p95": 1000.0}
    stalled = client.get("/api/runs/r3").json()
    assert stalled["display_status"] == "stalled"
    assert client.get("/api/runs/nope").status_code == 404


def test_compare_breakdown_failures(tmp_path):
    client, _db, _r1 = _seed(tmp_path)
    cmp = client.get("/api/runs/r1/compare/r2").json()["metrics"]
    assert cmp["answer_correctness"]["delta"] == 0.25
    assert cmp["answer_correctness"]["n_paired"] == 1
    assert cmp["answer_correctness"]["within_noise"] is False
    assert cmp["answer_correctness"]["ci"]["lo"] == 0.25
    assert client.get("/api/runs/r1/compare/nope").status_code == 404
    bd = client.get("/api/runs/r1/breakdown",
                    params={"by": "question_type", "metric": "recall_at_k"}).json()
    assert bd["groups"] == [{"group": "factual_lookup", "value": 1.0, "n": 1}]
    assert client.get("/api/runs/r1/breakdown", params={"by": "nope"}).json()["groups"] == []
    fails = client.get("/api/runs/r2/failures").json()
    assert fails["by_cause"] == {"generation_error": 1} and fails["total_failed"] == 1


def test_results_filters_sort_and_trace(tmp_path):
    client, _db, r1 = _seed(tmp_path)
    body = client.get("/api/runs/r1/results",
                      params={"sort": "answer_correctness", "order": "desc"}).json()
    assert body["total"] == 2 and body["results"][0]["question_id"] == "q1"
    assert client.get("/api/runs/r1/results",
                      params={"cause": "generation_error"}).json()["total"] == 0
    assert client.get("/api/runs/r2/results",
                      params={"cause": "generation_error"}).json()["total"] == 1
    assert client.get("/api/runs/r1/results",
                      params={"question_type": "out_of_scope"}).json()["total"] == 1
    trace = client.get(f"/api/results/{r1}").json()
    assert trace["chunks"][0]["relevant"] is True
    assert trace["citations"][0]["hit"] is True
    assert trace["citations"][0]["url"].startswith("https://www.youtube.com/watch?v=v")
    assert client.get("/api/results/999999").status_code == 404


def test_trend_csv_and_start_validation(tmp_path):
    import sqlite3

    client, db, _r1 = _seed(tmp_path)
    trend = client.get("/api/trend",
                       params={"metrics": "recall_at_k", "last": 5}).json()["trend"]
    assert [t["run_id"] for t in trend] == ["r1", "r2"]
    csv = client.get("/api/runs/r1/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("question_id,question,")
    assert "recall_at_k" in csv.text.splitlines()[0]
    # A running run blocks new starts.
    assert client.post("/api/runs", json={"dataset_path": "x"}).status_code == 409
    con = sqlite3.connect(db)
    con.execute("UPDATE eval_runs SET status = 'completed' WHERE id = 'r3'")
    con.commit()
    con.close()
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", json={"dataset_path": "nope.jsonl"}).status_code == 400
    assert client.post("/api/runs/r9/cancel").status_code == 404
    assert client.post("/api/runs/r3/cancel").json()["cancel_requested"] is True


def test_sse_stream_and_poll_unit(tmp_path):
    import asyncio

    from video_rag.api.eval_routes import eval_event_stream

    client, db, _r1 = _seed(tmp_path)
    changed = poll_revs(db, {})
    assert set(changed) == {"r1", "r2", "r3"}
    assert poll_revs(db, {k: v["rev"] for k, v in changed.items()}) == {}

    async def collect():
        gen = eval_event_stream(db)
        first = await asyncio.wait_for(gen.__anext__(), timeout=10)
        second = await asyncio.wait_for(gen.__anext__(), timeout=10)
        await gen.aclose()
        return first, second

    first, second = asyncio.run(collect())
    assert first.startswith(": connected")
    assert second.startswith("data:") and '"run_id"' in second


def test_stale_recovery_marks_failed(tmp_path):
    _client, db, _r1 = _seed(tmp_path)
    assert A.mark_stale_failed(db, 60) == 1
    con = connect(db)
    try:
        assert con.execute("SELECT status FROM eval_runs WHERE id = 'r3'").fetchone()[0] == "failed"
    finally:
        con.close()
