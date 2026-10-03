import asyncio

from video_rag.adapters.fakes import (
    FakeCatalog,
    FakeEmbedder,
    FakeStore,
    FakeTranscripts,
)
from video_rag.adapters.sqlite_registry import SqliteRegistry
from video_rag.config import Settings
from video_rag.core.models import TranscriptDocument, VideoJob
from video_rag.ingest.service import IngestService


def _words(text: str) -> list[dict]:
    return [{"word": w, "start": float(i)} for i, w in enumerate(text.split())]


def _docs() -> dict[str, TranscriptDocument]:
    return {
        "v1": TranscriptDocument(
            video_id="v1",
            title="Hash Tables",
            raw_words=_words("hashmap gives o1 lookup used for caching"),
            clean_text="hashmap gives o1 lookup used for caching",
        ),
        "v2": TranscriptDocument(
            video_id="v2",
            title="Arrays",
            raw_words=_words("arrays store items in order"),
            clean_text="arrays store items in order",
        ),
    }


def _svc(store: FakeStore, registry):
    jobs = [
        VideoJob(video_id="v1", course_id="c1", title="Hash Tables", duration_sec=120, position=0),
        VideoJob(video_id="v2", course_id="c1", title="Arrays", duration_sec=300, position=1),
    ]
    settings = Settings(llm_api_key="stub")
    catalog = FakeCatalog(jobs, title="Fake Course")
    return IngestService(
        settings, catalog, FakeTranscripts(_docs()), FakeEmbedder(), store, registry
    )


def test_ingest_writes_registry_rows(tmp_path):
    store = FakeStore([])
    reg = SqliteRegistry(tmp_path / "r.db")
    svc = _svc(store, reg)

    report = asyncio.run(svc.ingest("https://www.youtube.com/playlist?list=c1"))

    assert report["indexed"] == 2 and report["failed"] == 0
    courses = reg.list_courses()
    assert len(courses) == 1
    row = courses[0]
    assert row["course_id"] == "c1"
    assert row["title"] == "Fake Course"
    assert row["video_count"] == 2
    assert row["status"] == "ready"

    vids = reg.list_course_videos("c1")
    assert [v["video_id"] for v in vids] == ["v1", "v2"]
    assert all(v["status"] == "indexed" for v in vids)


def test_ingest_marks_failed_video_in_registry(tmp_path):
    docs = _docs()
    del docs["v2"]  # FakeTranscripts.fetch raises KeyError -> per-video failure
    store = FakeStore([])
    reg = SqliteRegistry(tmp_path / "r.db")

    jobs = [
        VideoJob(video_id="v1", course_id="c1", title="Hash Tables", duration_sec=120, position=0),
        VideoJob(video_id="v2", course_id="c1", title="Arrays", duration_sec=300, position=1),
    ]
    svc = IngestService(
        Settings(llm_api_key="stub"),
        FakeCatalog(jobs),
        FakeTranscripts(docs),
        FakeEmbedder(),
        store,
        reg,
    )
    report = asyncio.run(svc.ingest("https://www.youtube.com/playlist?list=c1"))

    assert report["indexed"] == 1 and report["failed"] == 1
    statuses = {v["video_id"]: v["status"] for v in reg.list_course_videos("c1")}
    assert statuses == {"v1": "indexed", "v2": "failed"}


def test_ingest_without_registry_still_works():
    store = FakeStore([])
    svc = _svc(store, registry=None)
    report = asyncio.run(svc.ingest("https://www.youtube.com/playlist?list=c1"))
    assert report["indexed"] == 2


def test_reingest_overwrites_without_duplicates(tmp_path):
    store = FakeStore([])
    reg = SqliteRegistry(tmp_path / "r.db")
    svc = _svc(store, reg)

    asyncio.run(svc.ingest("https://www.youtube.com/playlist?list=c1"))
    asyncio.run(svc.ingest("https://www.youtube.com/playlist?list=c1"))

    assert len(reg.list_courses()) == 1
    assert len(reg.list_course_videos("c1")) == 2
    assert store.count_chunks("c1") > 0
