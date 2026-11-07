from datetime import date, datetime

import pytest

from billboard_ops.parsing import (parse_bool, parse_date, parse_hour, parse_hours_range, parse_number,
                                   parse_status)


@pytest.mark.parametrize("raw,expected", [
    ("2026-03-04", date(2026, 3, 4)),
    ("2026/3/4", date(2026, 3, 4)),
    ("04/03/2026", date(2026, 3, 4)),          # day first by default
    ("4.3.26", date(2026, 3, 4)),
    ("25/03/2026", date(2026, 3, 25)),
    ("03/25/2026", date(2026, 3, 25)),         # only one reading possible
    ("4 Mar 2026", date(2026, 3, 4)),
    ("4-March-2026", date(2026, 3, 4)),
    ("March 4, 2026", date(2026, 3, 4)),
    ("4th Sept 2026", date(2026, 9, 4)),
    (46085, date(2026, 3, 4)),                 # a Sheets day number
    (datetime(2026, 3, 4, 0, 0), date(2026, 3, 4)),
])
def test_dates(raw, expected):
    assert parse_date(raw) == expected


def test_month_first_when_set():
    assert parse_date("04/03/2026", "mdy") == date(2026, 4, 3)


@pytest.mark.parametrize("raw", ["", None, "soon", "31/02/2026", "2026-13-01"])
def test_unreadable_dates(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("6", 6), ("06:00", 6), ("6am", 6), ("6 pm", 18), ("12am", 0), ("12pm", 12), ("24:00", 24),
    ("midnight", 24), ("noon", 12), (18, 18), (0.75, 18), ("18.30", 18), ("25", None), ("x", None),
])
def test_hours(raw, expected):
    assert parse_hour(raw) == expected


def test_hour_ranges():
    assert parse_hours_range("06:00-24:00") == (6, 24)
    assert parse_hours_range("6pm to 2am") == (18, 2)
    assert parse_hours_range("24/7") == (None, None)
    assert parse_hours_range("") == (None, None)
    with pytest.raises(ValueError):
        parse_hours_range("evenings")


def test_statuses():
    assert parse_status("Tentative") == ("hold", True)
    assert parse_status("BOOKED") == ("confirmed", True)
    assert parse_status("canceled") == ("cancelled", True)
    assert parse_status("maybe?") == ("hold", False)
    assert parse_status("") == ("confirmed", False)


def test_small_things():
    assert parse_bool("Yes") and parse_bool("x") and not parse_bool("no") and not parse_bool("")
    assert parse_number("1,200 sec") == 1200 and parse_number("") is None
