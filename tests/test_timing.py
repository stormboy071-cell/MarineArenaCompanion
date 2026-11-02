from __future__ import annotations

import pytest

from marine_arena_companion.timing import (
    MAX_ELAPSED_SECONDS,
    format_elapsed_time,
    parse_elapsed_time,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:40:00", 2400),
        ("01:24:03", 5043),
        ("123:04:05", 443_045),
    ],
)
def test_parse_elapsed_match_time(value: str, expected: int) -> None:
    assert parse_elapsed_time(value) == expected


@pytest.mark.parametrize("value", ("", "12:60:00", "12:00:60", "-1:00:00", "1:2:03", "596524:00:00"))
def test_parse_elapsed_match_time_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_elapsed_time(value)


def test_format_elapsed_match_time_and_maximum() -> None:
    assert format_elapsed_time(2400) == "00:40:00"
    assert format_elapsed_time(5043) == "01:24:03"
    assert format_elapsed_time(MAX_ELAPSED_SECONDS) == "596523:14:07"
