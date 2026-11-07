"""Small display helpers for the templates."""

from datetime import date, datetime


def fmt_date(d, with_year=True):
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return d.strftime("%-d %b %Y" if with_year else "%-d %b")


def fmt_dt(d):
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return d.strftime("%-d %b %Y, %H:%M")


def fmt_hours(start_hour, end_hour):
    if start_hour is None or end_hour is None:
        return "All operating hours"
    return f"{start_hour:02d}:00–{end_hour:02d}:00"


def heat(pct, closed=False):
    """A CSS class for a calendar cell, by how full the busiest hour is."""
    if closed:
        return "heat-closed"
    if pct <= 0.01:
        return "heat-0"
    if pct < 50:
        return "heat-1"
    if pct < 80:
        return "heat-2"
    if pct < 100 - 1e-6:
        return "heat-3"
    return "heat-4"


def register(app):
    app.jinja_env.filters.update(date=fmt_date, dt=fmt_dt, heat=heat,
                                 pct=lambda v: f"{v:.0f}%", num=lambda v: f"{v:g}")
    app.jinja_env.globals.update(fmt_hours=fmt_hours, today=date.today, now=datetime.utcnow)
