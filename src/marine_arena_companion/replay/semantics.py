from __future__ import annotations

import json
from pathlib import Path


class SemanticDictionary:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "version": 1, "units": {}, "upgrades": {}, "abilities": {}
        }

    def lookup(self, category: str, raw_identifier: str) -> dict[str, str] | None:
        value = self.data.get(category, {}).get(raw_identifier)
        return value if isinstance(value, dict) else None

    def entries(self) -> list[tuple[str, str, str | None, str | None]]:
        result = []
        for category in ("units", "upgrades", "abilities"):
            mappings = self.data.get(category, {})
            if not isinstance(mappings, dict):
                continue
            for raw_identifier, mapping in mappings.items():
                if isinstance(mapping, dict):
                    semantic_id = mapping.get("semantic_id")
                    label = mapping.get("label")
                    result.append((category, str(raw_identifier), semantic_id, label))
        return result
