from __future__ import annotations

from enum import StrEnum


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def confidence(sample_count: int, consistency: float, recency_weight: float = 1.0) -> Confidence:
    if sample_count < 5 or consistency < 0.5 or recency_weight < 0.25:
        return Confidence.LOW
    if sample_count >= 15 and consistency >= 0.75 and recency_weight >= 0.6:
        return Confidence.HIGH
    return Confidence.MEDIUM
