"""Reading the messy values people type into sheets: dates, hours, yes/no, numbers and statuses."""

import re
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTHS.update({"sept": 9})


def blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _year(y):
    y = int(y)
    return y + 2000 if y < 100 else y


def parse_date(v, order="dmy"):
    """A date from a date, an Excel day number, or text such as 2026-03-01, 01/03/2026, 1 Mar 2026 or
    March 1, 2026. Slashed dates are read day-first unless order is "mdy" (or only one reading is possible)."""
    if blank(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:      # Excel / Sheets serial day
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip().lower().replace(",", " ")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\s+", " ", s)
    try:
        m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ t].*)?", s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})(?: .*)?", s)
        if m:
            a, b, y = int(m[1]), int(m[2]), _year(m[3])
            if a > 12:
                return date(y, b, a)
            if b > 12:
                return date(y, a, b)
            return date(y, b, a) if order != "mdy" else date(y, a, b)
        m = re.fullmatch(r"(\d{1,2})[ -]([a-z]+)[ -](\d{2,4})", s)
        if m and (MONTHS.get(m[2][:4]) or MONTHS.get(m[2][:3])):
            return date(_year(m[3]), MONTHS.get(m[2][:4]) or MONTHS[m[2][:3]], int(m[1]))
        m = re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{2,4})", s)
        if m and m[1][:3] in MONTHS:
            return date(_year(m[3]), MONTHS[m[1][:3]], int(m[2]))
        if re.fullmatch(r"\d{5}(\.0+)?", s):
            return parse_date(float(s), order)
    except ValueError:
        return None
    return None


def parse_hour(v):
    """A whole hour 0–24 from 6, 06:00, 6am, 6 pm, 18:00, 24:00, noon or midnight. Minutes are dropped."""
    if blank(v):
        return None
    if isinstance(v, (int, float)):
        h = float(v)
        if 0 < h < 1:            # a Sheets time stored as a fraction of a day
            h = round(h * 24, 4)
        return int(h) if 0 <= h <= 24 else None
    s = str(v).strip().lower().replace(".", ":")
    if s in ("noon", "midday"):
        return 12
    if s == "midnight":
        return 24
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?", s)
    if not m:
        return None
    h = int(m[1])
    if m[3] == "am" and h == 12:
        h = 0
    elif m[3] == "pm" and h < 12:
        h += 12
    return h if 0 <= h <= 24 else None


ALL_DAY = ("24h", "24 h", "24/7", "24 hours", "all day", "all-day", "full day", "all hours", "any")


def parse_hours_range(v):
    """(start, end) from "06:00-24:00", "6am to 2am", "18-02"; (None, None) for blank or all day;
    raises ValueError when it can't be read."""
    if blank(v):
        return None, None
    s = str(v).strip().lower()
    if s in ALL_DAY:
        return None, None
    parts = re.split(r"\s*(?:-|–|—|to|till|until)\s*", s)
    if len(parts) != 2:
        raise ValueError(f"can't read hours '{v}'")
    a, b = parse_hour(parts[0]), parse_hour(parts[1])
    if a is None or b is None:
        raise ValueError(f"can't read hours '{v}'")
    return a, b


def parse_bool(v):
    if blank(v):
        return False
    return str(v).strip().lower() in ("yes", "y", "true", "1", "x", "✓", "✔", "whole", "exclusive", "full")


def parse_number(v):
    if blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


STATUS_WORDS = {
    "hold": ("hold", "on hold", "held", "tentative", "pencil", "pencilled", "penciled", "option", "reserved",
             "provisional", "blocked", "block"),
    "confirmed": ("confirmed", "confirm", "booked", "signed", "approved", "live", "running", "active", "won"),
    "cancelled": ("cancelled", "canceled", "cancel", "lost", "dropped", "void"),
    "expired": ("expired", "lapsed", "released"),
    "completed": ("completed", "complete", "done", "finished", "ended", "over", "past"),
}


def parse_status(v):
    """(status, recognised?)"""
    if blank(v):
        return "confirmed", False
    s = str(v).strip().lower()
    for status, words in STATUS_WORDS.items():
        if s in words:
            return status, True
    return "hold", False


def norm(s):
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", str(s or "").lower())
