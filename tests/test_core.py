from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from marine_arena_companion.analysis.confidence import Confidence, confidence
from marine_arena_companion.analysis.identity import player_identity, split_player_name
from marine_arena_companion.analysis.opponent import frequency_summary
from marine_arena_companion.analysis.timing import aggregate_timings
from marine_arena_companion.classification import MapClassification, classify_map
from marine_arena_companion.config import AppConfig, ConfigStore, validate_replay_folder
from marine_arena_companion.db.repository import Repository, SCHEMA_VERSION
from marine_arena_companion.replay.hashing import sha256_file
from marine_arena_companion.replay.readiness import ReplayNotReadyError, wait_until_ready
from marine_arena_companion.replay.semantics import SemanticDictionary
from marine_arena_companion.timing import MAX_ELAPSED_SECONDS


def test_sha256_duplicate_content_is_location_independent(tmp_path: Path) -> None:
    first = tmp_path / "first.SC2Replay"
    second = tmp_path / "renamed.SC2Replay"
    contents = b"fixture replay bytes"
    first.write_bytes(contents)
    second.write_bytes(contents)
    assert sha256_file(first) == sha256_file(second) == hashlib.sha256(contents).hexdigest()


def test_config_persists_and_validates(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.update(
        replay_folder=str(tmp_path), watcher_enabled=False,
        update_repository="community/marine-companion", last_update_check=1234.5,
        battle_tag="PlayerName#1234",
    )
    assert AppConfig.load(path).battle_tag == "PlayerName#1234"
    assert AppConfig.load(path).replay_folder == str(tmp_path)
    assert AppConfig.load(path).watcher_enabled is False
    assert AppConfig.load(path).update_repository == "community/marine-companion"
    assert AppConfig.load(path).last_update_check == 1234.5
    assert validate_replay_folder(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError):
        validate_replay_folder(tmp_path / "missing")
    with pytest.raises(ValueError):
        store.update(battle_tag="missing-discriminator")


def test_battle_tag_matches_recorded_name_history(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "identity.sqlite")
    first_replay = repository.create_replay(tmp_path / "first.SC2Replay", "identity-first", 10, None)
    second_replay = repository.create_replay(tmp_path / "second.SC2Replay", "identity-second", 10, None)
    assert first_replay is not None and second_replay is not None
    player_id = repository.add_player(first_replay, "toon:stable", "OldName", 1, 1, "Victory")
    repository.add_player(second_replay, "toon:stable", "CurrentName", 1, 1, "Victory")

    assert repository.player_ids_for_battle_tag("OldName#1234") == [player_id]
    assert repository.player_ids_for_battle_tag("CurrentName#1234") == [player_id]
    assert repository.player_ids_for_battle_tag("OtherName#1234") == []


def test_readiness_waits_for_stable_mpq_header(tmp_path: Path) -> None:
    replay = tmp_path / "ready.SC2Replay"
    replay.write_bytes(b"MPQ\x1b" + b"\x00" * 24)
    assert wait_until_ready(replay, attempts=3, interval=0, sleeper=lambda _: None) == 28
    replay.write_bytes(b"not an mpq archive")
    with pytest.raises(ReplayNotReadyError):
        wait_until_ready(replay, attempts=2, interval=0, sleeper=lambda _: None)


def test_database_schema_and_sha256_unique_constraint(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "marine_arena.db")
    path = tmp_path / "game.SC2Replay"
    path.write_bytes(b"x" * 20)
    digest = sha256_file(path)
    first = repo.create_replay(path, digest, path.stat().st_size, None)
    second = repo.create_replay(tmp_path / "copy.SC2Replay", digest, path.stat().st_size, None)
    assert first is not None
    assert second is None
    assert repo.summary()["total"] == 1
    with repo.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_health_summary_uses_plain_language_and_hides_decoder_details(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "health.sqlite")
    replay_id = repository.create_replay(tmp_path / "private-path" / "match.SC2Replay", "health-digest", 12, None)
    assert replay_id is not None
    repository.update_replay(replay_id, status="DECODE_FAILED", error="NNet raw exception")
    repository.add_failure(
        replay_id, "private-path/match.SC2Replay", "analysis",
        "NNet.Replay.Tracker.SUnitBornEvent raw decoder exception",
    )

    health = repository.health_summary()
    assert health["library"]["failed"] == 1
    assert health["issues"] == [{
        "title": "Some replays could not be fully analyzed",
        "action": "Use Retry Failed Replays to try them again.",
        "count": 1,
        "severity": "warning",
    }]
    assert "private-path" not in repr(health)
    assert "NNet" not in repr(health)


def test_legacy_database_schema_migrates_existing_records(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite"
    replay_path = tmp_path / "legacy.SC2Replay"
    replay_path.write_bytes(b"legacy replay fixture")
    digest = hashlib.sha256(replay_path.read_bytes()).hexdigest()
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE replays (
                id INTEGER PRIMARY KEY, sha256 TEXT, path TEXT, map_name TEXT,
                build INTEGER, analyzed_at TEXT, status TEXT, error TEXT
            );
            CREATE TABLE players (
                id INTEGER PRIMARY KEY, replay_id INTEGER, name TEXT, toon_id TEXT,
                team_id INTEGER, result TEXT
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, replay_id INTEGER, gameloop INTEGER,
                kind TEXT, player_id INTEGER, payload TEXT
            );
            CREATE INDEX idx_players_name ON players(name);
            CREATE INDEX idx_events_replay ON events(replay_id);
            """
        )
        conn.execute(
            "INSERT INTO replays VALUES(?,?,?,?,?,?,?,?)",
            (7, digest, str(replay_path), "Marine Arena EU", 12345,
             "2026-10-30T12:00:00+00:00", "complete", None),
        )
        conn.execute(
            "INSERT INTO players VALUES(?,?,?,?,?,?)",
            (3, 7, "Legacy Player", "toon-123", 1, "Win"),
        )
        conn.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?)",
            (11, 7, 224, "LegacyEvent", 3, '{"value":"kept"}'),
        )

    repository = Repository(database)
    replay = repository.list_replays(1)[0]
    detail = repository.replay_detail(replay["id"])
    assert replay["filename"] == replay_path.name
    assert replay["map_name"] == "Marine Arena EU"
    assert replay["decode_status"] == "ANALYZED"
    assert detail is not None
    assert detail["base_build"] == 12345
    assert detail["players"][0]["display_name"] == "Legacy Player"
    assert detail["players"][0]["team"] == 1
    assert detail["events"][0]["event_type"] == "LegacyEvent"
    assert detail["events"][0]["game_seconds"] == 10.0
    assert json.loads(detail["events"][0]["payload_json"]) == {
        "value": "kept",
        "_legacy_player_id": 3,
    }
    with repository.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM legacy_v0_replays").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM legacy_v0_players").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM legacy_v0_events").fetchone()[0] == 1

    Repository(database)
    assert repository.summary()["total"] == 1
    with repository.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM replay_files").fetchone()[0] == 1


def test_version_two_database_adds_and_seeds_replay_file_index(tmp_path: Path) -> None:
    database = tmp_path / "schema-v2.sqlite"
    repository = Repository(database)
    with repository.connection() as conn:
        conn.execute("DROP INDEX idx_replay_files_replay")
        conn.execute("DROP TABLE replay_files")
        conn.execute(
            """INSERT INTO replays
               (sha256,original_path,filename,file_size,modified_time,imported_at,decode_status)
               VALUES('known-digest','known.SC2Replay','known.SC2Replay',42,
                      '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','ANALYZED')"""
        )
        conn.execute("PRAGMA user_version=2")

    migrated = Repository(database)
    assert migrated.replay_file_states() == {
        "known.sc2replay": (42, "2026-01-01T00:00:00+00:00", "ANALYZED"),
    }


def test_version_one_database_adds_reminder_table_without_data_loss(tmp_path: Path) -> None:
    database = tmp_path / "schema-v1.sqlite"
    repository = Repository(database)
    with repository.connection() as conn:
        conn.execute("DROP INDEX idx_overlay_reminders_enabled_time")
        conn.execute("DROP TABLE overlay_reminders")
        conn.execute("PRAGMA user_version=1")
        conn.execute(
            """INSERT INTO replays
               (sha256,original_path,filename,file_size,imported_at,decode_status)
               VALUES('kept-digest','kept.SC2Replay','kept.SC2Replay',10,'2026-01-01','PROCESSING')"""
        )

    migrated = Repository(database)
    with migrated.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM replays").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM overlay_reminders").fetchone()[0] == 0


def test_overlay_reminders_create_filter_edit_and_delete(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "reminders.sqlite")
    first = repository.create_reminder(
        title="Prepare the unit wave",
        at_seconds=75,
        category="unit_combo",
        item_label="2 Marines + 1 Medic",
        note="Use your saved build",
    )
    second = repository.create_reminder(
        title="Check hero timing", at_seconds=45, category="hero", item_label="Raynor",
    )
    assert [item["id"] for item in repository.list_reminders(enabled_only=True)] == [second, first]
    assert repository.update_reminder(
        first, title="Updated reminder", at_seconds=90, category="elite",
        item_label="Elite wave", note="", enabled=False,
    )
    assert [item["id"] for item in repository.list_reminders(enabled_only=True)] == [second]
    assert repository.list_reminders()[1]["title"] == "Updated reminder"
    assert repository.delete_reminder(second)
    remaining = repository.list_reminders()
    assert len(remaining) == 1
    assert remaining[0]["id"] == first
    assert remaining[0]["enabled"] == 0


def test_overlay_reminder_accepts_match_time_over_one_day(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "long-match-time.sqlite")
    reminder_id = repository.create_reminder(
        title="Check late game", at_seconds=1 * 3600 + 24 * 60 + 3, category="custom",
    )
    reminder = next(item for item in repository.list_reminders() if item["id"] == reminder_id)
    assert reminder["at_seconds"] == 5043


@pytest.mark.parametrize(
    ("title", "at_seconds", "category"),
    [
        ("", 10, "hero"),
        ("Cue", -1, "hero"),
        ("Cue", MAX_ELAPSED_SECONDS + 1, "hero"),
        ("Cue", True, "hero"),
        ("Cue", 10, "unrecognized"),
    ],
)
def test_overlay_reminder_rejects_invalid_values(
    tmp_path: Path, title: str, at_seconds: int, category: str,
) -> None:
    repository = Repository(tmp_path / "invalid-reminder.sqlite")
    with pytest.raises(ValueError):
        repository.create_reminder(title=title, at_seconds=at_seconds, category=category)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Marine Arena EU", MapClassification.MARINE_ARENA_EU),
        ("Marine_Arena", MapClassification.MARINE_ARENA_OTHER),
        ("Some Ladder Map", MapClassification.OTHER_MAP),
        (None, MapClassification.UNKNOWN),
    ],
)
def test_map_classification_is_conservative(name: str | None, expected: MapClassification) -> None:
    assert classify_map(name) == expected


def test_semantic_mapping_keeps_unknown_identifiers_unknown(tmp_path: Path) -> None:
    path = tmp_path / "semantics.json"
    path.write_text(json.dumps({"units": {"Known": {"label": "Known unit"}}, "upgrades": {}, "abilities": {}}))
    semantics = SemanticDictionary(path)
    assert semantics.lookup("units", "Known") == {"label": "Known unit"}
    assert semantics.lookup("units", "Unmapped") is None
    assert semantics.entries() == [("units", "Known", None, "Known unit")]


def test_diagnostics_excludes_explicitly_mapped_unit_identifiers(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "semantics.sqlite")
    replay_path = tmp_path / "sample.SC2Replay"
    replay_path.write_bytes(b"fixture")
    replay_id = repository.create_replay(replay_path, "digest", 7, None)
    assert replay_id is not None
    repository.save_semantic_mappings([("units", "ObservedUnit", "marine", "Mapped marine")])
    repository.add_events(replay_id, [{
        "event_type": "NNet.Replay.Tracker.SUnitBornEvent",
        "game_loop": 44,
        "game_seconds": 1.96,
        "raw_identifier": "ObservedUnit",
        "semantic_category": "units",
        "semantic_mapping": {"semantic_id": "marine", "label": "Mapped marine"},
        "payload": {"_event": "NNet.Replay.Tracker.SUnitBornEvent"},
    }])
    assert repository.diagnostics()["unknown_units"] == []


def test_identity_prefers_toon_and_does_not_merge_name_only_players() -> None:
    first, _ = player_identity({"name": "SameName", "toon": {"m_region": 1, "m_programId": "S2", "m_realm": 1, "m_id": 25}})
    second, _ = player_identity({"name": "SameName", "toon": {"m_region": 1, "m_programId": "S2", "m_realm": 1, "m_id": 26}})
    assert first != second
    assert player_identity({"name": "SameName"}, "replay-a")[0] != player_identity({"name": "SameName"}, "replay-b")[0]


def test_battlenet_name_format_splits_clan_and_removes_sp_markup() -> None:
    assert split_player_name("<L4GNDS><sp/>StЯям") == ("L4GNDS", "StЯям")
    assert split_player_name("Player<sp/>Two") == (None, "Player Two")
    assert split_player_name("&lt;MGR&gt; TbF") == (None, "<MGR> TbF")


def test_version_three_database_backfills_player_user_ids(tmp_path: Path) -> None:
    database = tmp_path / "schema-v3.sqlite"
    repository = Repository(database)
    replay_path = tmp_path / "profile.SC2Replay"
    replay_path.write_bytes(b"fixture")
    replay_id = repository.create_replay(replay_path, "profile-digest", 7, None)
    assert replay_id is not None
    player_id = repository.add_player(replay_id, "profile-toon", "Profile Player", 1, 1, "Win")
    with repository.connection() as conn:
        conn.execute(
            """UPDATE replays SET metadata_json=?
               WHERE id=?""",
            (json.dumps({"details": {"m_playerList": [{"m_userId": 27}]}}), replay_id),
        )
        conn.execute("ALTER TABLE replay_players DROP COLUMN user_id")
        conn.execute("PRAGMA user_version=3")

    Repository(database)
    with repository.connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM replay_players WHERE replay_id=? AND player_id=?",
            (replay_id, player_id),
        ).fetchone()
    assert row["user_id"] == 27


def test_confidence_and_timing_aggregation() -> None:
    assert confidence(2, 1.0) is Confidence.LOW
    assert confidence(20, 0.9) is Confidence.HIGH
    result = aggregate_timings([10, 20, 30, 40, 50])
    assert result == {"samples": 5, "median_seconds": 30, "p25_seconds": 20.0, "p75_seconds": 40.0}
    assert aggregate_timings([]) is None
    assert frequency_summary(3, 10, 0.6)["kind"] == "INFERRED"


def test_s2protocol_build_modules_load_on_current_python() -> None:
    from marine_arena_companion.replay.decoder import _install_legacy_imp_compatibility

    _install_legacy_imp_compatibility()
    from s2protocol.versions import latest, list_all

    assert list_all()
    assert latest().__name__.startswith("protocol")
