from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from marine_arena_companion.api import CompanionHTTPServer
from marine_arena_companion.config import ConfigStore
from marine_arena_companion.db.repository import Repository, SCHEMA_VERSION
from marine_arena_companion.replay.scanner import ReplayScanner
from marine_arena_companion.replay.watcher import ReplayWatcher


def request_json(url: str, method: str = "GET", data: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_status_route(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    scanner = ReplayScanner(repository)
    watcher = ReplayWatcher(scanner)
    replay_folder = tmp_path / "private-replays"
    replay_folder.mkdir()
    watcher.start(replay_folder)
    server = CompanionHTTPServer("127.0.0.1", 0, config, repository, scanner, watcher)
    server.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.port}/api/status", timeout=3) as response:
            body = json.loads(response.read())
        with urlopen(f"http://127.0.0.1:{server.port}/assets/app.js", timeout=3) as response:
            script = response.read()
            assert response.headers["Content-Type"].startswith("application/javascript")
        with urlopen(f"http://127.0.0.1:{server.port}/", timeout=3) as response:
            dashboard = response.read().decode("utf-8")
        with urlopen(f"http://127.0.0.1:{server.port}/assets/NotoSans-VF.ttf", timeout=3) as response:
            font = response.read()
            assert len(font) > 1_000_000
        with urlopen(f"http://127.0.0.1:{server.port}/api/diagnostics", timeout=3) as response:
            diagnostics = json.loads(response.read())
        assert body["library"]["total"] == 0
        assert body["watcher"]["active"] is True
        assert body["watcher"]["folder"] == replay_folder.name
        assert str(tmp_path) not in repr(body)
        assert "config" not in body
        assert "schema_version" not in body["library"]
        assert "current" not in body["scan"]
        assert body["application_version"] == "0.1.0"
        assert b"async function refresh" in script
        assert b"Replay evidence only" not in script
        assert b"Recorded tendencies, not a prediction" not in script
        assert b"APM counts player-linked commands" not in script
        assert "Local data · Completed replays only" not in dashboard
        assert "Units & upgrades" in script.decode("utf-8")
        assert "Maps & results" in script.decode("utf-8")
        assert "Achievements" not in script.decode("utf-8")
        assert diagnostics["about"]["database_schema_version"] == SCHEMA_VERSION
    finally:
        server.stop()
        watcher.stop()
        scanner.close()


def test_reminder_and_overlay_api_lifecycle(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    scanner = ReplayScanner(repository)
    watcher = ReplayWatcher(scanner)
    server = CompanionHTTPServer("127.0.0.1", 0, config, repository, scanner, watcher)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        status, created = request_json(
            f"{base}/api/reminders",
            "POST",
            {
                "title": "Build a marine wave",
                "at_seconds": 0,
                "category": "unit_combo",
                "item_label": "2 Marines + 1 Medic",
                "note": "Fixture cue",
                "enabled": True,
            },
        )
        assert status == 201
        reminder_id = created["id"]
        status, state = request_json(f"{base}/api/overlay/state")
        assert status == 200
        assert state["reminders"][0]["id"] == reminder_id
        assert state["session"]["running"] is False

        status, _ = request_json(
            f"{base}/api/reminders",
            "POST",
            {"title": "Bad category", "at_seconds": 30, "category": "guess"},
        )
        assert status == 400

        status, dismissed = request_json(
            f"{base}/api/overlay/reminders/{reminder_id}/dismiss", "POST",
        )
        assert status == 200
        assert dismissed["session"]["dismissed_reminder_ids"] == [reminder_id]

        status, updated = request_json(
            f"{base}/api/reminders/{reminder_id}",
            "PUT",
            {
                "title": "Remember the elite wave",
                "at_seconds": 90,
                "category": "elite",
                "item_label": "Elite",
                "note": "",
                "enabled": False,
            },
        )
        assert status == 200 and updated["updated"] is True
        status, state = request_json(f"{base}/api/overlay/state")
        assert state["reminders"] == []
        assert state["session"]["dismissed_reminder_ids"] == []
        status, body = request_json(f"{base}/api/reminders/{reminder_id}", "DELETE")
        assert status == 200 and body["deleted"] is True
        status, body = request_json(f"{base}/api/reminders/{reminder_id}", "DELETE")
        assert status == 404 and body["deleted"] is False

        status, started = request_json(f"{base}/api/overlay/start", "POST")
        assert status == 200 and started["session"]["running"] is True
        status, paused = request_json(f"{base}/api/overlay/pause", "POST")
        assert status == 200 and paused["session"]["running"] is False
        status, reset = request_json(f"{base}/api/overlay/reset", "POST")
        assert status == 200 and reset["session"]["elapsed_seconds"] == 0

        with urlopen(f"{base}/overlay", timeout=3) as response:
            overlay_html = response.read().decode("utf-8")
        assert "YOUR TIMED CUES" in overlay_html
        with urlopen(base, timeout=3) as response:
            dashboard_html = response.read().decode("utf-8")
        assert "Overlay reminders" in dashboard_html
        assert "unit_combo" in dashboard_html
        assert "id=\"reminderSearch\"" in dashboard_html
    finally:
        server.stop()
        scanner.close()


def test_required_local_profile_separates_owner_from_opponents(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    replay_id = repository.create_replay(tmp_path / "profile-match.SC2Replay", "profile-digest", 12, None)
    assert replay_id is not None
    repository.update_replay(
        replay_id, status="ANALYZED", map_name="Marine Arena",
        classification="MARINE_ARENA", duration_seconds=60,
    )
    own_id = repository.add_player(
        replay_id, "toon:profile", "<TEAM><sp/>ProfilePlayer", 1, 1, "Victory", user_id=31,
    )
    opponent_id = repository.add_player(
        replay_id, "toon:opponent", "OtherPlayer", 2, 2, "Defeat", user_id=42,
    )
    repository.add_events(replay_id, [
        {"event_type": "NNet.Game.SCmdEvent", "game_seconds": 10, "payload": {"_userid": 31}},
        {"event_type": "NNet.Game.SCmdEvent", "game_seconds": 30, "payload": {"_userid": 31}},
    ])
    scanner = ReplayScanner(repository)
    server = CompanionHTTPServer(
        "127.0.0.1", 0, config, repository, scanner, ReplayWatcher(scanner),
    )
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        status, initial = request_json(f"{base}/api/status")
        assert status == 200 and initial["user_profile"]["configured"] is False
        status, invalid = request_json(
            f"{base}/api/profile", "POST", {"battle_tag": "not-a-tag"},
        )
        assert status == 400
        status, saved = request_json(
            f"{base}/api/profile", "POST", {"battle_tag": "ProfilePlayer#1234"},
        )
        assert status == 200
        assert saved == {
            "configured": True,
            "battle_tag": "ProfilePlayer#1234",
            "display_name": "ProfilePlayer",
        }
        status, own_profile = request_json(f"{base}/api/profile")
        assert status == 200
        assert own_profile["player_ids"] == [own_id]
        assert own_profile["all_time_games"] == 1
        assert own_profile["profile"]["wins"] == 1
        assert own_profile["profile"]["average_apm"] == 2.0
        assert own_profile["profile"]["apm_games"] == 1
        status, opponents = request_json(f"{base}/api/opponents")
        assert status == 200
        assert opponents["total"] == 1
        assert [player["id"] for player in opponents["items"]] == [opponent_id]
        assert own_id not in {player["id"] for player in opponents["items"]}
        with urlopen(base, timeout=3) as response:
            dashboard = response.read().decode("utf-8")
        assert "profileDialog" in dashboard
        assert 'data-player-section="mine"' in dashboard
        assert 'data-player-section="opponents"' in dashboard
    finally:
        server.stop()
        scanner.close()


def test_library_routes_filter_and_paginate_replays_and_players(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    scanner = ReplayScanner(repository)
    watcher = ReplayWatcher(scanner)
    marine_id = repository.create_replay(tmp_path / "Marine Arena.SC2Replay", "marine-digest", 12, None)
    other_id = repository.create_replay(tmp_path / "Ladder.SC2Replay", "ladder-digest", 8, None)
    assert marine_id is not None and other_id is not None
    repository.update_replay(
        marine_id, status="ANALYZED", map_name="Marine Arena EU",
        classification="MARINE_ARENA_EU", base_build=12345,
    )
    repository.update_replay(other_id, status="DECODE_FAILED", error="Fixture failure")
    player_id = repository.add_player(marine_id, "fixture-player", "Fixture Player", 1, 1, "Victory")
    repository.add_player(other_id, "fixture-player", "Fixture Player", 1, 1, "Defeat")
    server = CompanionHTTPServer("127.0.0.1", 0, config, repository, scanner, watcher)
    server.start()
    try:
        query = urlencode({
            "q": "Marine", "classification": "MARINE_ARENA", "limit": 1, "offset": 0,
        })
        with urlopen(f"http://127.0.0.1:{server.port}/api/replays?{query}", timeout=3) as response:
            replay_page = json.loads(response.read())
        with urlopen(f"http://127.0.0.1:{server.port}/api/replays?status=NEEDS_ATTENTION", timeout=3) as response:
            issue_page = json.loads(response.read())
        query = urlencode({"q": "Fixture", "sort": "games", "limit": 1})
        with urlopen(f"http://127.0.0.1:{server.port}/api/opponents?{query}", timeout=3) as response:
            player_page = json.loads(response.read())
        query = urlencode({"q": "Marine", "classification": "MARINE_ARENA"})
        with urlopen(f"http://127.0.0.1:{server.port}/api/opponents/{player_id}?{query}", timeout=3) as response:
            player_detail = json.loads(response.read())

        assert replay_page["total"] == 1
        assert replay_page["items"][0]["id"] == marine_id
        assert "original_path" not in replay_page["items"][0]
        assert "decode_error" not in replay_page["items"][0]
        assert issue_page["total"] == 1
        assert issue_page["items"][0]["decode_status"] == "DECODE_FAILED"
        assert player_page["total"] == 1
        assert player_page["items"][0]["games"] == 2
        assert "identity_key" not in player_page["items"][0]
        assert player_detail["all_time_games"] == 2
        assert player_detail["matches_total"] == 1
        assert player_detail["matches"][0]["replay_id"] == marine_id
        assert "identity_key" not in player_detail
        assert "map_version" not in player_detail["matches"][0]
        assert "team" not in player_detail["matches"][0]
        assert "note" not in player_detail
    finally:
        server.stop()
        scanner.close()


def test_player_profile_summarizes_attributed_results_actions_and_units(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    replay_id = repository.create_replay(tmp_path / "profile.SC2Replay", "profile-digest", 12, None)
    assert replay_id is not None
    repository.update_replay(
        replay_id, status="ANALYZED", map_name="Marine Arena", duration_seconds=120,
    )
    player_id = repository.add_player(
        replay_id, "toon:1", "<L4GNDS><sp/>StЯям", 1, 1, "Victory", user_id=11,
    )
    repository.add_player(replay_id, "toon:2", "Other Player", 2, 2, "Defeat", user_id=22)
    repository.add_events(replay_id, [
        {
            "event_type": "NNet.Game.SCmdEvent", "raw_identifier": None,
            "payload": {"_userid": 11},
        },
        {
            "event_type": "NNet.Game.SSelectionDeltaEvent", "raw_identifier": None,
            "payload": {"_userid": 11},
        },
        {
            "event_type": "NNet.Replay.Tracker.SUnitBornEvent",
            "raw_identifier": "Marine", "payload": {
                "m_unitTagIndex": 501, "m_unitTagRecycle": 0,
                "m_controlPlayerId": 1, "m_unitTypeName": "Marine",
            },
        },
        {
            "event_type": "NNet.Replay.Tracker.SUnitDiedEvent",
            "raw_identifier": None, "payload": {
                "m_unitTagIndex": 501, "m_unitTagRecycle": 0,
            },
        },
        {
            "event_type": "NNet.Replay.Tracker.SUpgradeEvent",
            "raw_identifier": "InfantryWeaponsLevel1",
            "payload": {"m_playerId": 1, "m_upgradeTypeName": "InfantryWeaponsLevel1"},
        },
    ])
    scanner = ReplayScanner(repository)
    server = CompanionHTTPServer(
        "127.0.0.1", 0, config, repository, scanner, ReplayWatcher(scanner),
    )
    server.start()
    try:
        status, profile = request_json(
            f"http://127.0.0.1:{server.port}/api/opponents/{player_id}",
        )
        assert status == 200
        assert profile["display_name"] == "StЯям"
        assert profile["clan_tag"] == "L4GNDS"
        assert profile["profile"]["wins"] == 1
        assert profile["profile"]["win_rate"] == 100
        assert profile["profile"]["average_apm"] == 1
        assert profile["profile"]["apm_games"] == 1
        assert profile["profile"]["units_lost"] == 1
        assert profile["profile"]["unit_loss_games"] == 1
        assert profile["profile"]["top_units"] == [{"name": "Marine", "count": 1}]
        assert profile["profile"]["top_upgrades"] == [
            {"name": "Infantry Weapons Level 1", "count": 1},
        ]
        assert profile["name_history"][0]["display_name"] == "StЯям"
        assert "identity_key" not in profile
    finally:
        server.stop()
        scanner.close()


def test_replay_dashboard_api_returns_plain_language_summary_only(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    replay_id = repository.create_replay(tmp_path / "match.SC2Replay", "summary-digest", 123, None)
    assert replay_id is not None
    repository.add_events(replay_id, [
        {"event_type": "NNet.Replay.Tracker.SUnitBornEvent", "game_seconds": 5, "raw_identifier": "Marine", "payload": {"secret": "payload"}},
        {"event_type": "NNet.Replay.Tracker.SUnitBornEvent", "game_seconds": 6, "raw_identifier": "Medic", "payload": {}},
        {"event_type": "NNet.Replay.Tracker.SUnitDiedEvent", "game_seconds": 7, "raw_identifier": "Marine", "payload": {}},
        {"event_type": "NNet.Replay.Tracker.SUpgradeEvent", "game_seconds": 8, "raw_identifier": "UpgradeId", "payload": {}},
        {"event_type": "NNet.Replay.Tracker.SPlayerStatsEvent", "game_seconds": 9, "raw_identifier": None, "payload": {}},
    ])
    repository.update_replay(
        replay_id, status="ANALYZED", map_name="Marine Arena", duration_seconds=180,
    )
    repository.add_failure(
        replay_id, "private-path/match.SC2Replay", "partial-decoder",
        "NNet.Replay.Tracker.SPlayerStatsEvent raw warning",
    )
    scanner = ReplayScanner(repository)
    watcher = ReplayWatcher(scanner)
    server = CompanionHTTPServer("127.0.0.1", 0, config, repository, scanner, watcher)
    server.start()
    try:
        status, detail = request_json(f"http://127.0.0.1:{server.port}/api/replays/{replay_id}")
        assert status == 200
        assert detail["activity"] == {
            "units_created": 2,
            "units_destroyed": 1,
            "upgrades_completed": 1,
            "abilities_used": 0,
        }
        assert "events" not in detail
        assert "original_path" not in detail
        assert "decode_error" not in detail
        assert "payload_json" not in detail
        health_status, health = request_json(f"http://127.0.0.1:{server.port}/api/health")
        assert health_status == 200
        assert health["issues"][0]["title"] == "Some optional match details are missing"
        assert "NNet" not in repr(health)
        assert "private-path" not in repr(health)
    finally:
        server.stop()
        scanner.close()


def test_replay_summary_returns_player_performance_score_units_build_order_and_graphs(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    repository = Repository(tmp_path / "db.sqlite")
    replay_id = repository.create_replay(tmp_path / "match.SC2Replay", "detailed-match", 123, None)
    assert replay_id is not None
    repository.update_replay(
        replay_id, status="ANALYZED", map_name="Marine Arena EU",
        classification="MARINE_ARENA_EU", duration_seconds=120,
    )
    player_id = repository.add_player(
        replay_id, "toon:one", "<TEAM><sp/>Player", 1, 1, "Victory", user_id=11,
    )
    opponent_id = repository.add_player(
        replay_id, "toon:two", "Opponent", 2, 2, "Defeat", user_id=22,
    )
    repository.add_events(replay_id, [
        {"event_type": "NNet.Game.SCmdEvent", "game_seconds": 10, "payload": {"_userid": 11}},
        {"event_type": "NNet.Game.SCmdEvent", "game_seconds": 70, "payload": {"_userid": 11}},
        {"event_type": "NNet.Replay.Tracker.SPlayerStatsEvent", "game_seconds": 0, "payload": {
            "m_playerId": 1, "m_stats": {
                "m_scoreValueMineralsCurrent": 100, "m_scoreValueVespeneCurrent": 50,
                "m_scoreValueFoodUsed": 15, "m_scoreValueFoodMade": 15,
                "m_scoreValueWorkersActiveCount": 8,
                "m_scoreValueMineralsCollectionRate": 20,
                "m_scoreValueMineralsUsedCurrentArmy": 100,
                "m_scoreValueMineralsUsedCurrentEconomy": 200,
                "m_scoreValueMineralsUsedCurrentTechnology": 30,
            },
        }},
        {"event_type": "NNet.Replay.Tracker.SPlayerStatsEvent", "game_seconds": 60, "payload": {
            "m_playerId": 1, "m_stats": {
                "m_scoreValueMineralsCurrent": 200, "m_scoreValueVespeneCurrent": 100,
                "m_scoreValueFoodUsed": 20, "m_scoreValueFoodMade": 30,
                "m_scoreValueWorkersActiveCount": 10,
                "m_scoreValueMineralsCollectionRate": 40,
                "m_scoreValueMineralsUsedCurrentArmy": 300,
                "m_scoreValueMineralsUsedCurrentEconomy": 400,
                "m_scoreValueMineralsUsedCurrentTechnology": 60,
            },
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitBornEvent", "game_seconds": 5, "raw_identifier": "SCV", "payload": {
            "m_unitTagIndex": 501, "m_unitTagRecycle": 0, "m_controlPlayerId": 1, "m_unitTypeName": "SCV",
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitBornEvent", "game_seconds": 15, "raw_identifier": "Marine", "payload": {
            "m_unitTagIndex": 502, "m_unitTagRecycle": 0, "m_controlPlayerId": 1, "m_unitTypeName": "Marine",
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitInitEvent", "game_seconds": 20, "raw_identifier": "SupplyDepot", "payload": {
            "m_unitTagIndex": 503, "m_unitTagRecycle": 0, "m_controlPlayerId": 1, "m_unitTypeName": "SupplyDepot",
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitDiedEvent", "game_seconds": 35, "payload": {
            "m_unitTagIndex": 503, "m_unitTagRecycle": 0, "m_killerPlayerId": 2,
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitDiedEvent", "game_seconds": 40, "payload": {
            "m_unitTagIndex": 502, "m_unitTagRecycle": 0, "m_killerPlayerId": 2,
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitBornEvent", "game_seconds": 45, "raw_identifier": "Zergling", "payload": {
            "m_unitTagIndex": 601, "m_unitTagRecycle": 0, "m_controlPlayerId": 2, "m_unitTypeName": "Zergling",
        }},
        {"event_type": "NNet.Replay.Tracker.SUnitDiedEvent", "game_seconds": 50, "payload": {
            "m_unitTagIndex": 601, "m_unitTagRecycle": 0, "m_killerPlayerId": 1,
        }},
        {"event_type": "NNet.Replay.Tracker.SUpgradeEvent", "game_seconds": 80, "raw_identifier": "InfantryWeaponsLevel1", "payload": {
            "m_playerId": 1, "m_upgradeTypeName": "InfantryWeaponsLevel1",
        }},
    ])
    scanner = ReplayScanner(repository)
    server = CompanionHTTPServer(
        "127.0.0.1", 0, config, repository, scanner, ReplayWatcher(scanner),
    )
    server.start()
    try:
        status, replay = request_json(
            f"http://127.0.0.1:{server.port}/api/replays/{replay_id}",
        )
        assert status == 200
        assert replay["players"][0]["display_name"] == "Player"
        assert replay["players"][0]["slot"] == 1
        assert replay["players"][0]["team"] == 1
        report = next(item for item in replay["player_reports"] if item["player_id"] == player_id)
        assert report["display_name"] == "Player"
        assert report["slot"] == 1
        assert report["performance"] == {
            "actions_per_minute": 1.0,
            "recorded_actions": 2,
            "average_unspent_resources": 225,
            "time_supply_capped_seconds": 60,
            "workers_created": 1,
        }
        assert report["score_summary"] == {
            "resources_on_hand": 300,
            "army_investment": 300,
            "economy_investment": 400,
            "technology_investment": 60,
        }
        assert report["units"]["trained"] == 2
        assert report["units"]["units_lost"] == 1
        assert report["units"]["units_killed"] == 1
        assert report["units"]["structures_built"] == 1
        assert report["units"]["structures_razed"] == 1
        assert report["units"]["top_units"] == [
            {"name": "Marine", "count": 1}, {"name": "SCV", "count": 1},
        ]
        assert report["units"]["top_upgrades"] == [
            {"name": "Infantry Weapons Level 1", "count": 1},
        ]
        assert any(
            item["category"] == "Structure" and item["item"] == "Supply Depot"
            for item in report["build_order"]
        )
        assert any(item["category"] == "Upgrade" for item in report["build_order"])
        assert report["graphs"]["army_value"] == [
            {"time": 0.0, "value": 100.0}, {"time": 60.0, "value": 300.0},
        ]
        assert "payload_json" not in repr(replay)
        assert "NNet.Replay" not in repr(replay)
        assert opponent_id != player_id
    finally:
        server.stop()
        scanner.close()
