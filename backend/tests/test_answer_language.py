from video_rag.adapters.fakes import FakeEmbedder, StubLanguageModel
from video_rag.adapters.reranker import LexicalReranker
from video_rag.ask.service import QueryService
from video_rag.core.chunking import chunk_words
from video_rag.core.language import detect_answer_language, language_directive
from video_rag.core.models import Chunk


def test_detect_english():
    assert detect_answer_language("what is the main function") == "english"


def test_detect_hinglish_example():
    assert detect_answer_language("Python me variable ka matlab kay hai") == "hinglish"


def test_detect_hindi():
    assert detect_answer_language("variable kya hai") in ("hinglish", "english")
    assert detect_answer_language("वेरिएबल क्या है") == "hindi"


def test_directive_generic():
    assert "same language" in language_directive("english").lower()


def test_directive_hinglish_roman():
    d = language_directive("hinglish")
    assert "Roman" in d and "same language" in d


def test_directive_hindi():
    assert "Devanagari" in language_directive("hindi")


def _svc():
    store: list = []
    for w in ["hashmaps kahan use hote hain hashmap caching"]:
        for p in chunk_words([{"word": x, "start": float(i)} for i, x in enumerate(w.split())]):
            store.append(
                Chunk(
                    video_id="v1",
                    course_id="c1",
                    video_title="T",
                    text=p["text"],
                    start_sec=p["start_sec"],
                    end_sec=p["end_sec"],
                    sentences=p["sentences"],
                )
            )
    from video_rag.adapters.fakes import FakeStore as FS
    from video_rag.config import Settings as S

    settings = S(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    return QueryService(settings, FakeEmbedder(), FS(store), LexicalReranker(), StubLanguageModel())


def test_hinglish_synthesis_uses_directive():
    svc = _svc()
    seen = {}

    class Cap(StubLanguageModel):
        def complete(self, messages, max_tokens=None):
            seen["sys"] = messages[0]["content"]
            return "theek hai"

    svc.llm = Cap()
    out = svc.ask("hashmaps kahan use hote hain", "c1")
    assert out["status"] in ("answered", "partial")
    assert "same language" in seen["sys"]


def test_english_refusal_zero_calls():
    svc = _svc()
    calls = []
    svc.llm.complete = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM on refusal"))
    out = svc.ask("how does kubernetes autoscaling work xyzzy", "c1")
    assert out["status"] == "not_covered"
    assert calls == []
