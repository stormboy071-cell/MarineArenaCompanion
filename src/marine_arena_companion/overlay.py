from __future__ import annotations

import threading
import time
from typing import Callable


class OverlaySession:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._started_at: float | None = None
        self._elapsed_before_pause = 0.0
        self._dismissed: set[int] = set()

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._started_at is None:
                self._started_at = self._clock()
            return self._status_locked()

    def pause(self) -> dict[str, object]:
        with self._lock:
            if self._started_at is not None:
                self._elapsed_before_pause += max(0.0, self._clock() - self._started_at)
                self._started_at = None
            return self._status_locked()

    def reset(self) -> dict[str, object]:
        with self._lock:
            self._started_at = None
            self._elapsed_before_pause = 0.0
            self._dismissed.clear()
            return self._status_locked()

    def dismiss(self, reminder_id: int) -> dict[str, object]:
        with self._lock:
            self._dismissed.add(reminder_id)
            return self._status_locked()

    def remove_reminder(self, reminder_id: int) -> None:
        with self._lock:
            self._dismissed.discard(reminder_id)

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, object]:
        elapsed = self._elapsed_before_pause
        if self._started_at is not None:
            elapsed += max(0.0, self._clock() - self._started_at)
        return {
            "running": self._started_at is not None,
            "elapsed_seconds": int(elapsed),
            "dismissed_reminder_ids": sorted(self._dismissed),
        }
