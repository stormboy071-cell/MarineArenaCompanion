from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from marine_arena_companion.analysis.identity import split_player_name
from marine_arena_companion.timing import MAX_ELAPSED_SECONDS

SCHEMA_VERSION = 5
REMINDER_CATEGORIES = {"unit_combo", "hero", "elite", "custom"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS replays (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    original_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    modified_time TEXT,
    imported_at TEXT NOT NULL,
    map_name TEXT,
    map_classification TEXT NOT NULL DEFAULT 'UNKNOWN',
    map_version TEXT,
    base_build INTEGER,
    data_build INTEGER,
    duration_seconds REAL,
    decode_status TEXT NOT NULL,
    decode_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS replay_files (
    path TEXT PRIMARY KEY COLLATE NOCASE,
    sha256 TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    modified_time TEXT,
    last_seen_at TEXT NOT NULL,
    replay_id INTEGER REFERENCES replays(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_aliases (
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(player_id, display_name)
);
CREATE TABLE IF NOT EXISTS replay_players (
    replay_id INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id),
    slot INTEGER,
    team INTEGER,
    result TEXT,
    user_id INTEGER,
    PRIMARY KEY (replay_id, player_id)
);
CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY,
    replay_id INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    game_loop INTEGER,
    game_seconds REAL,
    raw_identifier TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_failures (
    id INTEGER PRIMARY KEY,
    replay_id INTEGER REFERENCES replays(id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_mappings (
    category TEXT NOT NULL,
    raw_identifier TEXT NOT NULL,
    semantic_id TEXT,
    label TEXT,
    PRIMARY KEY(category, raw_identifier)
);
CREATE TABLE IF NOT EXISTS opponent_observations (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    replay_id INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    observed_type TEXT NOT NULL,
    game_seconds REAL,
    evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overlay_reminders (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    at_seconds INTEGER NOT NULL CHECK(at_seconds >= 0),
    category TEXT NOT NULL CHECK(category IN ('unit_combo','hero','elite','custom')),
    item_label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_replays_date ON replays(imported_at);
CREATE INDEX IF NOT EXISTS idx_replays_classification ON replays(map_classification, map_version);
CREATE INDEX IF NOT EXISTS idx_players_identity ON players(identity_key);
CREATE INDEX IF NOT EXISTS idx_raw_events_type_time ON raw_events(event_type, game_seconds);
CREATE INDEX IF NOT EXISTS idx_raw_events_replay ON raw_events(replay_id);
CREATE INDEX IF NOT EXISTS idx_failures_path ON scan_failures(path);
CREATE INDEX IF NOT EXISTS idx_overlay_reminders_enabled_time ON overlay_reminders(enabled,at_seconds,id);
CREATE INDEX IF NOT EXISTS idx_replay_files_replay ON replay_files(replay_id);
CREATE INDEX IF NOT EXISTS idx_replay_players_player_replay ON replay_players(player_id,replay_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=lambda item: item.decode("latin-1") if isinstance(item, bytes) else str(item))


class Repository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema {version} is newer than this application supports ({SCHEMA_VERSION})")
            if version == 0:
                conn.execute("BEGIN IMMEDIATE")
                legacy_schema = self._rename_legacy_tables(conn)
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                if legacy_schema:
                    self._migrate_legacy_rows(conn)
                self._seed_replay_file_index(conn)
                self._backfill_player_user_ids(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version < SCHEMA_VERSION:
                conn.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA.split(";"):
                    if any(name in statement for name in (
                        "overlay_reminders", "idx_overlay_reminders", "replay_files", "idx_replay_files",
                        "idx_replay_players_player_replay",
                    )):
                        if statement.strip():
                            conn.execute(statement)
                self._seed_replay_file_index(conn)
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(replay_players)")
                }
                if "user_id" not in columns:
                    conn.execute("ALTER TABLE replay_players ADD COLUMN user_id INTEGER")
                self._backfill_player_user_ids(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _seed_replay_file_index(conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO replay_files
               (path,sha256,file_size,modified_time,last_seen_at,replay_id)
               SELECT original_path,sha256,file_size,modified_time,imported_at,id
               FROM replays WHERE original_path != ''"""
        )

    @staticmethod
    def _backfill_player_user_ids(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """SELECT rp.replay_id,rp.slot,r.metadata_json
               FROM replay_players rp JOIN replays r ON r.id=rp.replay_id
               WHERE rp.user_id IS NULL"""
        ).fetchall()
        for row in rows:
            try:
                details = json.loads(row["metadata_json"]).get("details", {})
                players = details.get("m_playerList", [])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(players, list):
                continue
            slot = row["slot"]
            if not isinstance(slot, int) or slot < 1 or slot > len(players):
                continue
            player = players[slot - 1]
            if not isinstance(player, dict):
                continue
            user_id = player.get("m_userId", player.get("m_playerId"))
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                continue
            conn.execute(
                "UPDATE replay_players SET user_id=? WHERE replay_id=? AND slot=?",
                (user_id, row["replay_id"], slot),
            )

    @staticmethod
    def _rename_legacy_tables(conn: sqlite3.Connection) -> bool:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "replays" not in tables:
            return False
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(replays)")
        }
        if "original_path" in columns:
            return False
        if not {"id", "sha256", "path"}.issubset(columns):
            raise RuntimeError("Unrecognized version-0 replay database schema; database was not changed")

        legacy_names = ("replays", "players", "events")
        for name in legacy_names:
            if name in tables:
                archived_name = f"legacy_v0_{name}"
                if archived_name in tables:
                    raise RuntimeError(f"Cannot safely migrate database: {archived_name} already exists")
                conn.execute(f'ALTER TABLE "{name}" RENAME TO "{archived_name}"')
        return True

    def _migrate_legacy_rows(self, conn: sqlite3.Connection) -> None:
        replay_ids: dict[int, int] = {}
        legacy_replays = conn.execute("SELECT * FROM legacy_v0_replays").fetchall()
        for replay in legacy_replays:
            digest = replay["sha256"]
            if not digest:
                continue
            path = str(replay["path"] or "")
            replay_path = Path(path)
            try:
                stat = replay_path.stat()
                file_size = stat.st_size
                modified_time = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            except OSError:
                file_size = 0
                modified_time = None
            imported_at = replay["analyzed_at"] or self._now()
            legacy_status = str(replay["status"] or "").upper()
            if legacy_status in {"ANALYZED", "DECODE_FAILED", "UNSUPPORTED_PROTOCOL", "NORMALIZATION_FAILED", "PROCESSING"}:
                status = legacy_status
            elif legacy_status in {"COMPLETE", "COMPLETED", "SUCCESS", "DONE"}:
                status = "ANALYZED"
            else:
                status = "DECODE_FAILED"
            metadata = {
                "legacy_schema_version": 0,
                "legacy_status": replay["status"],
                "legacy_error": replay["error"],
            }
            conn.execute(
                """INSERT OR IGNORE INTO replays
                   (id,sha256,original_path,filename,file_size,modified_time,imported_at,
                    map_name,base_build,data_build,decode_status,decode_error,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    replay["id"], digest, path, replay_path.name or "Unknown.SC2Replay",
                    file_size, modified_time, imported_at, replay["map_name"],
                    replay["build"], replay["build"], status, replay["error"], _json(metadata),
                ),
            )
            migrated = conn.execute("SELECT id FROM replays WHERE sha256=?", (digest,)).fetchone()
            replay_ids[replay["id"]] = int(migrated["id"])

        if "legacy_v0_players" in {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            for player in conn.execute("SELECT * FROM legacy_v0_players").fetchall():
                replay_id = replay_ids.get(player["replay_id"])
                if replay_id is None:
                    continue
                name = str(player["name"] or "Unknown").strip() or "Unknown"
                toon_id = player["toon_id"]
                identity_key = f"legacy-toon:{toon_id}" if toon_id else f"legacy:{replay_id}:{player['id']}"
                conn.execute(
                    """INSERT INTO players(identity_key,display_name) VALUES(?,?)
                       ON CONFLICT(identity_key) DO UPDATE SET display_name=excluded.display_name""",
                    (identity_key, name),
                )
                player_id = int(conn.execute(
                    "SELECT id FROM players WHERE identity_key=?", (identity_key,)
                ).fetchone()["id"])
                imported_at = conn.execute(
                    "SELECT imported_at FROM replays WHERE id=?", (replay_id,)
                ).fetchone()["imported_at"]
                conn.execute(
                    """INSERT OR IGNORE INTO player_aliases
                       (player_id,display_name,first_seen_at,last_seen_at) VALUES(?,?,?,?)""",
                    (player_id, name, imported_at, imported_at),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO replay_players(replay_id,player_id,team,result)
                       VALUES(?,?,?,?)""",
                    (replay_id, player_id, player["team_id"], player["result"]),
                )

        if "legacy_v0_events" in {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            for event in conn.execute("SELECT * FROM legacy_v0_events").fetchall():
                replay_id = replay_ids.get(event["replay_id"])
                if replay_id is None:
                    continue
                try:
                    payload = json.loads(event["payload"]) if event["payload"] else {}
                except (TypeError, json.JSONDecodeError):
                    payload = {"legacy_payload": event["payload"]}
                if not isinstance(payload, dict):
                    payload = {"legacy_payload": payload}
                payload["_legacy_player_id"] = event["player_id"]
                try:
                    game_loop = int(event["gameloop"]) if event["gameloop"] is not None else None
                except (TypeError, ValueError):
                    game_loop = None
                conn.execute(
                    """INSERT INTO raw_events
                       (replay_id,event_type,game_loop,game_seconds,payload_json)
                       VALUES(?,?,?,?,?)""",
                    (
                        replay_id, str(event["kind"] or "UNKNOWN"), game_loop,
                        game_loop / 22.4 if game_loop is not None else None, _json(payload),
                    ),
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def replay_file_states(self) -> dict[str, tuple[int, str | None, str]]:
        with self.connection() as conn:
            return {
                row["path"].casefold(): (row["file_size"], row["modified_time"], row["decode_status"])
                for row in conn.execute(
                    """SELECT f.path,f.file_size,f.modified_time,r.decode_status
                       FROM replay_files f LEFT JOIN replays r ON r.id=f.replay_id"""
                )
            }

    def record_replay_file(self, path: Path, sha256: str, file_size: int,
                           modified_time: str | None, replay_id: int | None) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO replay_files(path,sha256,file_size,modified_time,last_seen_at,replay_id)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,
                   file_size=excluded.file_size,modified_time=excluded.modified_time,
                   last_seen_at=excluded.last_seen_at,replay_id=excluded.replay_id""",
                (str(path.resolve()), sha256, file_size, modified_time, self._now(), replay_id),
            )

    def replay_id_for_hash(self, sha256: str) -> int | None:
        with self.connection() as conn:
            row = conn.execute("SELECT id FROM replays WHERE sha256=?", (sha256,)).fetchone()
            return int(row["id"]) if row else None

    def create_replay(self, path: Path, sha256: str, file_size: int, modified_time: str | None) -> int | None:
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO replays
                   (sha256, original_path, filename, file_size, modified_time, imported_at, decode_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'PROCESSING')""",
                (sha256, str(path), path.name, file_size, modified_time, self._now()),
            )
            if cursor.rowcount == 0:
                existing = conn.execute("SELECT id,decode_status FROM replays WHERE sha256=?", (sha256,)).fetchone()
                if existing and existing["decode_status"] in ("DECODE_FAILED", "UNSUPPORTED_PROTOCOL", "NORMALIZATION_FAILED"):
                    conn.execute(
                        """UPDATE replays SET original_path=?,filename=?,file_size=?,modified_time=?,
                           decode_status='PROCESSING',decode_error=NULL WHERE id=?""",
                        (str(path), path.name, file_size, modified_time, existing["id"]),
                    )
                    conn.execute("DELETE FROM raw_events WHERE replay_id=?", (existing["id"],))
                    conn.execute("DELETE FROM replay_players WHERE replay_id=?", (existing["id"],))
                    return int(existing["id"])
                return None
            return int(cursor.lastrowid)

    def update_replay(self, replay_id: int, *, status: str, error: str | None = None,
                      map_name: str | None = None, classification: str = "UNKNOWN",
                      map_version: str | None = None, base_build: int | None = None,
                      data_build: int | None = None, duration_seconds: float | None = None,
                      metadata: dict[str, Any] | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE replays SET decode_status=?, decode_error=?, map_name=?,
                   map_classification=?, map_version=?, base_build=?, data_build=?,
                   duration_seconds=?, metadata_json=? WHERE id=?""",
                (status, error, map_name, classification, map_version, base_build,
                 data_build, duration_seconds, _json(metadata or {}), replay_id),
            )

    def add_failure(self, replay_id: int | None, path: str, stage: str, reason: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO scan_failures(replay_id,path,stage,reason,occurred_at) VALUES(?,?,?,?,?)",
                (replay_id, path, stage, reason[:2000], self._now()),
            )

    def add_player(self, replay_id: int, identity_key: str, name: str, slot: int | None,
                   team: int | None, result: str | None, user_id: int | None = None) -> int:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO players(identity_key,display_name) VALUES(?,?)
                   ON CONFLICT(identity_key) DO UPDATE SET display_name=excluded.display_name""",
                (identity_key, name),
            )
            player_id = int(conn.execute("SELECT id FROM players WHERE identity_key=?", (identity_key,)).fetchone()["id"])
            now = self._now()
            conn.execute(
                """INSERT INTO player_aliases(player_id,display_name,first_seen_at,last_seen_at)
                   VALUES(?,?,?,?) ON CONFLICT(player_id,display_name)
                   DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (player_id, name, now, now),
            )
            conn.execute(
                """INSERT OR REPLACE INTO replay_players
                   (replay_id,player_id,slot,team,result,user_id) VALUES(?,?,?,?,?,?)""",
                (replay_id, player_id, slot, team, result, user_id),
            )
            return player_id

    def add_events(self, replay_id: int, events: list[dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.executemany(
                """INSERT INTO raw_events(replay_id,event_type,game_loop,game_seconds,raw_identifier,payload_json)
                   VALUES(?,?,?,?,?,?)""",
                [(replay_id, event["event_type"], event.get("game_loop"), event.get("game_seconds"),
                  event.get("raw_identifier"), _json({
                      **event["payload"],
                      "_companion": {
                          "semantic_category": event.get("semantic_category"),
                          "semantic_mapping": event.get("semantic_mapping"),
                      },
                  })) for event in events],
            )

    def save_semantic_mappings(self, entries: list[tuple[str, str, str | None, str | None]]) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM semantic_mappings")
            conn.executemany(
                "INSERT INTO semantic_mappings(category,raw_identifier,semantic_id,label) VALUES(?,?,?,?)",
                entries,
            )

    @staticmethod
    def _validate_reminder(title: str, at_seconds: int, category: str, item_label: str, note: str) -> None:
        if not title.strip() or len(title.strip()) > 100:
            raise ValueError("Reminder text must contain between 1 and 100 characters")
        if (
            isinstance(at_seconds, bool)
            or not isinstance(at_seconds, int)
            or not 0 <= at_seconds <= MAX_ELAPSED_SECONDS
        ):
            raise ValueError("Reminder time must be a non-negative elapsed match duration")
        if category not in REMINDER_CATEGORIES:
            raise ValueError("Reminder category must be a unit combo, hero, elite, or custom")
        if len(item_label.strip()) > 100:
            raise ValueError("Unit, hero, or elite label must be 100 characters or fewer")
        if len(note.strip()) > 500:
            raise ValueError("Reminder notes must be 500 characters or fewer")

    def list_reminders(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.connection() as conn:
            where = " WHERE enabled=1" if enabled_only else ""
            return [dict(row) for row in conn.execute(
                """SELECT id,title,at_seconds,category,item_label,note,enabled,created_at,updated_at
                   FROM overlay_reminders""" + where + " ORDER BY at_seconds,id"
            )]

    def create_reminder(self, *, title: str, at_seconds: int, category: str,
                        item_label: str = "", note: str = "", enabled: bool = True) -> int:
        self._validate_reminder(title, at_seconds, category, item_label, note)
        now = self._now()
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO overlay_reminders
                   (title,at_seconds,category,item_label,note,enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (title.strip(), at_seconds, category, item_label.strip(), note.strip(), int(enabled), now, now),
            )
            return int(cursor.lastrowid)

    def update_reminder(self, reminder_id: int, *, title: str, at_seconds: int, category: str,
                        item_label: str = "", note: str = "", enabled: bool = True) -> bool:
        self._validate_reminder(title, at_seconds, category, item_label, note)
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE overlay_reminders SET title=?,at_seconds=?,category=?,item_label=?,
                   note=?,enabled=?,updated_at=? WHERE id=?""",
                (title.strip(), at_seconds, category, item_label.strip(), note.strip(),
                 int(enabled), self._now(), reminder_id),
            )
            return cursor.rowcount == 1

    def delete_reminder(self, reminder_id: int) -> bool:
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM overlay_reminders WHERE id=?", (reminder_id,))
            return cursor.rowcount == 1

    def summary(self) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute("SELECT decode_status, map_classification, COUNT(*) n FROM replays GROUP BY 1,2").fetchall()
            total = conn.execute("SELECT COUNT(*) FROM replays").fetchone()[0]
            failed = conn.execute(
                """SELECT COUNT(*) FROM (
                     SELECT DISTINCT sf.path FROM scan_failures sf
                     LEFT JOIN replays r ON r.original_path=sf.path
                     WHERE r.id IS NULL OR r.decode_status IN ('DECODE_FAILED','UNSUPPORTED_PROTOCOL','NORMALIZATION_FAILED')
                   )"""
            ).fetchone()[0]
            last = conn.execute("SELECT filename,map_name,decode_status,imported_at FROM replays ORDER BY imported_at DESC LIMIT 1").fetchone()
            player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            counts = {row["decode_status"]: row["n"] for row in rows}
            maps = {row["map_classification"]: row["n"] for row in rows}
            return {
                "total": total, "analyzed": counts.get("ANALYZED", 0), "failed": failed,
                "processing": counts.get("PROCESSING", 0), "marine_arena": maps.get("MARINE_ARENA_EU", 0) + maps.get("MARINE_ARENA_OTHER", 0),
                "players": player_count,
                "classifications": maps, "statuses": counts,
                "latest": dict(last) if last else None, "schema_version": SCHEMA_VERSION,
            }

    @staticmethod
    def _like_pattern(search: str) -> str:
        return f"%{search.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"

    @staticmethod
    def _replay_filters(search: str, status: str, classification: str) -> tuple[str, list[Any]]:
        clauses = []
        params: list[Any] = []
        if search:
            pattern = Repository._like_pattern(search)
            clauses.append("(filename LIKE ? ESCAPE '!' OR map_name LIKE ? ESCAPE '!')")
            params.extend((pattern, pattern))
        if status:
            if status == "NEEDS_ATTENTION":
                clauses.append("decode_status IN ('DECODE_FAILED','UNSUPPORTED_PROTOCOL','NORMALIZATION_FAILED')")
            else:
                if status not in {
                    "ANALYZED", "PROCESSING", "DECODE_FAILED",
                    "UNSUPPORTED_PROTOCOL", "NORMALIZATION_FAILED",
                }:
                    raise ValueError("Unsupported replay status filter")
                clauses.append("decode_status=?")
                params.append(status)
        if classification:
            if classification == "MARINE_ARENA":
                clauses.append("map_classification IN ('MARINE_ARENA_EU','MARINE_ARENA_OTHER')")
            else:
                if classification not in {
                    "MARINE_ARENA_EU", "MARINE_ARENA_OTHER", "OTHER_MAP", "UNKNOWN",
                }:
                    raise ValueError("Unsupported map classification filter")
                clauses.append("map_classification=?")
                params.append(classification)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    def list_replays(self, limit: int = 50, offset: int = 0, *,
                     search: str = "", status: str = "", classification: str = "") -> list[dict[str, Any]]:
        where, params = self._replay_filters(search, status, classification)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,filename,map_name,map_classification,duration_seconds,decode_status,imported_at FROM replays"
                + where + " ORDER BY imported_at DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(limit, 200)), max(offset, 0)),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_replays(self, *, search: str = "", status: str = "", classification: str = "") -> int:
        where, params = self._replay_filters(search, status, classification)
        with self.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM replays" + where, params).fetchone()[0])

    def replay_detail(self, replay_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM replays WHERE id=?", (replay_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["metadata"] = json.loads(result.pop("metadata_json"))
            result["players"] = [dict(p) for p in conn.execute(
                """SELECT p.identity_key,p.display_name,rp.slot,rp.team,rp.result
                   FROM replay_players rp JOIN players p ON p.id=rp.player_id WHERE rp.replay_id=?""", (replay_id,)
            )]
            result["events"] = [dict(e) for e in conn.execute(
                "SELECT event_type,game_loop,game_seconds,raw_identifier,payload_json FROM raw_events WHERE replay_id=? ORDER BY id LIMIT 2000",
                (replay_id,),
            )]
            return result

    def replay_summary(self, replay_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT id,filename,map_name,map_classification,duration_seconds,
                          decode_status,imported_at
                   FROM replays WHERE id=?""",
                (replay_id,),
            ).fetchone()
            if row is None:
                return None

            players = conn.execute(
                """SELECT p.id player_id,p.display_name,rp.result,rp.slot,rp.team,rp.user_id FROM replay_players rp
                   JOIN players p ON p.id=rp.player_id WHERE rp.replay_id=?
                   ORDER BY rp.slot,p.display_name COLLATE NOCASE""",
                (replay_id,),
            ).fetchall()
            normalized_players = []
            for player in players:
                clan_tag, display_name = split_player_name(player["display_name"])
                normalized_players.append({
                    "player_id": int(player["player_id"]),
                    "display_name": display_name,
                    "clan_tag": clan_tag,
                    "result": player["result"],
                    "slot": player["slot"],
                    "team": player["team"],
                })
            activity = {
                "units_created": 0,
                "units_destroyed": 0,
                "upgrades_completed": 0,
                "abilities_used": 0,
            }
            for event in conn.execute(
                "SELECT event_type,COUNT(*) count FROM raw_events WHERE replay_id=? GROUP BY event_type",
                (replay_id,),
            ):
                event_type = event["event_type"].casefold()
                if "sunitbornevent" in event_type:
                    activity["units_created"] += event["count"]
                elif "sunitdiedevent" in event_type:
                    activity["units_destroyed"] += event["count"]
                elif "supgradeevent" in event_type:
                    activity["upgrades_completed"] += event["count"]
                elif any(token in event_type for token in ("ability", "command", "scmd")):
                    activity["abilities_used"] += event["count"]

            return {
                **dict(row),
                "players": normalized_players,
                "activity": activity,
                "player_reports": self._replay_player_reports(
                    conn, replay_id, players, row["duration_seconds"],
                ),
            }

    @staticmethod
    def _replay_player_reports(
        conn: sqlite3.Connection,
        replay_id: int,
        participants: list[sqlite3.Row],
        duration_seconds: float | None,
    ) -> list[dict[str, Any]]:
        players_by_slot = {
            int(player["slot"]): int(player["player_id"])
            for player in participants if player["slot"] is not None
        }
        players_by_user = {
            int(player["user_id"]): int(player["player_id"])
            for player in participants if player["user_id"] is not None
        }
        reports = {
            int(player["player_id"]): {
                "player_id": int(player["player_id"]),
                "performance": {
                    "actions_per_minute": None,
                    "recorded_actions": 0,
                    "average_unspent_resources": None,
                    "time_supply_capped_seconds": None,
                    "workers_created": None,
                },
                "score_summary": {
                    "resources_on_hand": None,
                    "army_investment": None,
                    "economy_investment": None,
                    "technology_investment": None,
                },
                "units": {
                    "trained": None,
                    "units_lost": None,
                    "units_killed": None,
                    "structures_built": None,
                    "structures_razed": None,
                    "top_units": [],
                    "top_upgrades": [],
                },
                "build_order": [],
                "graphs": {
                    "army_value": [],
                    "resource_collection_rate": [],
                    "upgrade_spending": [],
                    "workers_active": [],
                },
                "samples": {"stats": 0, "unit_events": 0},
            }
            for player in participants
        }
        if not reports:
            return []

        rows = conn.execute(
            """SELECT event_type,game_seconds,raw_identifier,payload_json FROM raw_events
               WHERE replay_id=? ORDER BY id""",
            (replay_id,),
        )
        action_counts: dict[int, int] = {}
        snapshots: dict[int, list[tuple[float, dict[str, Any]]]] = {}
        unit_owners: dict[tuple[str, str], int] = {}
        unit_types: dict[tuple[str, str], str] = {}
        trained_counts: dict[int, dict[str, int]] = {}
        upgrade_counts: dict[int, dict[str, int]] = {}
        lost_counts: dict[int, dict[str, int]] = {}
        killed_counts: dict[int, int] = {}
        structures_built: dict[int, int] = {}
        structures_razed: dict[int, int] = {}
        workers_created: dict[int, int] = {}
        build_order: dict[int, list[dict[str, Any]]] = {}
        unit_event_samples: dict[int, int] = {}

        def number(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def unit_label(identifier: Any) -> str:
            return Repository._readable_identifier(str(identifier or ""), "unit")

        for row in rows:
            event_type = str(row["event_type"]).casefold()
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            seconds = number(row["game_seconds"])
            if seconds is None:
                seconds = 0.0
            if "splayerstatsevent" in event_type:
                try:
                    player_id = players_by_slot.get(int(payload.get("m_playerId")))
                except (TypeError, ValueError):
                    player_id = None
                stats = payload.get("m_stats")
                if player_id in reports and isinstance(stats, dict):
                    snapshots.setdefault(player_id, []).append((seconds, stats))
                continue

            if any(token in event_type for token in (
                "scmdevent", "sselectiondeltaevent", "scontrolgroupupdateevent",
            )):
                try:
                    player_id = players_by_user.get(int(payload.get("_userid")))
                except (TypeError, ValueError):
                    player_id = None
                if player_id in reports:
                    action_counts[player_id] = action_counts.get(player_id, 0) + 1
                continue

            if not any(token in event_type for token in (
                "sunitbornevent", "sunitinitevent", "sunitdoneevent",
                "sunitdiedevent", "sunitownerchangeevent", "supgradeevent",
            )):
                continue

            if "supgradeevent" in event_type:
                try:
                    player_id = players_by_slot.get(int(payload.get("m_playerId")))
                except (TypeError, ValueError):
                    player_id = None
                if player_id in reports:
                    identifier = Repository._readable_identifier(
                        str(payload.get("m_upgradeTypeName") or row["raw_identifier"] or ""),
                        "upgrade",
                    )
                    counts = upgrade_counts.setdefault(player_id, {})
                    counts[identifier] = counts.get(identifier, 0) + 1
                    build_order.setdefault(player_id, []).append({
                        "time_seconds": seconds,
                        "category": "Upgrade",
                        "item": identifier,
                        "supply": None,
                    })
                continue

            tag_index = payload.get("m_unitTagIndex")
            if tag_index is None:
                continue
            tag = (str(tag_index), str(payload.get("m_unitTagRecycle", 0)))
            if "sunitownerchangeevent" in event_type:
                try:
                    new_owner = players_by_slot.get(int(payload.get("m_controlPlayerId")))
                except (TypeError, ValueError):
                    new_owner = None
                if new_owner is not None:
                    unit_owners[tag] = new_owner
                continue

            if "sunitdiedevent" in event_type:
                victim_id = unit_owners.get(tag)
                try:
                    killer_id = players_by_slot.get(int(payload.get("m_killerPlayerId")))
                except (TypeError, ValueError):
                    killer_id = None
                if victim_id in reports:
                    identifier = unit_types.get(tag)
                    label = unit_label(identifier)
                    if Repository._is_structure_identifier(label):
                        structures_razed[victim_id] = structures_razed.get(victim_id, 0) + 1
                    else:
                        lost_counts.setdefault(victim_id, {})[label] = (
                            lost_counts.setdefault(victim_id, {}).get(label, 0) + 1
                        )
                if killer_id in reports and killer_id != victim_id:
                    identifier = unit_types.get(tag, "")
                    if not Repository._is_structure_identifier(identifier):
                        killed_counts[killer_id] = killed_counts.get(killer_id, 0) + 1
                continue

            try:
                player_id = players_by_slot.get(int(payload.get("m_controlPlayerId")))
            except (TypeError, ValueError):
                player_id = None
            if player_id not in reports:
                continue
            identifier = payload.get("m_unitTypeName") or row["raw_identifier"]
            label = unit_label(identifier)
            unit_owners[tag] = player_id
            unit_types[tag] = label
            unit_event_samples[player_id] = unit_event_samples.get(player_id, 0) + 1

            is_init = "sunitinitevent" in event_type
            if is_init or "sunitbornevent" in event_type:
                if Repository._is_structure_identifier(label):
                    structures_built[player_id] = structures_built.get(player_id, 0) + 1
                elif not is_init:
                    counts = trained_counts.setdefault(player_id, {})
                    counts[label] = counts.get(label, 0) + 1
                    if re.match(r"^(scv|drone|probe)(?:\b|\s)", label, re.IGNORECASE):
                        workers_created[player_id] = workers_created.get(player_id, 0) + 1
                build_order.setdefault(player_id, []).append({
                    "time_seconds": seconds,
                    "category": "Structure" if Repository._is_structure_identifier(label) else "Unit",
                    "item": label,
                    "supply": None,
                })

        def graph_samples(points: list[dict[str, Any]]) -> list[dict[str, float]]:
            if len(points) <= 80:
                return points
            step = (len(points) - 1) / 79
            return [points[round(index * step)] for index in range(80)]

        def score_total(stats: dict[str, Any], *fields: str) -> float | None:
            values = [number(stats.get(field)) for field in fields]
            present = [value for value in values if value is not None]
            return sum(present) if present else None

        for player_id, report in reports.items():
            action_count = action_counts.get(player_id, 0)
            report["performance"]["recorded_actions"] = action_count
            if duration_seconds and duration_seconds > 0 and action_count:
                report["performance"]["actions_per_minute"] = round(
                    action_count * 60 / duration_seconds, 1,
                )
            samples = snapshots.get(player_id, [])
            report["samples"]["stats"] = len(samples)
            if samples:
                resources = []
                capped_seconds = 0.0
                graph_values: dict[str, list[dict[str, float]]] = {
                    "army_value": [],
                    "resource_collection_rate": [],
                    "upgrade_spending": [],
                    "workers_active": [],
                }
                final = samples[-1][1]
                for index, (seconds, stats) in enumerate(samples):
                    minerals = number(stats.get("m_scoreValueMineralsCurrent"))
                    gas = number(stats.get("m_scoreValueVespeneCurrent"))
                    if minerals is not None or gas is not None:
                        resources.append((minerals or 0) + (gas or 0))
                    army = score_total(
                        stats, "m_scoreValueMineralsUsedCurrentArmy",
                        "m_scoreValueVespeneUsedCurrentArmy",
                    )
                    rate = score_total(
                        stats, "m_scoreValueMineralsCollectionRate",
                        "m_scoreValueVespeneCollectionRate",
                    )
                    tech = score_total(
                        stats, "m_scoreValueMineralsUsedCurrentTechnology",
                        "m_scoreValueVespeneUsedCurrentTechnology",
                    )
                    workers = number(stats.get("m_scoreValueWorkersActiveCount"))
                    for key, value in (
                        ("army_value", army), ("resource_collection_rate", rate),
                        ("upgrade_spending", tech),
                    ):
                        if value is not None:
                            graph_values[key].append({"time": seconds, "value": value})
                    if workers is not None:
                        graph_values["workers_active"].append({"time": seconds, "value": workers})
                    if index + 1 < len(samples):
                        next_seconds = samples[index + 1][0]
                    else:
                        next_seconds = float(duration_seconds or seconds)
                    food_used = number(stats.get("m_scoreValueFoodUsed"))
                    food_made = number(stats.get("m_scoreValueFoodMade"))
                    if food_used is not None and food_made and food_used >= food_made:
                        capped_seconds += max(0.0, next_seconds - seconds)
                if resources:
                    report["performance"]["average_unspent_resources"] = round(
                        sum(resources) / len(resources),
                    )
                if any(
                    number(stats.get("m_scoreValueFoodUsed")) is not None
                    and number(stats.get("m_scoreValueFoodMade")) is not None
                    for _, stats in samples
                ):
                    report["performance"]["time_supply_capped_seconds"] = round(capped_seconds)
                score_fields = {
                    "resources_on_hand": (
                        "m_scoreValueMineralsCurrent", "m_scoreValueVespeneCurrent",
                    ),
                    "army_investment": (
                        "m_scoreValueMineralsUsedCurrentArmy",
                        "m_scoreValueVespeneUsedCurrentArmy",
                    ),
                    "economy_investment": (
                        "m_scoreValueMineralsUsedCurrentEconomy",
                        "m_scoreValueVespeneUsedCurrentEconomy",
                    ),
                    "technology_investment": (
                        "m_scoreValueMineralsUsedCurrentTechnology",
                        "m_scoreValueVespeneUsedCurrentTechnology",
                    ),
                }
                report["score_summary"] = {
                    key: (round(value) if value is not None else None)
                    for key, fields in score_fields.items()
                    if (value := score_total(final, *fields)) is not None
                }
                report["graphs"] = {
                    key: graph_samples(points) for key, points in graph_values.items()
                }
            report["performance"]["workers_created"] = workers_created.get(player_id)
            report["units"] = {
                "trained": sum(trained_counts.get(player_id, {}).values()) if player_id in trained_counts else None,
                "units_lost": sum(lost_counts.get(player_id, {}).values()) if player_id in lost_counts else None,
                "units_killed": killed_counts.get(player_id),
                "structures_built": structures_built.get(player_id),
                "structures_razed": structures_razed.get(player_id),
                "top_units": [
                    {"name": name, "count": count}
                    for name, count in sorted(
                        trained_counts.get(player_id, {}).items(),
                        key=lambda item: (-item[1], item[0].casefold()),
                    )[:10]
                ],
                "top_upgrades": [
                    {"name": name, "count": count}
                    for name, count in sorted(
                        upgrade_counts.get(player_id, {}).items(),
                        key=lambda item: (-item[1], item[0].casefold()),
                    )[:10]
                ],
            }
            report["samples"]["unit_events"] = unit_event_samples.get(player_id, 0)
            for item in build_order.get(player_id, []):
                prior = [
                    (seconds, stats) for seconds, stats in snapshots.get(player_id, [])
                    if seconds <= item["time_seconds"]
                ]
                if prior:
                    item["supply"] = round(
                        number(prior[-1][1].get("m_scoreValueFoodUsed")) or 0,
                    )
            report["build_order"] = sorted(
                build_order.get(player_id, []),
                key=lambda item: item["time_seconds"],
            )[:150]
        return list(reports.values())

    def player_ids_for_battle_tag(self, battle_tag: str) -> list[int]:
        configured_tag = battle_tag.strip().casefold()
        configured_name = configured_tag.rsplit("#", 1)[0]
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT p.id,a.display_name
                   FROM players p LEFT JOIN player_aliases a ON a.player_id=p.id"""
            ).fetchall()
        matches = []
        for row in rows:
            _clan_tag, display_name = split_player_name(row["display_name"])
            replay_name = display_name.strip().casefold()
            if "#" in replay_name:
                matches_name = replay_name == configured_tag
            else:
                matches_name = replay_name == configured_name
            if matches_name:
                matches.append(int(row["id"]))
        return sorted(set(matches))

    def opponents(self, limit: int = 200, offset: int = 0, *, search: str = "", sort: str = "",
                  exclude_player_ids: set[int] | None = None) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if search:
            pattern = self._like_pattern(search)
            clauses.append("p.display_name LIKE ? ESCAPE '!'")
            params.append(pattern)
        if exclude_player_ids:
            clauses.append("p.id NOT IN (" + ",".join("?" for _ in exclude_player_ids) + ")")
            params.extend(sorted(exclude_player_ids))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        ordering = {
            "recent": "latest_encounter DESC",
            "games": "games DESC, latest_encounter DESC",
            "name": "p.display_name COLLATE NOCASE ASC",
        }.get(sort, "latest_encounter DESC")
        with self.connection() as conn:
            players = [dict(row) for row in conn.execute(
                """SELECT p.id,p.display_name,COUNT(rp.replay_id) games,
                   MAX(r.imported_at) latest_encounter FROM players p
                   JOIN replay_players rp ON rp.player_id=p.id JOIN replays r ON r.id=rp.replay_id
                   """ + where + f" GROUP BY p.id ORDER BY {ordering} LIMIT ? OFFSET ?",
                (*params, max(1, min(limit, 200)), max(offset, 0)),
            )]
            for player in players:
                player["clan_tag"], player["display_name"] = split_player_name(player["display_name"])
            return players

    def count_opponents(self, *, search: str = "", exclude_player_ids: set[int] | None = None) -> int:
        clauses = ["EXISTS (SELECT 1 FROM replay_players rp WHERE rp.player_id=p.id)"]
        params: list[Any] = []
        if search:
            clauses.append("p.display_name LIKE ? ESCAPE '!'")
            params.append(self._like_pattern(search))
        if exclude_player_ids:
            clauses.append("p.id NOT IN (" + ",".join("?" for _ in exclude_player_ids) + ")")
            params.extend(sorted(exclude_player_ids))
        with self.connection() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM players p WHERE " + " AND ".join(clauses),
                params,
            ).fetchone()[0])

    def my_profile(self, player_ids: list[int], limit: int = 100, offset: int = 0, *,
                   search: str = "", classification: str = "", result: str = "") -> dict[str, Any]:
        ids = sorted(set(player_ids))
        if not ids:
            return {
                "player_ids": [],
                "display_name": "",
                "clan_tag": None,
                "matches": [],
                "matches_total": 0,
                "all_time_games": 0,
                "name_history": [],
                "profile": self._empty_player_profile(),
            }
        placeholders = ",".join("?" for _ in ids)
        clauses = [f"rp.player_id IN ({placeholders})"]
        params: list[Any] = list(ids)
        if search:
            pattern = self._like_pattern(search)
            clauses.append("(r.filename LIKE ? ESCAPE '!' OR r.map_name LIKE ? ESCAPE '!')")
            params.extend((pattern, pattern))
        if classification == "MARINE_ARENA":
            clauses.append("r.map_classification IN ('MARINE_ARENA_EU','MARINE_ARENA_OTHER')")
        elif classification:
            clauses.append("r.map_classification=?")
            params.append(classification)
        if result:
            clauses.append("rp.result=?")
            params.append(result)
        where = " WHERE " + " AND ".join(clauses)
        with self.connection() as conn:
            matches_total = int(conn.execute(
                """SELECT COUNT(DISTINCT r.id) FROM replay_players rp
                   JOIN replays r ON r.id=rp.replay_id""" + where,
                params,
            ).fetchone()[0])
            matches = conn.execute(
                """SELECT r.id replay_id,r.filename,r.imported_at,r.map_name,r.map_classification,
                          MAX(rp.result) result
                   FROM replay_players rp JOIN replays r ON r.id=rp.replay_id
                   """ + where + """ GROUP BY r.id
                   ORDER BY r.imported_at DESC LIMIT ? OFFSET ?""",
                (*params, max(1, min(limit, 100)), max(offset, 0)),
            ).fetchall()
            player_rows = conn.execute(
                f"SELECT id,display_name FROM players WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            all_time_games = int(conn.execute(
                f"SELECT COUNT(DISTINCT replay_id) FROM replay_players WHERE player_id IN ({placeholders})",
                ids,
            ).fetchone()[0])
            aliases = conn.execute(
                f"""SELECT display_name,first_seen_at,last_seen_at FROM player_aliases
                    WHERE player_id IN ({placeholders}) ORDER BY first_seen_at DESC""",
                ids,
            ).fetchall()
            seen_names: set[tuple[str | None, str]] = set()
            name_history = []
            for alias in aliases:
                clan_tag, display_name = split_player_name(alias["display_name"])
                key = (clan_tag, display_name.casefold())
                if key not in seen_names:
                    seen_names.add(key)
                    name_history.append({
                        "display_name": display_name,
                        "clan_tag": clan_tag,
                        "last_seen_at": alias["last_seen_at"],
                    })
            newest_name = player_rows[0]["display_name"]
            return {
                "player_ids": ids,
                "display_name": split_player_name(newest_name)[1],
                "clan_tag": split_player_name(newest_name)[0],
                "matches": [dict(row) for row in matches],
                "matches_total": matches_total,
                "all_time_games": all_time_games,
                "name_history": name_history,
                "profile": self._player_profile(conn, set(ids)),
            }

    def opponent_detail(self, opponent_id: int, limit: int = 100, offset: int = 0, *,
                        search: str = "", classification: str = "", result: str = "") -> dict[str, Any] | None:
        with self.connection() as conn:
            player = conn.execute("SELECT id,display_name FROM players WHERE id=?", (opponent_id,)).fetchone()
            if player is None:
                return None
            clauses = ["rp.player_id=?"]
            params: list[Any] = [opponent_id]
            if search:
                pattern = self._like_pattern(search)
                clauses.append("(r.filename LIKE ? ESCAPE '!' OR r.map_name LIKE ? ESCAPE '!')")
                params.extend((pattern, pattern))
            if classification == "MARINE_ARENA":
                clauses.append("r.map_classification IN ('MARINE_ARENA_EU','MARINE_ARENA_OTHER')")
            elif classification:
                clauses.append("r.map_classification=?")
                params.append(classification)
            if result:
                clauses.append("rp.result=?")
                params.append(result)
            where = " WHERE " + " AND ".join(clauses)
            matches_total = int(conn.execute(
                """SELECT COUNT(*) FROM replay_players rp
                   JOIN replays r ON r.id=rp.replay_id""" + where,
                params,
            ).fetchone()[0])
            matches = conn.execute(
                """SELECT r.id replay_id,r.filename,r.imported_at,r.map_name,r.map_classification,
                          rp.result
                   FROM replay_players rp JOIN replays r ON r.id=rp.replay_id
                   """ + where + " ORDER BY r.imported_at DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(limit, 100)), max(offset, 0)),
            ).fetchall()
            all_time_games = int(conn.execute(
                "SELECT COUNT(*) FROM replay_players WHERE player_id=?", (opponent_id,)
            ).fetchone()[0])
            result = dict(player)
            result["matches"] = [dict(row) for row in matches]
            result["matches_total"] = matches_total
            result["clan_tag"], result["display_name"] = split_player_name(result["display_name"])
            result["name_history"] = []
            aliases = conn.execute(
                """SELECT display_name,first_seen_at,last_seen_at
                   FROM player_aliases WHERE player_id=?
                   ORDER BY first_seen_at DESC""",
                (opponent_id,),
            ).fetchall()
            seen_names: set[tuple[str | None, str]] = set()
            for alias in aliases:
                clan_tag, display_name = split_player_name(alias["display_name"])
                key = (clan_tag, display_name.casefold())
                if key not in seen_names:
                    seen_names.add(key)
                    result["name_history"].append({
                        "display_name": display_name,
                        "clan_tag": clan_tag,
                        "last_seen_at": alias["last_seen_at"],
                    })
            result["all_time_games"] = all_time_games
            result["profile"] = self._player_profile(conn, opponent_id)
            return result

    @staticmethod
    def _empty_player_profile() -> dict[str, Any]:
        return {
            "games": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "recorded_results": 0,
            "win_rate": None,
            "average_apm": None,
            "apm_games": 0,
            "units_lost": None,
            "unit_loss_games": 0,
            "top_units": [],
            "top_upgrades": [],
            "map_history": [],
        }

    @staticmethod
    def _player_profile(conn: sqlite3.Connection, player_id: int | set[int]) -> dict[str, Any]:
        player_ids = {player_id} if isinstance(player_id, int) else player_id
        if not player_ids:
            return Repository._empty_player_profile()
        placeholders = ",".join("?" for _ in player_ids)
        params = sorted(player_ids)
        games = conn.execute(
            """SELECT rp.replay_id,MAX(rp.slot) slot,MAX(rp.user_id) user_id,
                      MAX(rp.result) result,MAX(r.duration_seconds) duration_seconds
               FROM replay_players rp JOIN replays r ON r.id=rp.replay_id
               WHERE rp.player_id IN (""" + placeholders + """)
               GROUP BY rp.replay_id ORDER BY MAX(r.imported_at) DESC""",
            params,
        ).fetchall()
        result = Repository._empty_player_profile()
        result["games"] = len(games)
        if not games:
            return result

        match_by_id = {int(game["replay_id"]): game for game in games}
        for game in games:
            outcome = str(game["result"] or "").strip().casefold()
            if outcome in {"win", "won", "victory"}:
                result["wins"] += 1
            elif outcome in {"loss", "lost", "defeat"}:
                result["losses"] += 1
            elif outcome in {"draw", "tie"}:
                result["draws"] += 1
        result["recorded_results"] = result["wins"] + result["losses"] + result["draws"]
        decided_games = result["wins"] + result["losses"]
        if decided_games:
            result["win_rate"] = round(result["wins"] * 100 / decided_games, 1)

        maps = conn.execute(
            """SELECT COALESCE(NULLIF(r.map_name,''),'Unknown map') map_name,
                      COUNT(DISTINCT r.id) games
               FROM replay_players rp JOIN replays r ON r.id=rp.replay_id
               WHERE rp.player_id IN (""" + placeholders + """)
               GROUP BY map_name ORDER BY games DESC,map_name COLLATE NOCASE""",
            params,
        ).fetchall()
        result["map_history"] = [dict(row) for row in maps[:5]]

        replay_ids = list(match_by_id)
        participant_slots: dict[tuple[int, int], int] = {}
        participant_users: dict[tuple[int, int], int] = {}
        for start in range(0, len(replay_ids), 250):
            batch = replay_ids[start:start + 250]
            placeholders = ",".join("?" for _ in batch)
            participants = conn.execute(
                f"""SELECT replay_id,player_id,slot,user_id FROM replay_players
                    WHERE replay_id IN ({placeholders})""",
                batch,
            ).fetchall()
            for participant in participants:
                replay_id = int(participant["replay_id"])
                if participant["slot"] is not None:
                    participant_slots[(replay_id, int(participant["slot"]))] = int(participant["player_id"])
                if participant["user_id"] is not None:
                    participant_users[(replay_id, int(participant["user_id"]))] = int(participant["player_id"])

        unit_counts: dict[str, int] = {}
        upgrade_counts: dict[str, int] = {}
        tracked_deaths = 0
        unit_tracking_games: set[int] = set()
        unit_types: dict[tuple[int, str, str], str] = {}
        apm_values: list[float] = []
        apm_by_game: dict[int, int] = {}
        for start in range(0, len(replay_ids), 250):
            batch = replay_ids[start:start + 250]
            placeholders = ",".join("?" for _ in batch)
            events = conn.execute(
                f"""SELECT replay_id,event_type,raw_identifier,payload_json
                    FROM raw_events WHERE replay_id IN ({placeholders})
                    AND (lower(event_type) LIKE '%scmdevent%'
                      OR lower(event_type) LIKE '%sselectiondeltaevent%'
                      OR lower(event_type) LIKE '%scontrolgroupupdateevent%'
                      OR lower(event_type) LIKE '%sunitbornevent%'
                      OR lower(event_type) LIKE '%sunitinitevent%'
                      OR lower(event_type) LIKE '%sunitdiedevent%'
                      OR lower(event_type) LIKE '%supgradeevent%')
                    ORDER BY replay_id,id""",
                batch,
            )
            unit_owners: dict[tuple[int, str, str], int] = {}
            for event in events:
                replay_id = int(event["replay_id"])
                event_type = str(event["event_type"]).casefold()
                try:
                    payload = json.loads(event["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if any(kind in event_type for kind in (
                    "scmdevent", "sselectiondeltaevent", "scontrolgroupupdateevent",
                )):
                    user_id = payload.get("_userid")
                    try:
                        matched_player = participant_users.get((replay_id, int(user_id)))
                    except (TypeError, ValueError):
                        matched_player = None
                    if matched_player in player_ids:
                        apm_by_game[replay_id] = apm_by_game.get(replay_id, 0) + 1
                if "sunitbornevent" in event_type or "sunitinitevent" in event_type:
                    try:
                        owner_slot = int(payload.get("m_controlPlayerId"))
                    except (TypeError, ValueError):
                        owner_slot = 0
                    owner_id = participant_slots.get((replay_id, owner_slot))
                    tag_index = payload.get("m_unitTagIndex")
                    tag_recycle = payload.get("m_unitTagRecycle", 0)
                    if tag_index is not None:
                        tag = (replay_id, str(tag_index), str(tag_recycle))
                        if owner_id is not None:
                            unit_owners[tag] = owner_id
                        identifier = str(event["raw_identifier"] or payload.get("m_unitTypeName") or "")
                        label = Repository._readable_identifier(identifier, "unit")
                        unit_types[tag] = label
                        is_structure = Repository._is_structure_identifier(label)
                        if owner_id in player_ids and not is_structure:
                            unit_tracking_games.add(replay_id)
                        if owner_id in player_ids and not is_structure and "sunitbornevent" in event_type:
                            companion = payload.get("_companion", {})
                            mapping = companion.get("semantic_mapping") if isinstance(companion, dict) else None
                            label = mapping.get("label") if isinstance(mapping, dict) else None
                            unit_label = Repository._readable_identifier(
                                str(label or identifier), "unit",
                            )
                            if unit_label:
                                unit_counts[unit_label] = unit_counts.get(unit_label, 0) + 1
                elif "sunitdiedevent" in event_type:
                    tag_index = payload.get("m_unitTagIndex")
                    tag_recycle = payload.get("m_unitTagRecycle", 0)
                    if tag_index is not None:
                        tag = (replay_id, str(tag_index), str(tag_recycle))
                        owner_id = unit_owners.get(tag)
                        if owner_id in player_ids and not Repository._is_structure_identifier(unit_types.get(tag, "")):
                            tracked_deaths += 1
                elif "supgradeevent" in event_type:
                    try:
                        owner_slot = int(payload.get("m_playerId"))
                    except (TypeError, ValueError):
                        owner_slot = 0
                    if participant_slots.get((replay_id, owner_slot)) in player_ids:
                        identifier = str(event["raw_identifier"] or "").strip()
                        if identifier:
                            upgrade_label = Repository._readable_identifier(identifier, "upgrade")
                            upgrade_counts[upgrade_label] = upgrade_counts.get(upgrade_label, 0) + 1

        for replay_id, count in apm_by_game.items():
            duration = match_by_id[replay_id]["duration_seconds"]
            if duration and float(duration) > 0:
                apm_values.append(count * 60 / float(duration))
        if apm_values:
            result["average_apm"] = round(sum(apm_values) / len(apm_values), 1)
            result["apm_games"] = len(apm_values)
        if unit_tracking_games:
            result["units_lost"] = tracked_deaths
            result["unit_loss_games"] = len(unit_tracking_games)
        result["top_units"] = [
            {"name": name, "count": count}
            for name, count in sorted(unit_counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:5]
        ]
        result["top_upgrades"] = [
            {"name": name, "count": count}
            for name, count in sorted(upgrade_counts.items(), key=lambda item: (-item[1], item[0].casefold()))[:5]
        ]
        return result

    @staticmethod
    def _readable_identifier(value: str, category: str) -> str:
        label = value.strip()
        if not label or re.fullmatch(r"(?:0x[0-9a-f]+|\d+)", label, re.IGNORECASE):
            return f"Other {category}"
        label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", label)
        label = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", label)
        label = re.sub(r"[_\s]+", " ", label).strip()
        return label or f"Other {category}"

    @staticmethod
    def _is_structure_identifier(value: str) -> bool:
        compact = re.sub(r"[^a-z]", "", value.casefold())
        tokens = (
            "commandcenter", "orbitalcommand", "planetaryfortress",
            "supplydepot", "refinery", "barracks", "engineeringbay",
            "missileturret", "sensortower", "bunker", "ghostacademy",
            "factory", "starport", "armory", "fusioncore", "techlab",
            "reactor", "nexus", "pylon", "assimilator", "gateway",
            "forge", "cyberneticscore", "photoncannon", "shieldbattery",
            "stargate", "roboticsfacility", "twilightcouncil", "templararchive",
            "hatchery", "lair", "hive", "extractor", "spawningpool",
            "evolutionchamber", "sporecrawler", "spinecrawler", "roachwarren",
            "hydraliskden", "infestationpit", "ultraliskcavern", "spire",
            "creepcolony", "nydus",
        )
        return any(token in compact for token in tokens)

    def diagnostics(self) -> dict[str, Any]:
        with self.connection() as conn:
            unknown_rows = conn.execute(
                """SELECT event_type,raw_identifier,COUNT(*) count FROM raw_events
                   WHERE raw_identifier IS NOT NULL GROUP BY event_type,raw_identifier ORDER BY count DESC LIMIT 1000"""
            ).fetchall()
            mappings = {
                (row["category"], row["raw_identifier"])
                for row in conn.execute("SELECT category,raw_identifier FROM semantic_mappings")
            }
            unknown = []
            for row in unknown_rows:
                event_type = row["event_type"].casefold()
                category = (
                    "units" if "unit" in event_type else
                    "upgrades" if "upgrade" in event_type else
                    "abilities" if any(token in event_type for token in ("ability", "command", "scmd")) else
                    None
                )
                if category and (category, row["raw_identifier"]) in mappings:
                    continue
                unknown.append(row)
            failures = conn.execute("SELECT path,stage,reason,occurred_at FROM scan_failures ORDER BY id DESC LIMIT 200").fetchall()
            builds = conn.execute("SELECT DISTINCT base_build FROM replays WHERE base_build IS NOT NULL ORDER BY base_build").fetchall()
            versions = conn.execute("SELECT map_name,COUNT(*) count FROM replays WHERE map_name IS NOT NULL GROUP BY map_name ORDER BY count DESC").fetchall()
            event_frequency = conn.execute(
                "SELECT event_type,COUNT(*) count FROM raw_events GROUP BY event_type ORDER BY count DESC LIMIT 1000"
            ).fetchall()
            unit_events = [dict(row) for row in unknown if "unit" in row["event_type"].casefold()]
            upgrade_events = [dict(row) for row in unknown if "upgrade" in row["event_type"].casefold()]
            ability_events = [dict(row) for row in unknown if any(token in row["event_type"].casefold() for token in ("ability", "command", "scmd"))]
            return {
                "unknown_identifiers": [dict(row) for row in unknown],
                "unknown_units": unit_events,
                "unknown_upgrades": upgrade_events,
                "unknown_abilities": ability_events,
                "failures": [dict(row) for row in failures],
                "protocol_builds": [row["base_build"] for row in builds],
                "map_names": [dict(row) for row in versions],
                "event_frequency": [dict(row) for row in event_frequency],
            }

    def health_summary(self) -> dict[str, Any]:
        library = self.summary()
        stage_messages = {
            "readiness": ("Some replay files were not ready to scan", "Try scanning again after StarCraft II finishes saving them.", "info"),
            "decoder": ("Some replay formats are not supported yet", "These replays remain saved; a future decoder update may add support.", "info"),
            "analysis": ("Some replays could not be fully analyzed", "Use Retry Failed Replays to try them again.", "warning"),
            "discovery": ("Some replay files became unavailable", "Check that the files still exist in your replay folder.", "warning"),
            "partial-decoder": ("Some optional match details are missing", "The replay was saved, but not every detail could be read.", "info"),
        }
        with self.connection() as conn:
            failures = conn.execute(
                """SELECT stage,COUNT(DISTINCT path) count FROM scan_failures
                   GROUP BY stage ORDER BY count DESC"""
            ).fetchall()
        issues = []
        for failure in failures:
            title, action, severity = stage_messages.get(
                failure["stage"],
                ("Some replay details could not be processed", "Retry the affected replay if the issue continues.", "warning"),
            )
            issues.append({
                "title": title,
                "action": action,
                "count": failure["count"],
                "severity": severity,
            })
        return {
            "library": {
                "total": library["total"],
                "analyzed": library["analyzed"],
                "failed": library["failed"],
                "players": library["players"],
            },
            "issues": issues,
        }

    def failed_paths(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT sf.path FROM scan_failures sf
                   LEFT JOIN replays r ON r.original_path=sf.path
                   WHERE r.id IS NULL OR r.decode_status IN ('DECODE_FAILED','UNSUPPORTED_PROTOCOL','NORMALIZATION_FAILED')
                   UNION SELECT original_path AS path FROM replays
                   WHERE decode_status IN ('DECODE_FAILED','UNSUPPORTED_PROTOCOL','NORMALIZATION_FAILED')"""
            ).fetchall()
            return [dict(row) for row in rows]
