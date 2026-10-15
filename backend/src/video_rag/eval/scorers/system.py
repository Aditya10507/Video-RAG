"""System metrics: latency percentiles (nearest-rank) and token cost."""

from __future__ import annotations

import math


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. NULL when empty."""
    if not values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(values)))
    return sorted(values)[rank - 1]


def cost_usd(usage: dict, pricing: dict) -> float | None:
    """Tokens x per-1k prices. NULL when the model has no price entry."""
    model = (usage or {}).get("model", "")
    price = (pricing or {}).get(model)
    if not price:
        return None
    try:
        per_in = float(price.get("input_per_1k", 0))
        per_out = float(price.get("output_per_1k", 0))
    except (TypeError, ValueError):
        return None
    return (float(usage.get("input_tokens", 0)) / 1000 * per_in
            + float(usage.get("output_tokens", 0)) / 1000 * per_out)
