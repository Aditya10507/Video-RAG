"""Command line entry point: smoke, serve, ingest, ask, config."""

from __future__ import annotations

import argparse
import asyncio
import json

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
            args.url
        )
    )
    print(json.dumps(report, indent=2))
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
    args = p.parse_args()
    raise SystemExit(
        {
            "smoke": cmd_smoke,
            "serve": cmd_serve,
            "config": cmd_config,
            "ask": cmd_ask,
            "ingest": cmd_ingest,
        }[args.cmd](args)
    )


if __name__ == "__main__":
    main()
