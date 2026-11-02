from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


class ReplayNotReadyError(OSError):
    """The replay is still being written or cannot yet be opened."""


def wait_until_ready(
    path: Path,
    *,
    attempts: int = 12,
    interval: float = 1.0,
    stable_checks: int = 2,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    if attempts < stable_checks or stable_checks < 2:
        raise ValueError("attempts must cover at least two stable checks")
    prior_size: int | None = None
    stable = 0
    for _ in range(attempts):
        try:
            with path.open("rb") as stream:
                header = stream.read(16)
                size = path.stat().st_size
                if size < 16 or len(header) < 16:
                    raise ReplayNotReadyError("Replay is too small to contain an MPQ header")
                if header[:4] != b"MPQ\x1b":
                    raise ReplayNotReadyError("Replay does not have a recognized MPQ header")
        except (OSError, ReplayNotReadyError):
            stable = 0
            prior_size = None
        else:
            if size == prior_size:
                stable += 1
                if stable >= stable_checks:
                    return size
            else:
                stable = 1
                prior_size = size
        sleeper(interval)
    raise ReplayNotReadyError(f"Replay did not become stable after {attempts} checks")
