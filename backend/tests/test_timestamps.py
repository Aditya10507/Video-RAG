from video_rag.core.timestamps import format_label, refine, youtube_url


def test_refine_picks_best_matching_sentence():
    sentences = [
        {"t": 60.0, "s": "let us start with a quick introduction"},
        {"t": 90.0, "s": "a hashmap gives you constant time lookup"},
        {"t": 120.0, "s": "and that wraps up the section"},
    ]
    start, label = refine(sentences, "where can we use hashmaps", 60.0)
    assert start == 87.0
    assert label == "1:27"


def test_refine_matches_after_stemming():
    sentences = [
        {"t": 10.0, "s": "items are stored one after another"},
        {"t": 40.0, "s": "arrays are indexed from zero"},
    ]
    start, _label = refine(sentences, "what are arrays", 10.0)
    assert start == 37.0


def test_refine_matches_alias_normalized_words():
    sentences = [
        {"t": 15.0, "s": "a hash map stores keys and values"},
        {"t": 55.0, "s": "collisions are handled by chaining"},
    ]
    start, _label = refine(sentences, "how does a hashmap work", 15.0)
    assert start == 12.0


def test_refine_without_match_keeps_chunk_start():
    sentences = [
        {"t": 5.0, "s": "unrelated words entirely"},
        {"t": 99.0, "s": "also unrelated"},
    ]
    start, _label = refine(sentences, "kubernetes autoscaling internals", 30.0)
    assert start == 27.0


def test_refine_empty_sentences_keeps_chunk_start():
    start, label = refine([], "anything", 42.0)
    assert start == 39.0
    assert label == "0:39"


def test_format_label():
    assert format_label(763) == "12:43"
    assert format_label(3661) == "1:01:01"
    assert format_label(0) == "0:00"


def test_youtube_url():
    assert youtube_url("abc12345678", 763) == (
        "https://www.youtube.com/watch?v=abc12345678&t=763s"
    )
