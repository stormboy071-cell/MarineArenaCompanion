from __future__ import annotations

from marine_arena_companion.analysis.confidence import confidence


def frequency_summary(observed: int, total: int, consistency: float, recency_weight: float = 1.0) -> dict[str, object]:
    if total <= 0 or observed < 0 or observed > total:
        raise ValueError("frequency counts must satisfy 0 <= observed <= total and total > 0")
    return {
        "observed": observed,
        "total": total,
        "percentage": round(100 * observed / total, 1),
        "confidence": confidence(total, consistency, recency_weight).value,
        "kind": "INFERRED",
    }
