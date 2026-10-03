from video_rag.core.coverage import gate


def test_covered():
    assert gate(0.8, [0.8, 0.7], 0.55, 0.15) == "covered"


def test_refused():
    assert gate(0.05, [0.05], 0.55, 0.15) == "not_covered"


def test_gray():
    assert gate(0.3, [0.3, 0.2], 0.55, 0.15) == "verify"
