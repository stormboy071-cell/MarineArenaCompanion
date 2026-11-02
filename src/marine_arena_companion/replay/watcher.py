from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from marine_arena_companion.replay.scanner import ReplayScanner


LOG = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, enqueue: Callable[[Path], bool]):
        self.enqueue = enqueue

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(str(event.dest_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._consider(str(event.src_path))

    def _consider(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.suffix.casefold() == ".sc2replay":
            LOG.info("Replay file detected: %s", path)
            self.enqueue(path)


class ReplayWatcher:
    def __init__(self, scanner: ReplayScanner):
        self.scanner = scanner
        self._observer: Observer | None = None
        self._folder: Path | None = None
        self._lock = RLock()
        self.error: str | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._observer and self._observer.is_alive())

    def start(self, folder: Path) -> None:
        with self._lock:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=3)
                self._observer = None
            self._folder = folder
            if not folder.is_dir():
                self.error = f"FOLDER NOT AVAILABLE: {folder}"
                LOG.error(self.error)
                return
            observer = Observer()
            observer.schedule(_Handler(self.scanner.enqueue), str(folder), recursive=True)
            observer.start()
            self._observer = observer
            self.error = None
            LOG.info("Replay watcher started for %s", folder)

    def stop(self) -> None:
        with self._lock:
            observer = self._observer
            self._observer = None
        if observer:
            observer.stop()
            observer.join(timeout=5)
        LOG.info("Replay watcher stopped")

    def status(self) -> dict[str, object]:
        with self._lock:
            folder = str(self._folder) if self._folder else ""
            observer = self._observer
            available = bool(self._folder and self._folder.is_dir())
            error = self.error
            if self._folder and not available:
                error = f"FOLDER NOT AVAILABLE: {self._folder}"
                self.error = error
            elif available and error and error.startswith("FOLDER NOT AVAILABLE:"):
                error = None
                self.error = None
            active = bool(observer and observer.is_alive() and available)
        return {"active": active, "folder": folder, "error": error}
