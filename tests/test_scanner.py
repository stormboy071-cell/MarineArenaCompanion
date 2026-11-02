from __future__ import annotations

import time
from pathlib import Path

from marine_arena_companion.db.repository import Repository
from marine_arena_companion.replay.decoder import DecodedReplay
from marine_arena_companion.replay.scanner import ReplayScanner


def test_library_scan_is_async_and_deduplicates_content(tmp_path: Path, monkeypatch) -> None:
    import marine_arena_companion.replay.scanner as scanner_module

    folder = tmp_path / "replays"
    folder.mkdir()
    contents = b"MPQ\x1b" + b"fixture replay data"
    original = folder / "first.SC2Replay"
    duplicate = folder / "renamed-copy.SC2Replay"
    original.write_bytes(contents)
    duplicate.write_bytes(contents)

    monkeypatch.setattr(scanner_module, "wait_until_ready", lambda path: path.stat().st_size)
    monkeypatch.setattr(
        scanner_module,
        "decode_replay",
        lambda path: DecodedReplay(
            base_build=1,
            data_build=1,
            map_name="Marine Arena EU",
            map_version=None,
            duration_seconds=20.0,
            players=[{"name": "Fixture", "toon": {"m_region": 1, "m_programId": "S2", "m_realm": 1, "m_id": 25}, "slot": 1}],
            events=[{"event_type": "ObservedFixtureEvent", "game_loop": 44, "game_seconds": 1.96, "raw_identifier": "FixtureUnit", "payload": {"_event": "ObservedFixtureEvent"}}],
        ),
    )
    repository = Repository(tmp_path / "db.sqlite")
    scanner = ReplayScanner(repository)
    try:
        assert scanner.scan_library(folder)
        deadline = time.monotonic() + 5
        while scanner.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.02)
        status = scanner.status()
        assert status["active"] is False
        assert status["discovered"] == 2
        assert status["processed"] == 2
        assert status["skipped"] == 1
        assert status["marine_arena"] == 1
        assert repository.summary()["total"] == 1
        replay = repository.list_replays(1)[0]
        assert replay["map_classification"] == "MARINE_ARENA_EU"
        detail = repository.replay_detail(replay["id"])
        assert detail is not None
        assert detail["players"][0]["display_name"] == "Fixture"
        assert detail["events"][0]["raw_identifier"] == "FixtureUnit"
        assert original.read_bytes() == contents
        assert duplicate.read_bytes() == contents
    finally:
        scanner.close()


def test_unchanged_replays_skip_hashing_after_restart_and_changes_are_reprocessed(
    tmp_path: Path, monkeypatch,
) -> None:
    import marine_arena_companion.replay.scanner as scanner_module

    folder = tmp_path / "replays"
    folder.mkdir()
    replay_path = folder / "fixture.SC2Replay"
    replay_path.write_bytes(b"MPQ\x1b" + b"first replay fixture")
    repository = Repository(tmp_path / "db.sqlite")
    calls = {"hash": 0, "decode": 0}
    original_hash = scanner_module.sha256_file

    def hash_file(path: Path) -> str:
        calls["hash"] += 1
        return original_hash(path)

    def decode(path: Path) -> DecodedReplay:
        calls["decode"] += 1
        return DecodedReplay(
            base_build=1, data_build=1, map_name="Marine Arena", map_version=None,
            duration_seconds=20.0, players=[], events=[],
        )

    monkeypatch.setattr(scanner_module, "wait_until_ready", lambda path: path.stat().st_size)
    monkeypatch.setattr(scanner_module, "sha256_file", hash_file)
    monkeypatch.setattr(scanner_module, "decode_replay", decode)

    def finish_scan(scanner: ReplayScanner) -> dict[str, object]:
        assert scanner.scan_library(folder)
        deadline = time.monotonic() + 5
        while scanner.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scanner.status()["active"] is False
        return scanner.status()

    scanner = ReplayScanner(repository)
    assert finish_scan(scanner)["processed"] == 1
    scanner.close()
    assert calls == {"hash": 1, "decode": 1}

    scanner = ReplayScanner(repository)
    try:
        unchanged = finish_scan(scanner)
        assert unchanged["skipped"] == 1
        assert calls == {"hash": 1, "decode": 1}

        replay_path.write_bytes(b"MPQ\x1b" + b"changed replay fixture with new size")
        changed = finish_scan(scanner)
        assert changed["processed"] == 1
        assert calls == {"hash": 2, "decode": 2}
        assert repository.summary()["total"] == 2
    finally:
        scanner.close()


def test_retry_failed_reprocesses_unchanged_file(tmp_path: Path, monkeypatch) -> None:
    import marine_arena_companion.replay.scanner as scanner_module

    folder = tmp_path / "replays"
    folder.mkdir()
    replay_path = folder / "failed.SC2Replay"
    replay_path.write_bytes(b"MPQ\x1b" + b"failed fixture")
    repository = Repository(tmp_path / "db.sqlite")
    decode_calls = 0

    def decode(path: Path) -> DecodedReplay:
        nonlocal decode_calls
        decode_calls += 1
        raise RuntimeError("fixture decoder failure")

    monkeypatch.setattr(scanner_module, "wait_until_ready", lambda path: path.stat().st_size)
    monkeypatch.setattr(scanner_module, "decode_replay", decode)
    scanner = ReplayScanner(repository)

    def wait_for_queue() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with scanner.lock:
                if not scanner._queued:
                    return
            time.sleep(0.02)
        raise AssertionError("Replay scan did not finish")

    def wait_for_scan() -> None:
        deadline = time.monotonic() + 5
        while scanner.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scanner.status()["active"] is False

    try:
        assert scanner.scan_library(folder)
        wait_for_scan()
        assert decode_calls == 1

        assert scanner.scan_library(folder)
        wait_for_scan()
        assert decode_calls == 1

        assert scanner.retry_failed() == 1
        wait_for_queue()
        assert decode_calls == 2
    finally:
        scanner.close()
