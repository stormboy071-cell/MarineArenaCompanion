from __future__ import annotations

import time
from pathlib import Path

from marine_arena_companion.db.repository import Repository
from marine_arena_companion.replay.decoder import DecodedReplay
from marine_arena_companion.replay.scanner import ReplayScanner
from marine_arena_companion.replay.watcher import ReplayWatcher


def test_recursive_watcher_queues_completed_replay_without_modifying_it(tmp_path: Path, monkeypatch) -> None:
    import marine_arena_companion.replay.scanner as scanner_module

    folder = tmp_path / "replay-root"
    nested = folder / "account" / "replays"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        scanner_module,
        "decode_replay",
        lambda path: DecodedReplay(123, 123, "Unrecognized Map", None, 60.0),
    )
    repository = Repository(tmp_path / "watcher.sqlite")
    scanner = ReplayScanner(repository)
    watcher = ReplayWatcher(scanner)
    replay = nested / "new-game.SC2Replay"
    contents = b"MPQ\x1b" + b"finished replay bytes"
    try:
        watcher.start(folder)
        replay.write_bytes(contents)
        deadline = time.monotonic() + 8
        while repository.summary()["analyzed"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert repository.summary()["total"] == 1
        assert repository.summary()["analyzed"] == 1
        assert repository.list_replays(1)[0]["map_classification"] == "OTHER_MAP"
        assert replay.read_bytes() == contents
    finally:
        watcher.stop()
        scanner.close()
