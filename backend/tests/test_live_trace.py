"""Live per-query tracing: retrieval capture, store roundtrip, judge
scoring, SSE polling and the /api/live endpoints. All offline with fakes."""

import asyncio
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_rag.adapters.fakes import FakeEmbedder, FakeStore, StubLanguageModel
from video_rag.adapters.reranker import LexicalReranker
from video_rag.ask.service import QueryService
from video_rag.config import Settings
from video_rag.core.chunking import chunk_words
from video_rag.core.models import Chunk
from video_rag.live import scoring as SC
from video_rag.live import store as L
from video_rag.live.store import LiveTracer


def _store():
    chunks = []
    for vid, text in [
        ("v1", "hashmap gives O(1) lookup used for caching deduplication"),
        ("v2", "arrays store items in order"),
    ]:
        for p in chunk_words(
            [{"word": w, "start": float(i)} for i, w in enumerate(text.split())]
        ):
            chunks.append(
                Chunk(
                    video_id=vid,
                    course_id="c1",
                    video_title=f"Title {vid}",
                    text=p["text"],
                    start_sec=p["start_sec"],
                    end_sec=p["end_sec"],
                    sentences=p["sentences"],
                )
            )
    return FakeStore(chunks)


class QuietTracer(LiveTracer):
    """Stores traces but never auto-spawns the scoring thread: tests call the
    scorer synchronously so timing is deterministic."""

    def _schedule_scoring(self, query_id: int, question: str) -> None:
        pass


