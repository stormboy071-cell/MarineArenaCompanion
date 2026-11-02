from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOG = logging.getLogger(__name__)
LOOPS_PER_SECOND = 22.4


class UnsupportedProtocolError(RuntimeError):
    pass


@dataclass
class DecodedReplay:
    base_build: int | None
    data_build: int | None
    map_name: str | None
    map_version: str | None
    duration_seconds: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
    players: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _install_legacy_imp_compatibility() -> None:
    if "imp" in sys.modules:
        return
    if importlib.util.find_spec("imp") is not None:
        return

    legacy = types.ModuleType("imp")

    def find_module(name: str, paths: list[str]):
        module_path = Path(paths[0]) / f"{name}.py"
        if not module_path.is_file():
            raise ImportError(f"No module named {name}")
        return module_path.open("rb"), str(module_path), (".py", "rb", 1)

    def load_module(name: str, stream: Any, pathname: str, description: tuple[Any, ...]):
        spec = importlib.util.spec_from_file_location(name, pathname)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load protocol module {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    legacy.find_module = find_module  # type: ignore[attr-defined]
    legacy.load_module = load_module  # type: ignore[attr-defined]
    sys.modules["imp"] = legacy


def _text(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {_text(k): _text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value]
    return value


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _identifier(event: dict[str, Any]) -> str | None:
    preferred = ("m_unitTypeName", "m_upgradeTypeName", "m_abilityLink", "m_abilLink",
                 "m_unitType", "m_upgradeType", "m_ability", "m_typeName")
    stack: list[Any] = [event]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in preferred:
                if key in item and item[key] not in (None, ""):
                    value = item[key]
                    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _decoded_event(event: Any) -> dict[str, Any]:
    raw = _text(event)
    if not isinstance(raw, dict):
        raw = {"value": raw}
    event_type = str(raw.get("_event") or raw.get("event") or "UNKNOWN")
    loop = raw.get("_gameloop", raw.get("m_gameLoop"))
    try:
        game_loop = int(loop) if loop is not None else None
    except (TypeError, ValueError):
        game_loop = None
    return {
        "event_type": event_type,
        "game_loop": game_loop,
        "game_seconds": game_loop / LOOPS_PER_SECOND if game_loop is not None else None,
        "raw_identifier": _identifier(raw),
        "payload": raw,
    }


def decode_replay(path: Path, *, include_events: bool = True) -> DecodedReplay:
    try:
        from mpyq import MPQArchive
    except ImportError as exc:
        raise RuntimeError("The mpyq package is required to read replay archives") from exc

    _install_legacy_imp_compatibility()
    try:
        from s2protocol.versions import build, latest
    except ImportError as exc:
        raise RuntimeError(f"Unable to import s2protocol: {exc}") from exc

    archive = MPQArchive(str(path))
    header_content = archive.header.get("user_data_header", {}).get("content")
    if not header_content:
        raise ValueError("Replay archive is missing the MPQ user-data header")
    header = _text(latest().decode_replay_header(header_content))
    version = header.get("m_version", {})
    base_build = version.get("m_baseBuild")
    data_build = version.get("m_dataBuild")
    if not isinstance(base_build, int):
        raise ValueError("Replay header does not include a valid base build")
    try:
        protocol = build(base_build)
    except Exception as exc:
        raise UnsupportedProtocolError(f"No installed s2protocol decoder for base build {base_build}") from exc

    def read_member(name: str) -> bytes | None:
        try:
            return archive.read_file(name)
        except (KeyError, OSError):
            return None

    details_bytes = read_member("replay.details")
    if not details_bytes:
        raise ValueError("Replay is missing replay.details")
    details = _text(protocol.decode_replay_details(details_bytes))

    init_bytes = read_member("replay.initData")
    initdata: dict[str, Any] = {}
    warnings: list[str] = []
    if init_bytes:
        try:
            initdata = _text(protocol.decode_replay_initdata(init_bytes))
        except Exception as exc:
            LOG.warning("Could not decode replay.initData in %s: %s", path.name, exc)
            warnings.append(f"initData decode failed: {exc}")

    metadata: dict[str, Any] = {"header": header, "details": details, "init_data": initdata}
    metadata_bytes = read_member("replay.gamemetadata.json")
    game_metadata: dict[str, Any] = {}
    if metadata_bytes:
        try:
            game_metadata = json.loads(metadata_bytes.decode("utf-8", errors="replace"))
            metadata["game_metadata"] = game_metadata
        except json.JSONDecodeError:
            LOG.warning("Replay metadata JSON is malformed: %s", path.name)
            warnings.append("Replay metadata JSON is malformed")

    map_name = _first(game_metadata, "Title", "MapName", "name")
    if map_name is None:
        map_name = _first(details, "m_title", "m_mapTitle", "m_mapName")
    map_name = str(map_name) if map_name is not None else None
    duration_loops = _first(header, "m_elapsedGameLoops", "m_elapsedGameLoops")
    if duration_loops is None:
        duration_loops = _first(details, "m_duration")
    try:
        duration_seconds = float(duration_loops) / LOOPS_PER_SECOND if duration_loops is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    players_raw = details.get("m_playerList", [])
    players: list[dict[str, Any]] = []
    if isinstance(players_raw, list):
        for slot, player in enumerate(players_raw, start=1):
            if not isinstance(player, dict):
                continue
            players.append({
                "name": _first(player, "m_name") or "Unknown",
                "toon": player.get("m_toon"),
                "player_id": _first(player, "m_userId", "m_playerId"),
                "slot": slot,
                "team": _first(player, "m_teamId"),
                "result": _first(player, "m_result"),
                "raw": player,
            })

    events: list[dict[str, Any]] = []
    if include_events:
        for member, method_name in (
            ("replay.tracker.events", "decode_replay_tracker_events"),
            ("replay.game.events", "decode_replay_game_events"),
            ("replay.message.events", "decode_replay_message_events"),
            ("replay.attributes.events", "decode_replay_attributes_events"),
        ):
            content = read_member(member)
            method = getattr(protocol, method_name, None)
            if content and method:
                try:
                    decoded_stream = method(content)
                    if member == "replay.attributes.events":
                        events.append({
                            "event_type": "ATTRIBUTE_EVENT",
                            "game_loop": None,
                            "game_seconds": None,
                            "raw_identifier": None,
                            "payload": _text(decoded_stream),
                        })
                    else:
                        events.extend(_decoded_event(event) for event in decoded_stream)
                except Exception as exc:
                    LOG.warning("Could not decode %s for %s: %s", member, path.name, exc)
                    warnings.append(f"{member} decode failed: {exc}")

    return DecodedReplay(
        base_build=base_build,
        data_build=data_build if isinstance(data_build, int) else None,
        map_name=map_name,
        map_version=str(_first(game_metadata, "Version", "MapVersion") or "") or None,
        duration_seconds=duration_seconds,
        metadata=metadata,
        players=players,
        events=events,
        warnings=warnings,
    )
