from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marine_arena_companion.updates import validate_repository


if len(sys.argv) != 2:
    raise SystemExit("Usage: write_update_repository.py <output-path>")

repository = os.environ.get("MARINE_ARENA_UPDATE_REPOSITORY", "").strip()
if repository:
    repository = validate_repository(repository)
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(repository, encoding="utf-8")
