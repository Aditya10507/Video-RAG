"""Golden-set loading + strict validation. Rejects bad files with line numbers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REQUIRED = ("id", "question", "question_type", "answerable", "gt_segments")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load(path: str | Path) -> tuple[list[dict], list[str]]:
    """Returns (rows, errors). Errors are 'line N: ...' strings; empty means valid."""
    p = Path(path)
    if not p.exists():
        return [], [f"{p}: file not found"]
    rows, errors, seen = [], [], set()
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            errors.append(f"line {n}: invalid JSON ({e})")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {n}: must be a JSON object")
            continue
        row["_line"] = n
        errors.extend(_check_row(row, seen))
        rows.append(row)
    return rows, errors


def _check_row(row: dict, seen: set) -> list[str]:
    n, errs = row["_line"], []
    for key in REQUIRED:
        if key not in row:
            errs.append(f"line {n}: missing '{key}'")
    qid = row.get("id")
    if qid is not None:
        if qid in seen:
            errs.append(f"line {n}: duplicate id '{qid}'")
        seen.add(qid)
    answerable = bool(row.get("answerable", False))
    segs = row.get("gt_segments", [])
    if not isinstance(segs, list):
        errs.append(f"line {n}: gt_segments must be a list")
        segs = []
    if answerable:
        if not segs:
            errs.append(f"line {n}: answerable=true needs >= 1 gt_segment")
        if not (row.get("reference_answer") or "").strip():
            errs.append(f"line {n}: answerable=true needs a reference_answer")
    else:
        if segs:
            errs.append(f"line {n}: answerable=false needs empty gt_segments")
    for s in segs:
        if not isinstance(s, dict):
            errs.append(f"line {n}: segment must be an object")
            continue
        if not s.get("video_id"):
            errs.append(f"line {n}: segment needs video_id")
        try:
            st, en = float(s.get("start_s", -1)), float(s.get("end_s", -1))
        except (TypeError, ValueError):
            errs.append(f"line {n}: segment times must be numbers")
            continue
        if not st < en:
            errs.append(f"line {n}: need start_s < end_s, got {st} >= {en}")
    return errs
