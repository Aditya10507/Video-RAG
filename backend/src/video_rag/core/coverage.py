"""The abstention gate. A rule in code, never a model judgement."""

from __future__ import annotations


def gate(top1: float, scores: list[float], t_high: float, t_low: float) -> str:
    if not scores:
        return "not_covered"
    coverage = sum(1 for s in scores if s > t_low)
    if top1 >= t_high:
        return "covered"
    if top1 < t_low and coverage == 0:
        return "not_covered"
    return "verify"
