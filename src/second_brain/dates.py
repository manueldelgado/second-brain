"""Lenient date parsing for frontmatter written by web clippers."""

from __future__ import annotations

import re
from datetime import date, datetime

# Month names (lower-case, accents stripped) in the languages clippers write.
_MONTHS = {
    # Spanish
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# "6 de octubre de 2026", "6 October 2026"
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)\.?,?\s+(?:de\s+|del\s+)?(\d{4})")
# "October 6, 2026"
_MONTH_DAY_YEAR = re.compile(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")


def parse_date(value: object) -> date | None:
    """Parse *value* into a date, or None.

    Accepts dates, datetimes, ISO strings and long-form Spanish/English dates
    ("6 de octubre de 2026", "October 6, 2026"). Implausible years (clipper
    garbage like 1970 or 0001) are rejected.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed: date | None = value
    else:
        parsed = _parse_str(str(value))
    if parsed is not None and 2005 <= parsed.year <= date.today().year + 1:
        return parsed
    return None


def _parse_str(text: str) -> date | None:
    text = text.strip().lower()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    if m := _DAY_MONTH_YEAR.search(text):
        day, month, year = m.group(1), m.group(2), m.group(3)
    elif m := _MONTH_DAY_YEAR.search(text):
        month, day, year = m.group(1), m.group(2), m.group(3)
    else:
        return None
    month_num = _MONTHS.get(month.translate(str.maketrans("áéíóú", "aeiou")))
    if month_num is None:
        return None
    try:
        return date(int(year), month_num, int(day))
    except ValueError:
        return None
