"""CI guard: no test fixtures or mock-data modules leak into shipped code.

Fails (exit 1) when non-test code imports from tests/fixtures, or when a
*mock*/*dummy* module appears outside tests/ (adapters/fakes.py is the one
allowed exception: offline doubles also used by `cli smoke`).
Run: .\\.venv\\Scripts\\python.exe backend\\tools\\check_no_mock_data.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src"
FRONTEND = ROOT / "frontend"
TESTS = ROOT / "backend" / "tests"
ALLOWED_FAKES = {SRC / "video_rag" / "adapters" / "fakes.py"}

IMPORT_RE = re.compile(r"^\s*(from|import)\s+(tests\b|test_\w+|fixtures\b|conftest\b)",
                       re.MULTILINE)

failures: list[str] = []


def check_tree(path: Path, pattern: str) -> None:
    for f in sorted(path.rglob(pattern)):
        if f.name.endswith(".test.js"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if path == SRC and f in ALLOWED_FAKES:
            continue
        m = IMPORT_RE.search(text)
        if m:
            failures.append(f"{f.relative_to(ROOT)} imports test code: {m.group(0).strip()}")
        low = f.name.lower()
        if ("mock" in low or "dummy" in low) and path != TESTS:
            failures.append(f"suspicious mock module: {f.relative_to(ROOT)}")


check_tree(SRC, "*.py")
check_tree(FRONTEND, "*.js")
# Image-anchored strings must never appear as shipped values.
BANNED = ["Run #14", "60 questions across 12 videos", "0.86", "golden set of 60"]
for f in list((ROOT / "frontend").glob("*.js")) + list(SRC.rglob("*.py")):
    if f.name.endswith(".test.js") or f.parent == TESTS or f in ALLOWED_FAKES:
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    for s in BANNED:
        if s in text:
            failures.append(f"{f.relative_to(ROOT)} contains reference-image string: {s!r}")

if failures:
    print("MOCK-DATA GUARD FAILED:")
    print("\n".join(f" - {f}" for f in failures))
    sys.exit(1)
print("mock-data guard: clean")