def _svc(tmp_path, **kw):
    settings = Settings(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    tracer = QuietTracer(tmp_path / "eval.db", StubLanguageModel())
    return QueryService(
        settings, FakeEmbedder(), _store(), LexicalReranker(), StubLanguageModel(),
        tracer=tracer, **kw
    )


def test_ask_records_before_after_rerank(tmp_path):
    svc = _svc(tmp_path)
    out = svc.ask("where can we use hashmaps", "c1")
    assert out["status"] == "answered"

    rows = L.recent(tmp_path / "eval.db")
    assert len(rows) == 1 and rows[0]["status"] == "answered"
    qid = rows[0]["id"]
    d = L.detail(tmp_path / "eval.db", qid)
    chunks = d["chunks"]
    assert len(chunks) == 2
    hash_chunk = next(c for c in chunks if c["video_id"] == "v1")
    arr_chunk = next(c for c in chunks if c["video_id"] == "v2")

    # Both stores produce scores for every retrieved chunk.
    assert hash_chunk["dense_score"] == 0.5          # FakeStore dense score
    assert hash_chunk["keyword_score"] is not None
    assert hash_chunk["rerank_score"] > arr_chunk["rerank_score"]
    # Rerank ordering lands the hashmap chunk first (idx 1) and it is sent.
    assert hash_chunk["idx"] == 1 and arr_chunk["idx"] == 2
    assert hash_chunk["sent_to_llm"] == 1 and arr_chunk["sent_to_llm"] == 1
    # Union rank equals insertion order of the dedupe (dense hit first).
    assert hash_chunk["union_rank"] in (1, 2)

    # Stubs carry no last_usage block: an empty dict is correct, a real
    # LanguageModel records the synthesis call's tokens here.
    assert isinstance(d["usage"], dict)
    assert d["primary_source"]["video_id"] == "v1"


def test_ask_stream_records_trace(tmp_path):
    svc = _svc(tmp_path)
    kinds = [k for k, _ in svc.ask_stream("where can we use hashmaps", "c1")]
    assert "token" in kinds and kinds[-1] == "result"
    rows = L.recent(tmp_path / "eval.db")
    assert rows[0]["source"] == "ask_stream" and rows[0]["status"] == "answered"


def test_refusal_records_chunks_without_sent_flags(tmp_path):
    svc = _svc(tmp_path)
    out = svc.ask("how does kubernetes autoscaling work xyzzy", "c1")
    assert out["status"] == "not_covered"
    d = L.detail(tmp_path / "eval.db", L.recent(tmp_path / "eval.db")[0]["id"])
    assert d["status"] == "not_covered" and d["answer"] == ""
    assert all(c["sent_to_llm"] == 0 for c in d["chunks"])
    assert d["gate"] == "not_covered"


def test_tracer_failure_never_breaks_ask(tmp_path):
    svc = _svc(tmp_path)

    class Broken:
        def open(self, meta):
            raise RuntimeError("boom")

        def set_retrieval(self, qid, capture):
            raise RuntimeError("boom")

        def finish(self, *a, **k):
            raise RuntimeError("boom")

        def fail(self, *a, **k):
            raise RuntimeError("boom")

    svc.tracer = Broken()
    out = svc.ask("where can we use hashmaps", "c1")
    assert out["status"] == "answered"


def _fake_llm(replies):
    class Scripted:
        model = "test-model"
        last_usage = {"input_tokens": 1, "output_tokens": 2, "model": "test-model"}

        def complete(self, messages, max_tokens=None):
            text = messages[0]["content"]
            if "relevant to answering the QUESTION" in text:
                return replies["chunk_relevance"]
            if "atomic factual claims" in text:
                return replies["faithfulness"]
            if "rate relevance only" in text:
                return replies["relevancy"]
            raise AssertionError(f"unexpected prompt: {text[:80]}")

    return Scripted()


def test_scoring_writes_scores_and_chunk_relevance(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    qid = L.recent(db)[0]["id"]
    SC._score_and_store(
        _fake_llm({
            "chunk_relevance": '{"items": [{"idx": 1, "relevant": true, "reason": "direct"},'
                               ' {"idx": 2, "relevant": false, "reason": "arrays only"}]}',
            "faithfulness": '{"claims": [{"text": "a", "supported": true},'
                            ' {"text": "b", "supported": true}]}',
            "relevancy": '{"score": 4, "reasoning": "exact"}',
        }),
        str(db), qid, "where can we use hashmaps",
    )
    d = L.detail(db, qid)
    assert d["scoring_status"] == "done"
    s = d["scores"]
    assert s["faithfulness"]["value"] == 1.0
    assert s["answer_relevancy"]["value"] == 1.0
    assert s["retrieval_relevance"]["value"] == 0.5
    assert s["retrieval_relevance"]["detail"] == {"relevant": 1, "of": 2}
    by_idx = {c["idx"]: c for c in d["chunks"]}
    assert by_idx[1]["relevance"] == 1.0 and by_idx[2]["relevance"] == 0.0
    assert by_idx[1]["relevance_detail"] == "direct"


def test_scoring_failure_keeps_null_not_zero(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    qid = L.recent(db)[0]["id"]

    class Dead:
        model = "dead"
        last_usage = {}

        def complete(self, messages, max_tokens=None):
            raise RuntimeError("LLM down")

    SC._score_and_store(Dead(), str(db), qid, "q")
    d = L.detail(db, qid)
    assert d["scoring_status"] == "error"
    assert d["scoring_error"]
    assert set(d["scores"]) == {
        "retrieval_relevance", "faithfulness", "answer_relevancy"
    }
    assert all(s["status"] == "error" and s["value"] is None for s in d["scores"].values())
    # Chunks stay unscored: missing is not zero.
    assert all(c["relevance"] is None for c in d["chunks"])


def test_partial_scoring_failure_is_visible_per_metric(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    qid = L.recent(db)[0]["id"]
    llm = _fake_llm({
        "chunk_relevance": '{"items": [{"idx": 1, "relevant": "true", "reason": "bad type"},'
                           ' {"idx": 2, "relevant": false, "reason": "unrelated"}]}',
        "faithfulness": '{"claims": [{"text": "a", "supported": true}]}',
        "relevancy": '{"score": 4, "reasoning": "exact"}',
    })

    SC._score_and_store(llm, str(db), qid, "where can we use hashmaps")

    d = L.detail(db, qid)
    assert d["scoring_status"] == "partial"
    assert d["scores"]["retrieval_relevance"]["status"] == "error"
    assert "relevant must be boolean" in d["scores"]["retrieval_relevance"]["error"]
    assert d["scores"]["faithfulness"]["status"] == "done"
    assert d["scores"]["faithfulness"]["prompt_version"] == SC.PROMPT_VERSION
    assert d["scores"]["answer_relevancy"]["value"] == 1.0


def test_full_live_scoring_queue_is_reported(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    qid = L.recent(db)[0]["id"]
    capacity = SC._capacity
    SC._capacity = SC.threading.BoundedSemaphore(0)
    try:
        SC.schedule(None, str(db), qid, "where can we use hashmaps")
    finally:
        SC._capacity = capacity

    d = L.detail(db, qid)
    assert d["scoring_status"] == "overloaded"
    assert "queue is full" in d["scoring_error"]


def test_score_table_migrates_existing_live_database(tmp_path):
    db = tmp_path / "eval.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE live_query_scores (query_id INTEGER, metric TEXT, value REAL,"
        " detail TEXT, judge_model TEXT, prompt_version TEXT,"
        " PRIMARY KEY (query_id, metric))"
    )
    con.close()

    con = L.connect_live(db)
    columns = {row["name"] for row in con.execute("PRAGMA table_info(live_query_scores)")}
    con.close()
    assert {"status", "error"} <= columns


def test_poll_live_reports_new_and_scored(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    events, since, watched = L.poll_live(db, 0, {})
    qid = L.recent(db)[0]["id"]
    assert events == [
        {"query_id": qid, "phase": "asked"},
        {"query_id": qid, "phase": "updated"},
    ]
    # Completion is reported while the asynchronous scores are still pending.
    events, since, watched = L.poll_live(db, since, watched)
    assert events == [] and watched == {qid: "updated"}
    # Score lands → scored event is reported exactly once.
    SC._score_and_store(
        _fake_llm({
            "chunk_relevance": '{"items": [{"idx": 1, "relevant": true, "reason": "r"},'
                               ' {"idx": 2, "relevant": true, "reason": "r"}]}',
            "faithfulness": '{"claims": [{"text": "a", "supported": true}]}',
            "relevancy": '{"score": 3, "reasoning": "ok"}',
        }),
        str(db), qid, "q",
    )
    events, since, watched = L.poll_live(db, since, watched)
    assert events == [{"query_id": qid, "phase": "scored"}]
    assert watched == {}
    events, _, _ = L.poll_live(db, since, {})
    assert events == []


def test_live_event_stream_emits_events(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")

    async def collect():
        gen = L.live_event_stream(db)
        first = await asyncio.wait_for(gen.__anext__(), timeout=10)
        second = await asyncio.wait_for(gen.__anext__(), timeout=10)
        await gen.aclose()
        return first, second

    first, second = asyncio.run(collect())
    assert first.startswith(": connected")
    assert second.startswith("data: ") and '"query_id"' in second


def test_live_api_endpoints(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path), llm_api_key="stub", frontend_dir=str(tmp_path / "nope")
    )
    app = FastAPI()
    from video_rag.api.eval_routes import router

    app.state.container = type(
        "C", (), {"settings": settings, "llm": None}
    )()
    app.include_router(router)
    client = TestClient(app)

    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    qid = L.recent(tmp_path / "eval.db")[0]["id"]

    recent = client.get("/api/live/recent").json()["queries"]
    assert [q["id"] for q in recent] == [qid] and recent[0]["status"] == "answered"
    detail = client.get(f"/api/live/query/{qid}").json()
    assert len(detail["chunks"]) == 2
    assert detail["scores"] == {}  # The test tracer deliberately leaves judging pending.
    assert client.get("/api/live/query/9999").status_code == 404


def test_detail_json_blobs_parse(tmp_path):
    db = tmp_path / "eval.db"
    svc = _svc(tmp_path)
    svc.ask("where can we use hashmaps", "c1")
    d = L.detail(db, L.recent(db)[0]["id"])
    assert isinstance(d["primary_source"], dict)
    assert isinstance(d["also_mentioned"], list)
    assert isinstance(d["covers"], list)
    assert isinstance(d["timings_ms"], dict)
