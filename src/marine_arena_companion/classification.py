from __future__ import annotations

import re
from enum import StrEnum


class MapClassification(StrEnum):
    MARINE_ARENA_EU = "MARINE_ARENA_EU"
    MARINE_ARENA_OTHER = "MARINE_ARENA_OTHER"
    OTHER_MAP = "OTHER_MAP"
    UNKNOWN = "UNKNOWN"


_MARINE_ARENA = re.compile(r"\bmarine[\s_-]*arena\b", re.IGNORECASE)


def classify_map(map_name: str | None) -> MapClassification:
    if not map_name or not map_name.strip():
        return MapClassification.UNKNOWN
    cleaned = map_name.strip()
    if not _MARINE_ARENA.search(cleaned):
        return MapClassification.OTHER_MAP
    if re.search(r"\bEU\b", cleaned, re.IGNORECASE):
        return MapClassification.MARINE_ARENA_EU
    return MapClassification.MARINE_ARENA_OTHER
