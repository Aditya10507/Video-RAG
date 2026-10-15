"""Command line entry point: smoke, serve, ingest, ask, config, eval-*."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import from_env, masked_dict


def cmd_smoke(_args) -> int:
    from .adapters.fakes import (
        FakeCatalog,
        FakeEmbedder,
        FakeStore,
        FakeTranscripts,
        StubLanguageModel,
    )
    from .adapters.reranker import LexicalReranker
    from .ask.service import QueryService
    from .config import Settings
    from .core.chunking import chunk_words
    from .core.models import Chunk, TranscriptDocument, VideoJob
    from .ingest.service import IngestService

    settings = Settings(llm_api_key="stub", threshold_high=0.1, threshold_low=0.05)
    jobs = [
        VideoJob(video_id="vid1", course_id="course1", title="Hash Tables Explained"),
        VideoJob(video_id="vid2", course_id="course1", title="Arrays"),
    ]
    docs = {
        "vid1": TranscriptDocument(
            video_id="vid1",
            title="Hash Tables",
            raw_words=[
                {"word": w, "start": float(i)}
                for i, w in enumerate(
                    ["hashmap", "gives", "O(1)", "lookup", "used", "for", "caching"]
                )
            ],
            clean_text="hashmap gives O(1) lookup used for caching",
        ),
        "vid2": TranscriptDocument(
            video_id="vid2",
            title="Arrays",
            raw_words=[
                {"word": w, "start": float(i)} for i, w in enumerate(["arrays", "store", "items"])
            ],
            clean_text="arrays store items",
        ),
    }
    store: list = []

    class MemStore(FakeStore):
        pass

    mem = MemStore(store)
    ing = IngestService(
        settings, FakeCatalog(jobs), FakeTranscripts(docs), FakeEmbedder(), mem, registry=None
    )
    # Build chunks directly (offline, no asyncio registry writes).
    for job in jobs:
        doc = docs[job.video_id]
        for p in chunk_words(doc.raw_words):
            store.append(
                Chunk(
                    video_id=job.video_id,
                    course_id="course1",
                    video_title=job.title,
                    text=p["text"],
                    start_sec=p["start_sec"],
                    end_sec=p["end_sec"],
                    sentences=p["sentences"],
                )
            )
    svc = QueryService(settings, FakeEmbedder(), mem, LexicalReranker(), StubLanguageModel())
    covered = svc.ask("where can we use hashmaps", "course1")
    assert covered["status"] == "answered" and covered["primary_source"], covered
    refused = svc.ask("how does kubernetes autoscaling work", "course1")
    assert refused["status"] == "not_covered", refused
    assert refused["message"] == "The topic is not covered in the given course", refused
    print("Smoke check: PASSED")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    settings = from_env()
    if args.port:
        settings.api_port = args.port
    from .api.app import create_app

    app = create_app(settings)
    print(f"Web page and API: http://{settings.api_host}:{settings.api_port}")
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
    return 0


def cmd_config(_args) -> int:
    print(json.dumps(masked_dict(from_env()), indent=2))
    return 0


def cmd_ask(args) -> int:
    from .api.app import create_app

    settings = from_env()
    svc = __import__("video_rag.ask.service", fromlist=["QueryService"]).QueryService
    app = create_app(settings)
    c = app.state.container
    out = svc(c.settings, c.embedder, c.store, c.reranker, c.llm, c.registry).ask(
        args.question, args.course, args.video
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_ingest(args) -> int:
    from .api.app import create_app

    settings = from_env()
    app = create_app(settings)
    c = app.state.container
    from .ingest.service import IngestService

    report = asyncio.run(
        IngestService(c.settings, c.catalog, c.transcripts, c.embedder, c.store, c.registry).ingest(
            args.url, force=getattr(args, "force", False)
        )
    )
    print(json.dumps(report, indent=2))
    return 0


def _eval_cfg() -> dict:
    import yaml

    path = Path(__file__).resolve().parents[3] / "backend" / "eval" / "eval.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _pricing() -> dict:
    import yaml

    path = Path(__file__).resolve().parents[3] / "backend" / "eval" / "pricing.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models", {})


def cmd_eval_validate(args) -> int:
    from .eval import datasets

    rows, errors = datasets.load(args.file)
    if errors:
        print("INVALID:")
        print("\n".join(errors))
        return 1
    print(f"valid: {len(rows)} questions, hash {datasets.file_hash(Path(args.file))}")
    return 0


def cmd_eval_run(args) -> int:
    from .api.app import create_app
    from .eval.runner import run_dataset

    settings = from_env()
    app = create_app(settings)
    c = app.state.container
    overrides = dict(kv.split("=", 1) for kv in (args.override or []))
    if "top_k" in overrides:
        overrides["top_k"] = int(overrides["top_k"])
    run_id = run_dataset(
        settings,
        {"embedder": c.embedder, "store": c.store, "reranker": c.reranker, "llm": c.llm},
        args.dataset,
        args.label or Path(args.dataset).name,
        args.split,
        overrides,
        str(Path(settings.data_dir) / "eval.db"),
        _eval_cfg(),
        course_default=args.course,
        pricing=_pricing(),
    )
    print(json.dumps({"run_id": run_id}))
    return 0


def cmd_eval_rescore(args) -> int:
    from .eval.runner import rescore_run

    settings = from_env()
    scored, errors = rescore_run(
        str(Path(settings.data_dir) / "eval.db"), args.run, settings, _eval_cfg()
    )
    print(json.dumps({"scored": scored, "errors": errors}))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="video-rag")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=None)
    sub.add_parser("config")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--course", required=True)
    a.add_argument("--video", "-v", default=None)
    g = sub.add_parser("ingest")
    g.add_argument("url")
    g.add_argument("--force", action="store_true")
    v = sub.add_parser("eval-validate")
    v.add_argument("file")
    r = sub.add_parser("eval-run")
    r.add_argument("--dataset", required=True)
    r.add_argument("--label", default=None)
    r.add_argument("--split", default=None)
    r.add_argument("--course", default=None)
    r.add_argument("--override", action="append", default=[])
    s = sub.add_parser("eval-rescore")
    s.add_argument("--run", required=True)
    args = p.parse_args()
    raise SystemExit(
        {
            "smoke": cmd_smoke,
            "serve": cmd_serve,
            "config": cmd_config,
            "ask": cmd_ask,
            "ingest": cmd_ingest,
            "eval-validate": cmd_eval_validate,
            "eval-run": cmd_eval_run,
            "eval-rescore": cmd_eval_rescore,
        }[args.cmd](args)
    )


if __name__ == "__main__":
    main()
