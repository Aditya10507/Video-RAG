import sqlite3

from video_rag.adapters.fakes import FakeStore
from video_rag.adapters.sqlite_registry import SqliteRegistry
from video_rag.core.models import Chunk


def test_registry_lists(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    assert reg.list_courses() == []
    assert reg.list_course_videos("nope") == []


def test_registry_restores_original_video_columns_without_data_loss(tmp_path):
    db_path = tmp_path / "registry.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE videos (
          video_id TEXT PRIMARY KEY, title TEXT, duration_sec INTEGER DEFAULT 0,
          status TEXT DEFAULT 'pending', transcript_source TEXT, pipeline_version TEXT,
          error TEXT, indexed_at TEXT
        );
        CREATE TABLE course_videos (
          course_id TEXT, video_id TEXT, position INTEGER,
          PRIMARY KEY (course_id, video_id)
        );
        INSERT INTO videos (video_id, title, duration_sec, status, indexed_at)
        VALUES ('v1', 'Existing video', 120, 'indexed', '2026-10-01T00:00:00Z');
        INSERT INTO course_videos (course_id, video_id, position) VALUES ('c1', 'v1', 0);
        """
    )
    con.close()

    reg = SqliteRegistry(db_path)

    with sqlite3.connect(db_path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(videos)")}
    assert "duration_seconds" in columns and "updated_at" in columns
    assert "duration_sec" not in columns and "indexed_at" not in columns

    video = reg.list_course_videos("c1")[0]
    assert video == {
        "video_id": "v1",
        "title": "Existing video",
        "status": "indexed",
        "duration_seconds": 120,
    }

    reg.upsert_video("v1", "Updated video", 180, status="indexed")
    assert reg.list_course_videos("c1")[0]["duration_seconds"] == 180


def test_new_registry_uses_original_video_column_names(tmp_path):
    db_path = tmp_path / "new.db"
    SqliteRegistry(db_path)
    with sqlite3.connect(db_path) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(videos)")}
    assert "duration_seconds" in columns and "updated_at" in columns
    assert "duration_sec" not in columns and "indexed_at" not in columns


def test_registry_write_roundtrip(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    reg.upsert_course("c1", "https://example.com", "Course One", 2)
    reg.upsert_video("v1", "Video One", 120)
    reg.upsert_video("v2", "Video Two", 300)
    reg.link_video_to_course("c1", "v1", 0)
    reg.link_video_to_course("c1", "v2", 1)

    courses = reg.list_courses()
    assert len(courses) == 1 and courses[0]["course_id"] == "c1"
    assert courses[0]["title"] == "Course One"
    assert courses[0]["video_count"] == 2
    assert courses[0]["status"] == "ingesting"

    vids = reg.list_course_videos("c1")
    assert [v["video_id"] for v in vids] == ["v1", "v2"]
    assert all(v["status"] == "pending" for v in vids)


def test_registry_upserts_are_idempotent(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    reg.upsert_course("c1", "https://a", "Old", 1)
    reg.upsert_course("c1", "https://b", "New", 3)
    reg.upsert_video("v1", "Old title", 10)
    reg.upsert_video("v1", "New title", 20)
    reg.link_video_to_course("c1", "v1", 0)
    reg.link_video_to_course("c1", "v1", 9)

    courses = reg.list_courses()
    assert len(courses) == 1
    assert courses[0]["title"] == "New" and courses[0]["video_count"] == 3
    vids = reg.list_course_videos("c1")
    assert len(vids) == 1 and vids[0]["title"] == "New title"


def test_registry_status_updates(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    reg.upsert_course("c1", "u", "t", 1)
    reg.upsert_video("v1", "t", 1)
    reg.link_video_to_course("c1", "v1", 0)

    reg.set_course_status("c1", "ready")
    assert reg.list_courses()[0]["status"] == "ready"

    reg.mark_video_status("v1", "indexed")
    assert reg.list_course_videos("c1")[0]["status"] == "indexed"

    reg.mark_video_status("v1", "failed", "no captions")
    row = reg.list_course_videos("c1")[0]
    assert row["status"] == "failed"


def test_mark_video_status_ignores_unknown_video(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    reg.mark_video_status("ghost", "indexed")
    assert reg.list_courses() == []


def test_store_counts():
    s = FakeStore(
        [
            Chunk(video_id="v", course_id="c", text="t hashmap"),
            Chunk(video_id="v", course_id="c", text="another chunk"),
            Chunk(video_id="v2", course_id="c", text="different video"),
            Chunk(video_id="v3", course_id="other", text="another course"),
        ]
    )
    assert s.count_chunks("c") == 3
    assert s.count_chunks("c", "v") == 2
    assert s.count_chunks("missing") == 0
    assert s.count_chunks_by_video("c") == {"v": 2, "v2": 1}
    assert s.count_chunks_by_course_and_video(["c", "other"]) == {
        "c": {"v": 2, "v2": 1},
        "other": {"v3": 1},
    }
