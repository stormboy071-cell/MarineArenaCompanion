from __future__ import annotations

import re


MAX_ELAPSED_SECONDS = 2_147_483_647
_ELAPSED_TIME_PATTERN = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)$")


def parse_elapsed_time(value: str) -> int:
    match = _ELAPSED_TIME_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError("Enter elapsed match time as hours:minutes:seconds, for example 01:24:03.")
    hours, minutes, seconds = (int(part) for part in match.groups())
    elapsed = hours * 3600 + minutes * 60 + seconds
    if elapsed > MAX_ELAPSED_SECONDS:
        raise ValueError("Elapsed match time exceeds the supported range.")
    return elapsed


def format_elapsed_time(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_ELAPSED_SECONDS:
        raise ValueError("Elapsed time must be a non-negative supported duration.")
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
