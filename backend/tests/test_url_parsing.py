from video_rag.core.url_parsing import course_id_for, parse_url


def test_video():
    assert parse_url("https://www.youtube.com/watch?v=abc12345678")["kind"] == "video"


def test_short():
    assert parse_url("https://youtu.be/abc12345678")["video_id"] == "abc12345678"


def test_playlist():
    p = parse_url("https://www.youtube.com/playlist?list=PLxyz")
    assert p["kind"] == "playlist" and course_id_for(p) == "PLxyz"


def test_channel_strips_handle():
    p = parse_url("https://www.youtube.com/@Career_with_yogita/videos")
    assert p["kind"] == "channel" and p["channel_ref"] == "Career_with_yogita"
    assert course_id_for(p) == "channel:Career_with_yogita"


def test_ambiguous():
    assert parse_url("https://www.youtube.com/watch?v=X&list=PLy")["kind"] == "ambiguous"
