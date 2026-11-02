from __future__ import annotations

import json
import platform
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from marine_arena_companion import __version__
from marine_arena_companion.db.repository import Repository, SCHEMA_VERSION


def export_diagnostics(destination: Path, repository: Repository, semantics_path: Path) -> Path:
    diagnostics = repository.diagnostics()
    diagnostics["failures"] = [
        {**failure, "path": Path(failure["path"]).name}
        for failure in diagnostics["failures"]
    ]
    try:
        s2protocol_version = version("s2protocol")
    except PackageNotFoundError:
        s2protocol_version = "unavailable"
    try:
        semantic_version = json.loads(semantics_path.read_text(encoding="utf-8")).get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        semantic_version = "unavailable"
    payload: dict[str, Any] = {
        "application_version": __version__,
        "database_schema_version": SCHEMA_VERSION,
        "runtime": platform.python_version(),
        "s2protocol_version": s2protocol_version,
        "semantic_dictionary_version": semantic_version,
        **diagnostics,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("diagnostics.json", json.dumps(payload, indent=2, ensure_ascii=False))
        for key, filename in (
            ("unknown_units", "unknown-units.json"),
            ("unknown_upgrades", "unknown-upgrades.json"),
            ("unknown_abilities", "unknown-abilities.json"),
            ("map_names", "map-versions.json"),
            ("event_frequency", "event-frequency.json"),
        ):
            bundle.writestr(filename, json.dumps(diagnostics[key], indent=2, ensure_ascii=False))
        if semantics_path.is_file():
            bundle.write(semantics_path, "marine_arena_semantics.json")
    return destination
