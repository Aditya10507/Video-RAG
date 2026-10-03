"""Stdlib test runner (no pytest needed)."""
import inspect
import pathlib
import sys
import tempfile
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

import test_answer_language
import test_ask_service
import test_chunking
import test_coverage
import test_ingest_registry
import test_library
import test_timestamps
import test_url_parsing

mods = [test_url_parsing, test_chunking, test_coverage, test_answer_language,
        test_ask_service, test_library, test_ingest_registry, test_timestamps]
passed = failed = 0
for m in mods:
    for name in dir(m):
        if name.startswith("test_"):
            fn = getattr(m, name)
            try:
                kwargs = {}
                try:
                    if "tmp_path" in inspect.signature(fn).parameters:
                        kwargs["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
                except (TypeError, ValueError):
                    pass
                fn(**kwargs)
                passed += 1
            except Exception:
                failed += 1
                print(f"FAIL {m.__name__}.{name}")
                traceback.print_exc()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
