"""Tests for lenient frontmatter date parsing."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from second_brain.dates import parse_date


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 6), date(2026, 10, 6)),
        (datetime(2026, 10, 6, 18, 17), date(2026, 10, 6)),
        ("2026-10-06", date(2026, 10, 6)),
        ("2026-10-06T18:17:00Z", date(2026, 10, 6)),
        ("6 de octubre de 2026", date(2026, 10, 6)),
        ("1 de septiembre del 2025", date(2025, 9, 1)),
        ("15 de Marzo de 2024", date(2024, 3, 15)),
        ("6 October 2026", date(2026, 10, 6)),
        ("October 6, 2026", date(2026, 10, 6)),
        ("Oct 6, 2026", None),  # abbreviations not supported
        ("31 de febrero de 2026", None),
        ("1970-01-01", None),  # implausible year
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(value: object, expected: date | None) -> None:
    assert parse_date(value) == expected
