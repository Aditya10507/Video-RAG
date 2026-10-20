"""Live per-query evaluation: capture at ask-time, judge scores post-answer.

Everything here is best-effort observability: a tracing or scoring failure must
never break an answer. Storage lives beside the offline eval tables in
data/eval.db; the dashboard reads the same file over /api/live/*.
"""
