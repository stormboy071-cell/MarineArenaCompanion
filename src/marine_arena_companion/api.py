from __future__ import annotations

import json
import logging
import platform
import threading
from importlib.metadata import PackageNotFoundError, version
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from marine_arena_companion import __version__
from marine_arena_companion.config import ConfigStore, validate_battle_tag, validate_replay_folder
from marine_arena_companion.db.repository import Repository, SCHEMA_VERSION
from marine_arena_companion.overlay import OverlaySession
from marine_arena_companion.replay.scanner import ReplayScanner
from marine_arena_companion.replay.watcher import ReplayWatcher


LOG = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).parent / "web"


class CompanionHTTPServer:
    def __init__(self, host: str, port: int, config: ConfigStore, repository: Repository,
                 scanner: ReplayScanner, watcher: ReplayWatcher, overlay: OverlaySession | None = None):
        self.config = config
        self.repository = repository
        self.scanner = scanner
        self.watcher = watcher
        self.overlay = overlay or OverlaySession()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MarineArenaCompanion/0.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                LOG.info("HTTP %s - %s", self.address_string(), fmt % args)

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, value: Any) -> None:
                self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

            def _read_json(self) -> dict[str, Any] | None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 16_384:
                        raise ValueError("Request body must be smaller than 16 KB")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("Request body must be a JSON object")
                    return body
                except (ValueError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return None

            @staticmethod
            def _reminder_values(body: dict[str, Any]) -> dict[str, Any]:
                required = ("title", "at_seconds", "category")
                if any(key not in body for key in required):
                    raise ValueError("title, at_seconds, and category are required")
                if not isinstance(body["title"], str) or not isinstance(body["category"], str):
                    raise ValueError("title and category must be text")
                item_label = body.get("item_label", "")
                note = body.get("note", "")
                enabled = body.get("enabled", True)
                if not isinstance(item_label, str) or not isinstance(note, str):
                    raise ValueError("item_label and note must be text")
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be true or false")
                return {
                    "title": body["title"],
                    "at_seconds": body["at_seconds"],
                    "category": body["category"],
                    "item_label": item_label,
                    "note": note,
                    "enabled": enabled,
                }

            @staticmethod
            def _user_profile() -> dict[str, Any]:
                battle_tag = owner.config.get().battle_tag
                return {
                    "configured": bool(battle_tag),
                    "battle_tag": battle_tag,
                    "display_name": battle_tag.rsplit("#", 1)[0] if battle_tag else "",
                }

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/status":
                    watcher = owner.watcher.status()
                    scan = owner.scanner.status()
                    library = owner.repository.summary()
                    latest = library.get("latest")
                    self._json(200, {
                        "application_version": __version__,
                        "user_profile": self._user_profile(),
                        "watcher": {
                            "active": watcher["active"],
                            "folder": Path(str(watcher["folder"])).name if watcher["folder"] else "",
                            "error": bool(watcher["error"]),
                        },
                        "library": {
                            "total": library["total"],
                            "analyzed": library["analyzed"],
                            "failed": library["failed"],
                            "marine_arena": library["marine_arena"],
                            "players": library["players"],
                            "latest": {
                                "filename": latest["filename"],
                                "map_name": latest["map_name"],
                            } if latest else None,
                        },
                        "scan": {
                            "active": scan["active"],
                            "discovered": scan["discovered"],
                            "processed": scan["processed"],
                            "marine_arena": scan["marine_arena"],
                            "skipped": scan["skipped"],
                            "failed": scan["failed"],
                        },
                    })
                elif parsed.path == "/api/reminders":
                    self._json(200, {"items": owner.repository.list_reminders()})
                elif parsed.path == "/api/overlay/state":
                    self._json(200, {
                        "session": owner.overlay.status(),
                        "reminders": owner.repository.list_reminders(enabled_only=True),
                    })
                elif parsed.path == "/api/replays":
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["50"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                    except ValueError:
                        self._json(400, {"error": "limit and offset must be integers"})
                        return
                    search = query.get("q", [""])[0].strip()
                    status = query.get("status", [""])[0].strip()
                    classification = query.get("classification", [""])[0].strip()
                    if len(search) > 200:
                        self._json(400, {"error": "Search text must be 200 characters or fewer"})
                        return
                    if status and status not in {
                        "ANALYZED", "PROCESSING", "DECODE_FAILED",
                        "UNSUPPORTED_PROTOCOL", "NORMALIZATION_FAILED", "NEEDS_ATTENTION",
                    }:
                        self._json(400, {"error": "Unsupported replay status filter"})
                        return
                    if classification and classification not in {
                        "MARINE_ARENA", "MARINE_ARENA_EU", "MARINE_ARENA_OTHER",
                        "OTHER_MAP", "UNKNOWN",
                    }:
                        self._json(400, {"error": "Unsupported map classification filter"})
                        return
                    items = owner.repository.list_replays(
                        limit, offset, search=search, status=status, classification=classification,
                    )
                    self._json(200, {
                        "items": items,
                        "total": owner.repository.count_replays(
                            search=search, status=status, classification=classification,
                        ),
                    })
                elif parsed.path.startswith("/api/replays/"):
                    try:
                        replay_id = int(parsed.path.rsplit("/", 1)[1])
                    except ValueError:
                        self._json(400, {"error": "Replay id must be an integer"})
                        return
                    result = owner.repository.replay_summary(replay_id)
                    self._json(200 if result else 404, result or {"error": "Replay not found"})
                elif parsed.path == "/api/profile":
                    profile = self._user_profile()
                    if not profile["configured"]:
                        self._json(200, {
                            **profile,
                            **owner.repository.my_profile([]),
                        })
                        return
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["20"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                    except ValueError:
                        self._json(400, {"error": "limit and offset must be integers"})
                        return
                    search = query.get("q", [""])[0].strip()
                    classification = query.get("classification", [""])[0].strip()
                    result = query.get("result", [""])[0].strip()
                    if len(search) > 200:
                        self._json(400, {"error": "Search text must be 200 characters or fewer"})
                        return
                    player_ids = owner.repository.player_ids_for_battle_tag(profile["battle_tag"])
                    details = owner.repository.my_profile(
                        player_ids, limit, offset, search=search,
                        classification=classification, result=result,
                    )
                    self._json(200, {
                        **profile,
                        **details,
                        "display_name": profile["display_name"],
                    })
                elif parsed.path == "/api/opponents":
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["200"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                    except ValueError:
                        self._json(400, {"error": "limit and offset must be integers"})
                        return
                    search = query.get("q", [""])[0].strip()
                    if len(search) > 200:
                        self._json(400, {"error": "Search text must be 200 characters or fewer"})
                        return
                    items = owner.repository.opponents(
                        limit, offset, search=search, sort=query.get("sort", ["recent"])[0],
                        exclude_player_ids=set(owner.repository.player_ids_for_battle_tag(
                            owner.config.get().battle_tag,
                        )) if owner.config.get().battle_tag else None,
                    )
                    self._json(200, {
                        "items": items,
                        "total": owner.repository.count_opponents(
                            search=search,
                            exclude_player_ids=set(owner.repository.player_ids_for_battle_tag(
                                owner.config.get().battle_tag,
                            )) if owner.config.get().battle_tag else None,
                        ),
                    })
                elif parsed.path.startswith("/api/opponents/"):
                    try:
                        opponent_id = int(parsed.path.rsplit("/", 1)[1])
                    except ValueError:
                        self._json(400, {"error": "Opponent id must be an integer"})
                        return
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["100"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                    except ValueError:
                        self._json(400, {"error": "limit and offset must be integers"})
                        return
                    search = query.get("q", [""])[0].strip()
                    if len(search) > 200:
                        self._json(400, {"error": "Search text must be 200 characters or fewer"})
                        return
                    result = owner.repository.opponent_detail(
                        opponent_id, limit, offset,
                        search=search,
                        classification=query.get("classification", [""])[0],
                        result=query.get("result", [""])[0],
                    )
                    self._json(200 if result else 404, result or {"error": "Opponent not found"})
                elif parsed.path == "/api/diagnostics":
                    try:
                        s2protocol_version = version("s2protocol")
                    except PackageNotFoundError:
                        s2protocol_version = "unavailable"
                    semantics_path = owner.config.path.parent / "data" / "marine_arena_semantics.json"
                    try:
                        semantic_version = json.loads(semantics_path.read_text(encoding="utf-8")).get("version", "unknown")
                    except (OSError, json.JSONDecodeError):
                        semantic_version = "unavailable"
                    self._json(200, {
                        "about": {
                            "application_version": __version__,
                            "database_schema_version": SCHEMA_VERSION,
                            "python_runtime": platform.python_version(),
                            "s2protocol_version": s2protocol_version,
                            "semantic_dictionary_version": semantic_version,
                        },
                        **owner.repository.diagnostics(),
                    })
                elif parsed.path == "/api/health":
                    self._json(200, owner.repository.health_summary())
                elif parsed.path in ("/", "/dashboard"):
                    self._static("dashboard.html")
                elif parsed.path == "/overlay":
                    self._static("overlay.html")
                elif parsed.path.startswith("/assets/"):
                    self._static(parsed.path.removeprefix("/assets/"))
                else:
                    self._json(404, {"error": "Not found"})

            def _static(self, filename: str) -> None:
                target = (WEB_ROOT / filename).resolve()
                if WEB_ROOT.resolve() not in target.parents or not target.is_file():
                    self._json(404, {"error": "Not found"})
                    return
                content_types = {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                }
                content_type = content_types.get(target.suffix, "application/octet-stream")
                self._send(200, target.read_bytes(), content_type)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/profile":
                    body = self._read_json()
                    if body is None:
                        return
                    try:
                        battle_tag = validate_battle_tag(body.get("battle_tag"))
                        profile = owner.config.update(battle_tag=battle_tag)
                    except ValueError as exc:
                        self._json(400, {"error": str(exc)})
                        return
                    self._json(200, {
                        "configured": True,
                        "battle_tag": profile.battle_tag,
                        "display_name": profile.battle_tag.rsplit("#", 1)[0],
                    })
                elif parsed.path == "/api/reminders":
                    body = self._read_json()
                    if body is None:
                        return
                    try:
                        reminder_id = owner.repository.create_reminder(**self._reminder_values(body))
                    except ValueError as exc:
                        self._json(400, {"error": str(exc)})
                        return
                    self._json(201, {"id": reminder_id})
                elif parsed.path in ("/api/overlay/start", "/api/overlay/pause", "/api/overlay/reset"):
                    action = parsed.path.rsplit("/", 1)[1]
                    session = {
                        "start": owner.overlay.start,
                        "pause": owner.overlay.pause,
                        "reset": owner.overlay.reset,
                    }[action]()
                    self._json(200, {"session": session})
                elif parsed.path.startswith("/api/overlay/reminders/") and parsed.path.endswith("/dismiss"):
                    try:
                        reminder_id = int(parsed.path.split("/")[-2])
                    except ValueError:
                        self._json(400, {"error": "Reminder id must be an integer"})
                        return
                    reminder = next(
                        (item for item in owner.repository.list_reminders(enabled_only=True)
                         if item["id"] == reminder_id),
                        None,
                    )
                    if reminder is None:
                        self._json(404, {"error": "Enabled reminder not found"})
                        return
                    if int(owner.overlay.status()["elapsed_seconds"]) < reminder["at_seconds"]:
                        self._json(409, {"error": "This reminder is not due yet"})
                        return
                    self._json(200, {"session": owner.overlay.dismiss(reminder_id)})
                elif parsed.path in ("/api/scan", "/api/retry"):
                    folder = owner.config.get().replay_folder
                    try:
                        root = validate_replay_folder(folder)
                    except ValueError as exc:
                        self._json(400, {"error": str(exc)})
                        return
                    if parsed.path == "/api/retry":
                        count = owner.scanner.retry_failed()
                        self._json(202, {"accepted": count})
                    else:
                        started = owner.scanner.scan_library(root)
                        self._json(202 if started else 409, {"started": started, "scan": owner.scanner.status()})
                elif parsed.path == "/api/watcher/start":
                    folder = owner.config.get().replay_folder
                    try:
                        root = validate_replay_folder(folder)
                    except ValueError as exc:
                        owner.watcher.error = str(exc)
                        self._json(400, {"error": str(exc)})
                        return
                    owner.watcher.start(root)
                    owner.config.update(watcher_enabled=True)
                    self._json(200, owner.watcher.status())
                elif parsed.path == "/api/watcher/stop":
                    owner.watcher.stop()
                    owner.config.update(watcher_enabled=False)
                    self._json(200, owner.watcher.status())
                elif parsed.path == "/api/config/replay-folder":
                    try:
                        body = self._read_json()
                        if body is None:
                            return
                        root = validate_replay_folder(str(body["path"]))
                        owner.config.update(replay_folder=str(root), watcher_enabled=True)
                        owner.watcher.start(root)
                    except (ValueError, KeyError) as exc:
                        self._json(400, {"error": str(exc)})
                        return
                    self._json(200, owner.watcher.status())
                else:
                    self._json(404, {"error": "Not found"})

            def do_PUT(self) -> None:
                parsed = urlparse(self.path)
                try:
                    reminder_id = int(parsed.path.rsplit("/", 1)[1]) if parsed.path.startswith("/api/reminders/") else None
                except ValueError:
                    reminder_id = None
                if reminder_id is None:
                    self._json(404, {"error": "Not found"})
                    return
                body = self._read_json()
                if body is None:
                    return
                try:
                    values = self._reminder_values(body)
                    updated = owner.repository.update_reminder(reminder_id, **values)
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                if updated:
                    owner.overlay.remove_reminder(reminder_id)
                self._json(200 if updated else 404, {"updated": updated})

            def do_DELETE(self) -> None:
                parsed = urlparse(self.path)
                try:
                    reminder_id = int(parsed.path.rsplit("/", 1)[1]) if parsed.path.startswith("/api/reminders/") else None
                except ValueError:
                    reminder_id = None
                if reminder_id is None:
                    self._json(404, {"error": "Not found"})
                    return
                deleted = owner.repository.delete_reminder(reminder_id)
                if deleted:
                    owner.overlay.remove_reminder(reminder_id)
                self._json(200 if deleted else 404, {"deleted": deleted})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="local-api")

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> None:
        self.thread.start()
        LOG.info("Local dashboard/API listening at http://127.0.0.1:%s", self.port)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
