from __future__ import annotations

import statistics
from typing import Iterable


def aggregate_timings(seconds: Iterable[float]) -> dict[str, float | int] | None:
    values = sorted(float(value) for value in seconds if value >= 0)
    if not values:
        return None
    median = statistics.median(values)
    low = values[int((len(values) - 1) * 0.25)]
    high = values[int((len(values) - 1) * 0.75)]
    return {"samples": len(values), "median_seconds": median, "p25_seconds": low, "p75_seconds": high}
