from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from marine_arena_companion.analysis.identity import player_identity
from marine_arena_companion.classification import classify_map
from marine_arena_companion.db.repository import Repository
from marine_arena_companion.replay.decoder import UnsupportedProtocolError, decode_replay
from marine_arena_companion.replay.hashing import sha256_file
from marine_arena_companion.replay.readiness import ReplayNotReadyError, wait_until_ready
from marine_arena_companion.replay.semantics import SemanticDictionary


LOG = logging.getLogger(__name__)


@dataclass
class ScanProgress:
    active: bool = False
    discovered: int = 0
    processed: int = 0
    marine_arena: int = 0
    skipped: int = 0
    failed: int = 0
    current: str = ""
    started_at: str | None = None


class ReplayScanner:
    def __init__(self, repository: Repository, concurrency: int = 1, semantics: SemanticDictionary | None = None):
        self.repository = repository
        self.semantics = semantics or SemanticDictionary(Path(__file__).parents[1] / "data" / "marine_arena_semantics.json")
        self.executor = ThreadPoolExecutor(max_workers=max(1, min(concurrency, 4)), thread_name_prefix="replay")
        self.lock = threading.RLock()
        self.progress = ScanProgress()
        self._queued: set[str] = set()
        self._scan_paths: set[str] = set()
        self._known_files = repository.replay_file_states()
        self._discovering = False
        self._closed = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            return asdict(self.progress)

    def scan_library(self, folder: Path) -> bool:
        with self.lock:
            if self.progress.active:
                return False
            self._scan_paths = set(self._queued)
            self._discovering = True
            self.progress = ScanProgress(
                active=True, discovered=len(self._scan_paths),
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        threading.Thread(target=self._discover_and_schedule, args=(folder,), daemon=True, name="library-scan").start()
        return True

    def _discover_and_schedule(self, folder: Path) -> None:
        try:
            paths = [path for path in folder.rglob("*") if path.is_file() and path.suffix.casefold() == ".sc2replay"]
            with self.lock:
                self._scan_paths.update(str(path.resolve()).casefold() for path in paths)
                self.progress.discovered = len(self._scan_paths)
            for path in paths:
                if not self.enqueue(path) and not path.is_file():
                    self.repository.add_failure(None, str(path), "discovery", "Replay file disappeared during library scan")
                    with self.lock:
                        self.progress.failed += 1
                        self.progress.processed += 1
            with self.lock:
                self._discovering = False
                self._finish_scan_if_idle()
        except OSError as exc:
            LOG.exception("Replay library discovery failed: %s", exc)
            with self.lock:
                self.progress.failed += 1
                self._discovering = False
                self.progress.active = False

    def enqueue(self, path: Path, force: bool = False) -> bool:
        path = path.resolve()
        if path.suffix.casefold() != ".sc2replay" or not path.is_file():
            return False
        key = str(path).casefold()
        with self.lock:
            if self._closed or key in self._queued:
                return False
            self._queued.add(key)
            if self.progress.active and key not in self._scan_paths:
                self._scan_paths.add(key)
                self.progress.discovered += 1
        self.executor.submit(self._process, path, key, force)
        return True

    def _remember_file(self, path: Path, file_size: int, modified_time: str | None,
                       digest: str, replay_id: int | None, status: str) -> None:
        self.repository.record_replay_file(path, digest, file_size, modified_time, replay_id)
        with self.lock:
            self._known_files[str(path).casefold()] = (file_size, modified_time, status)

    def _process(self, path: Path, key: str, force: bool = False) -> None:
        with self.lock:
            self.progress.current = path.name
        replay_id: int | None = None
        digest: str | None = None
        file_size: int | None = None
        modified_time: str | None = None
        try:
            stat = path.stat()
            file_size = stat.st_size
            modified_time = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            with self.lock:
                known = self._known_files.get(key)
            if (
                not force
                and known is not None
                and known[0] == file_size
                and known[1] == modified_time
                and known[2] in {"ANALYZED", "UNSUPPORTED_PROTOCOL", "DECODE_FAILED", "NORMALIZATION_FAILED"}
            ):
                with self.lock:
                    if self.progress.active and key in self._scan_paths:
                        self.progress.skipped += 1
                        self.progress.processed += 1
                LOG.debug("Unchanged replay skipped: %s", path)
                return

            file_size = wait_until_ready(path)
            digest = sha256_file(path)
            stat = path.stat()
            file_size = stat.st_size
            modified_time = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            replay_id = self.repository.create_replay(
                path, digest, file_size, modified_time,
            )
            if replay_id is None:
                replay_id = self.repository.replay_id_for_hash(digest)
                self._remember_file(path, file_size, modified_time, digest, replay_id, "ANALYZED")
                with self.lock:
                    self.progress.skipped += 1
                    self.progress.processed += 1
                LOG.info("Duplicate replay skipped: %s", path)
                return

            self._remember_file(path, file_size, modified_time, digest, replay_id, "PROCESSING")
            decoded = decode_replay(path)
            classification = classify_map(decoded.map_name)
            for event in decoded.events:
                name = str(event["event_type"]).casefold()
                category = (
                    "units" if "unit" in name else
                    "upgrades" if "upgrade" in name else
                    "abilities" if any(token in name for token in ("ability", "command", "scmd")) else
                    None
                )
                raw_identifier = event.get("raw_identifier")
                if category and raw_identifier:
                    mapping = self.semantics.lookup(category, str(raw_identifier))
                    event["semantic_category"] = category
                    event["semantic_mapping"] = mapping
            for player in decoded.players:
                identity, name = player_identity(player, digest)
                user_id = player.get("player_id")
                try:
                    user_id = int(user_id) if user_id is not None else None
                except (TypeError, ValueError):
                    user_id = None
                self.repository.add_player(
                    replay_id, identity, name, player.get("slot"), player.get("team"),
                    player.get("result"), user_id,
                )
            for start in range(0, len(decoded.events), 500):
                self.repository.add_events(replay_id, decoded.events[start:start + 500])
            self.repository.update_replay(
                replay_id, status="ANALYZED", map_name=decoded.map_name,
                classification=classification.value, map_version=decoded.map_version,
                base_build=decoded.base_build, data_build=decoded.data_build,
                duration_seconds=decoded.duration_seconds, metadata=decoded.metadata,
                error="; ".join(decoded.warnings) or None,
            )
            self._remember_file(path, file_size, modified_time, digest, replay_id, "ANALYZED")
            for warning in decoded.warnings:
                self.repository.add_failure(replay_id, str(path), "partial-decoder", warning)
            with self.lock:
                self.progress.processed += 1
                if classification.value.startswith("MARINE_ARENA"):
                    self.progress.marine_arena += 1
            LOG.info("Replay analyzed: %s (%s)", path, classification.value)
        except ReplayNotReadyError as exc:
            self.repository.add_failure(replay_id, str(path), "readiness", str(exc))
            LOG.warning("Replay not ready; it can be retried on a later scan: %s", path)
            with self.lock:
                self.progress.failed += 1
                self.progress.processed += 1
        except UnsupportedProtocolError as exc:
            if replay_id is not None:
                self.repository.update_replay(replay_id, status="UNSUPPORTED_PROTOCOL", error=str(exc))
            if digest is not None and file_size is not None:
                replay_id = replay_id or self.repository.replay_id_for_hash(digest)
                self._remember_file(path, file_size, modified_time, digest, replay_id, "UNSUPPORTED_PROTOCOL")
            self.repository.add_failure(replay_id, str(path), "decoder", str(exc))
            LOG.warning("Unsupported replay protocol: %s", path)
            with self.lock:
                self.progress.failed += 1
                self.progress.processed += 1
        except Exception as exc:
            if replay_id is not None:
                self.repository.update_replay(replay_id, status="DECODE_FAILED", error=str(exc))
            if digest is not None and file_size is not None:
                replay_id = replay_id or self.repository.replay_id_for_hash(digest)
                self._remember_file(path, file_size, modified_time, digest, replay_id, "DECODE_FAILED")
            self.repository.add_failure(replay_id, str(path), "analysis", str(exc))
            LOG.exception("Replay analysis failed: %s", path)
            with self.lock:
                self.progress.failed += 1
                self.progress.processed += 1
        finally:
            with self.lock:
                self._queued.discard(key)
                self._finish_scan_if_idle()

    def _finish_scan_if_idle(self) -> None:
        if not self._queued:
            self.progress.current = ""
            if self.progress.active and not self._discovering and self.progress.processed >= self.progress.discovered:
                self.progress.active = False

    def retry_failed(self) -> int:
        candidates = [Path(row["path"]) for row in self.repository.failed_paths()]
        unique = {str(path).casefold(): path for path in candidates}
        return sum(1 for path in unique.values() if path.exists() and self.enqueue(path, force=True))

    def close(self) -> None:
        with self.lock:
            self._closed = True
        self.executor.shutdown(wait=False, cancel_futures=False)
