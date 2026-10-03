from video_rag.adapters.fakes import FakeEmbedder, FakeStore, StubLanguageModel
from video_rag.adapters.reranker import LexicalReranker
from video_rag.ask.service import QueryService
from video_rag.core.chunking import chunk_words
from video_rag.core.models import Chunk


def _store():
    chunks = []
    for vid, text in [
        ("v1", "hashmap gives O(1) lookup used for caching deduplication"),
        ("v2", "arrays store items in order"),
    ]:
        for p in chunk_words([{"word": w, "start": float(i)} for i, w in enumerate(text.split())]):
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


def _svc(**kw):
    from video_rag.config import Settings as S

    settings = S(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    return QueryService(
        settings, FakeEmbedder(), _store(), LexicalReranker(), StubLanguageModel(), **kw
    )


def test_answered():
    out = _svc().ask("where can we use hashmaps", "c1")
    assert out["status"] == "answered"
    assert out["primary_source"]["video_id"] == "v1"


def test_refused():
    out = _svc().ask("how does kubernetes autoscaling work xyzzy", "c1")
    assert out["status"] == "not_covered"


def test_video_scope_filters():
    out = _svc().ask("where can we use hashmaps", "c1", video_id="v2")
    assert out["primary_source"] is None or out["primary_source"]["video_id"] == "v2"


def test_video_membership_rejected():
    import pytest

    from video_rag.adapters.sqlite_registry import SqliteRegistry

    class R(SqliteRegistry):
        def __init__(self):
            pass

        def list_course_videos(self, course_id):
            return [{"video_id": "v1"}]

    with pytest.raises(ValueError):
        _svc(registry=R()).ask("hashmaps", "c1", video_id="nope")


def test_stream_tokens_then_result():
    kinds = [k for k, _p in _svc().ask_stream("where can we use hashmaps", "c1")]
    assert "token" in kinds and kinds[-1] == "result"


def test_citation_lands_on_matching_chunk_and_sentence():
    from video_rag.config import Settings as S

    def _chunk(vid, text, start, sentences=None):
        return Chunk(
            video_id=vid,
            course_id="c1",
            video_title=f"Title {vid}",
            text=text,
            start_sec=start,
            end_sec=start + 60,
            sentences=sentences or [{"t": start, "s": text}],
        )

    chunks = [
        _chunk("v1", "welcome to the course introduction", 60.0),
        _chunk(
            "v1",
            "let me introduce caching a hashmap gives o1 lookup used for caching",
            300.0,
            [
                {"t": 300.0, "s": "let me introduce caching"},
                {"t": 420.0, "s": "a hashmap gives o1 lookup used for caching"},
            ],
        ),
    ]
    settings = S(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    svc = QueryService(
        settings, FakeEmbedder(), FakeStore(chunks), LexicalReranker(), StubLanguageModel()
    )
    out = svc.ask("where can we use hashmaps", "c1")
    assert out["status"] == "answered"
    assert out["primary_source"]["video_id"] == "v1"
    assert out["primary_source"]["start_seconds"] == 417.0
    assert out["primary_source"]["timestamp_label"] == "6:57"
