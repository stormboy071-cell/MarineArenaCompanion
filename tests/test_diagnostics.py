from __future__ import annotations

import json
import zipfile
from pathlib import Path

from marine_arena_companion.db.repository import Repository
from marine_arena_companion.diagnostics import export_diagnostics


def test_diagnostics_export_contains_identifiers_not_replay_files(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "database.sqlite")
    semantics = tmp_path / "semantics.json"
    semantics.write_text(json.dumps({"version": 1, "units": {}, "upgrades": {}, "abilities": {}}))
    archive_path = export_diagnostics(tmp_path / "diagnostics.zip", repository, semantics)
    with zipfile.ZipFile(archive_path) as bundle:
        names = set(bundle.namelist())
        payload = json.loads(bundle.read("diagnostics.json"))
    assert {
        "diagnostics.json", "unknown-units.json", "unknown-upgrades.json",
        "unknown-abilities.json", "map-versions.json", "event-frequency.json",
        "marine_arena_semantics.json",
    }.issubset(names)
    assert payload["application_version"] == "0.1.0"
    assert not any(name.lower().endswith(".sc2replay") for name in names)
