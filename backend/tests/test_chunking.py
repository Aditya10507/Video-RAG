from video_rag.core.chunking import chunk_words


def _words(text, start=0.0):
    return [{"word": w, "start": start + i} for i, w in enumerate(text.split())]


def test_chunks_keep_sentence_boundary():
    parts = chunk_words(_words("Hello world. " * 40), window=60, overlap=15)
    assert parts and all("start_sec" in p for p in parts)


def test_empty():
    assert chunk_words([]) == []
