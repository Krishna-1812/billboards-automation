"""The vacancy engine: is there room in a screen's loop for these dates, hours and plays?

A digital screen plays a loop that several advertisers share. So vacancy is measured in airtime: every hour a
screen is on, it has 3600 seconds to sell (less any share the owner keeps for their own content). A booking uses
`ad_seconds × plays_per_hour` of each hour it runs; a whole-screen booking uses all of it. A request fits an hour
when what it needs is no more than what the other bookings leave free.

Days here are "broadcast days": a screen that runs 18:00–02:00 on 1 March plays its 00:00 and 01:00 hours on the
calendar date 2 March. Every hour is keyed by its real (date, hour), so overnight hours of one day and the early
hours of the next can never be counted twice or missed.

The core works on plain data (`Rules`, `Use`, `Request`) so it can be tested without a database; the functions at
the bottom load that data from the database.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

EPS = 1e-6
SECONDS_PER_HOUR = 3600.0


# ── Plain data ──────────────────────────────────────────────────────────────────

@dataclass
class Rules:
    """A screen's hours and capacity."""
    screen_id: int
    open_hour: int = 6
    close_hour: int = 24
    sellable_pct: float = 100.0
    blackouts: list = field(default_factory=list)   # [(start_date, end_date)]
    overrides: list = field(default_factory=list)   # [(start_date, end_date, open_hour, close_hour)]

    @property
    def capacity(self):
        return SECONDS_PER_HOUR * max(0.0, min(100.0, self.sellable_pct)) / 100.0


@dataclass
class Use:
    """An existing booking line on a screen."""
    booking_id: int
    booking_code: str
    status: str
    start_date: date
    end_date: date
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    ad_seconds: float = 10
    plays_per_hour: float = 30
    exclusive: bool = False
    client: str = ""
    plan: Optional[dict] = None       # a confirmed play plan: plays per hour vary by hour, weekday and date


@dataclass
class Request:
    start_date: date
    end_date: date
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    ad_seconds: float = 10
    plays_per_hour: float = 30
    exclusive: bool = False

    def shifted(self, days):
        d = timedelta(days=days)
        return Request(self.start_date + d, self.end_date + d, self.start_hour, self.end_hour,
                       self.ad_seconds, self.plays_per_hour, self.exclusive)


def demand(item, rules):
    """Seconds of each hour this booking or request takes."""
    if item.exclusive:
        return rules.capacity
    return float(item.ad_seconds) * float(item.plays_per_hour)


def plan_plays(plan, day, hour):
    """Plays in one hour under a play plan. `day` is the broadcast day, `hour` the clock hour.
    Dates with special terms come first, then the weekdays the ad runs on, then the plays for that hour."""
    iso = day.isoformat()
    for ex in plan.get("exceptions") or []:
        if str(ex.get("from", "")) <= iso <= str(ex.get("to", "")):
            return float(ex.get("plays") or 0)
    days = plan.get("weekdays")
    if days is not None and day.weekday() not in days:
        return 0.0
    return float((plan.get("hours") or {}).get(str(hour), 0) or 0)


def slot_demand(item, rules, day, slot):
    """Seconds a booking or request takes in one (date, hour) slot of a broadcast day."""
    if item.exclusive:
        return rules.capacity
    plan = getattr(item, "plan", None)
    if plan:
        return float(item.ad_seconds) * plan_plays(plan, day, slot[1])
    return float(item.ad_seconds) * float(item.plays_per_hour)


def days_between(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ── Hours ───────────────────────────────────────────────────────────────────────

def window(day, open_hour, close_hour):
    """The real (date, hour) slots of a span of hours on a broadcast day.
    close <= open runs past midnight; open == close (or 0 → 24) is all day."""
    o, c = int(open_hour) % 24, int(close_hour)
    if c == 24 and o == 0:
        hours = [(0, h) for h in range(24)]
    elif c % 24 == o:
        hours = [(0 if h < 24 else 1, h % 24) for h in range(o, o + 24)]
    elif c > o:
        hours = [(0, h) for h in range(o, c)]
    else:
        hours = [(0, h) for h in range(o, 24)] + [(1, h) for h in range(0, c)]
    return {(day + timedelta(days=off), h) for off, h in hours}


def in_blackout(rules, day):
    return any(s <= day <= e for s, e in rules.blackouts)


def hours_for(rules, day):
    """(open, close) for a broadcast day: the last override covering it, else the screen's own."""
    found = (rules.open_hour, rules.close_hour)
    for s, e, o, c in rules.overrides:
        if s <= day <= e:
            found = (o, c)
    return found


def operating_slots(rules, day):
    if in_blackout(rules, day):
        return set()
    o, c = hours_for(rules, day)
    return window(day, o, c)


def wanted_slots(rules, item, day):
    """The hours a booking or request asks for on a broadcast day (before operating hours are applied)."""
    if item.start_hour is None or item.end_hour is None:
        o, c = hours_for(rules, day)
        return window(day, o, c)
    return window(day, item.start_hour, item.end_hour)


def playing_slots(rules, item, day):
    return wanted_slots(rules, item, day) & operating_slots(rules, day)


# ── Usage ───────────────────────────────────────────────────────────────────────

def usage(rules, uses, start, end):
    """Seconds used per (date, hour), and who uses them, for every hour of broadcast days start..end.
    Bookings from the day before are included, since their late hours can run into `start`."""
    used = defaultdict(float)
    who = defaultdict(list)
    lo, hi = start - timedelta(days=1), end
    for u in uses:
        for day in days_between(max(u.start_date, lo), min(u.end_date, hi)):
            for slot in playing_slots(rules, u, day):
                need = slot_demand(u, rules, day, slot)
                if need <= 0:
                    continue
                used[slot] += need
                who[slot].append(u)
    return used, who


@dataclass
class DayResult:
    day: date
    blackout: bool = False
    wanted: int = 0
    ok: int = 0
    full: list = field(default_factory=list)        # hours with no room
    outside: list = field(default_factory=list)     # hours the screen is off
    peak_used_pct: float = 0.0                      # how full the busiest asked-for hour already is

    @property
    def fits(self):
        return not self.blackout and self.wanted > 0 and self.ok == self.wanted


@dataclass
class Result:
    screen_id: int
    status: str                                     # fits | partial | no
    reason: str = ""
    days: list = field(default_factory=list)
    blocking: list = field(default_factory=list)    # booking codes in the way
    need_seconds: float = 0.0
    capacity: float = 0.0
    alt_dates: list = field(default_factory=list)   # [(start, end)] that fit fully
    alt_screens: list = field(default_factory=list)  # filled in by the database layer

    @property
    def ok_days(self):
        return sum(1 for d in self.days if d.fits)

    @property
    def problem_days(self):
        return [d for d in self.days if not d.fits]


def check(rules, uses, req, screen_id=None):
    """Does `req` fit on a screen with these rules and existing uses?"""
    sid = screen_id if screen_id is not None else rules.screen_id
    cap = rules.capacity
    need = demand(req, rules)
    res = Result(screen_id=sid, status="no", need_seconds=need, capacity=cap)
    if req.end_date < req.start_date:
        res.reason = "end_before_start"
        return res
    if need <= 0:
        res.reason = "nothing_asked"
        return res
    if need > cap + EPS:
        res.reason = "more_than_capacity"
        return res
    used, who = usage(rules, uses, req.start_date, req.end_date)
    blocking = set()
    total_ok = 0
    for day in days_between(req.start_date, req.end_date):
        dr = DayResult(day=day)
        if in_blackout(rules, day):
            dr.blackout = True
            res.days.append(dr)
            continue
        want = wanted_slots(rules, req, day)
        on = operating_slots(rules, day)
        dr.wanted = len(want)
        for slot in sorted(want):
            if slot not in on:
                dr.outside.append(slot[1])
                continue
            u = used.get(slot, 0.0)
            dr.peak_used_pct = max(dr.peak_used_pct, 100.0 * u / cap if cap else 100.0)
            free = cap - u
            if need <= free + EPS:
                dr.ok += 1
            else:
                dr.full.append(slot[1])
                blocking.update(x.booking_code for x in who.get(slot, []))
        total_ok += dr.ok
        res.days.append(dr)
    res.blocking = sorted(blocking)
    if res.days and all(d.fits for d in res.days):
        res.status = "fits"
    elif total_ok > 0:
        res.status = "partial"
        res.reason = _reason(res)
    else:
        res.status = "no"
        res.reason = _reason(res)
    return res


def _reason(res):
    if any(d.full for d in res.days):
        return "full"
    if any(d.blackout for d in res.days):
        return "blackout"
    if any(d.outside for d in res.days):
        return "outside_hours"
    return "unavailable"


def alternative_dates(rules, uses, req, today=None, max_shift=60, want=3):
    """The nearest whole windows of the same length that fit, trying later dates first at each distance."""
    found = []
    for shift in range(1, max_shift + 1):
        for s in (shift, -shift):
            cand = req.shifted(s)
            if today and cand.start_date < today:
                continue
            if check(rules, uses, cand).status == "fits":
                found.append((cand.start_date, cand.end_date))
                if len(found) >= want:
                    return sorted(found)
    return sorted(found)


def day_fill(rules, used, day):
    """How full a broadcast day is: (peak %, average %, closed?)."""
    slots = operating_slots(rules, day)
    if not slots:
        return 0.0, 0.0, True
    cap = rules.capacity or 1.0
    vals = [100.0 * used.get(s, 0.0) / cap for s in slots]
    return max(vals), sum(vals) / len(vals), False


# ── Database layer ──────────────────────────────────────────────────────────────

def load_rules(session, screen):
    from sqlalchemy import or_, select
    from .models import Blackout, HoursOverride
    bl = session.scalars(select(Blackout).where(or_(Blackout.screen_id == screen.id,
                                                    Blackout.screen_id.is_(None)))).all()
    ov = session.scalars(select(HoursOverride).where(or_(HoursOverride.screen_id == screen.id,
                                                         HoursOverride.screen_id.is_(None)))
                         .order_by(HoursOverride.screen_id.is_(None).desc(), HoursOverride.id)).all()
    # Screen-wide overrides first, so a screen's own override wins (the last match wins).
    return Rules(screen_id=screen.id, open_hour=screen.open_hour, close_hour=screen.close_hour,
                 sellable_pct=screen.sellable_pct,
                 blackouts=[(b.start_date, b.end_date) for b in bl],
                 overrides=[(o.start_date, o.end_date, o.open_hour, o.close_hour) for o in ov])


def load_uses(session, screen_id, start, end, exclude_booking_id=None):
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    from .models import Booking, BookingLine, OCCUPYING
    q = (select(BookingLine).join(Booking)
         .options(joinedload(BookingLine.booking).joinedload(Booking.client))
         .where(BookingLine.screen_id == screen_id,
                Booking.status.in_(OCCUPYING),
                BookingLine.start_date <= end + timedelta(days=1),
                BookingLine.end_date >= start - timedelta(days=1)))
    if exclude_booking_id:
        q = q.where(Booking.id != exclude_booking_id)
    return [use_from_line(l) for l in session.scalars(q).unique().all()]


def use_from_line(l):
    b = l.booking
    return Use(booking_id=b.id, booking_code=b.code, status=b.status, start_date=l.start_date,
               end_date=l.end_date, start_hour=l.start_hour, end_hour=l.end_hour, ad_seconds=l.ad_seconds,
               plays_per_hour=l.plays_per_hour, exclusive=l.exclusive,
               client=b.client.name if b.client else "", plan=l.confirmed_plan or None)


def check_screen(session, screen, req, exclude_booking_id=None, today=None, alternatives=True):
    rules = load_rules(session, screen)
    span = max(60, (req.end_date - req.start_date).days + 1)
    uses = load_uses(session, screen.id, req.start_date - timedelta(days=span if alternatives else 0),
                     req.end_date + timedelta(days=span if alternatives else 0), exclude_booking_id)
    res = check(rules, uses, req, screen.id)
    if alternatives and res.status != "fits" and res.reason not in ("more_than_capacity", "end_before_start"):
        res.alt_dates = alternative_dates(rules, uses, req, today=today)
    return res


def other_screens(session, screen, req, exclude_booking_id=None, limit=5):
    """Active screens with the same spec, in the same area when the screen has one, where the request fits."""
    from sqlalchemy import select
    from .models import Screen
    q = select(Screen).where(Screen.active.is_(True), Screen.id != screen.id)
    if screen.spec_id:
        q = q.where(Screen.spec_id == screen.spec_id)
    if screen.area:
        q = q.where(Screen.area == screen.area)
    out = []
    for other in session.scalars(q.order_by(Screen.code)).all():
        if check_screen(session, other, req, exclude_booking_id, alternatives=False).status == "fits":
            out.append(other)
            if len(out) >= limit:
                break
    return out


def check_many(session, screens, req, exclude_booking_id=None, today=None, alternatives=True):
    results = []
    for s in screens:
        r = check_screen(session, s, req, exclude_booking_id, today, alternatives)
        if alternatives and r.status != "fits":
            r.alt_screens = other_screens(session, s, req, exclude_booking_id)
        results.append(r)
    return results


def fill_grid(session, screens, start, days):
    """{screen_id: [(day, peak %, average %, closed?)]} for the calendar grid."""
    end = start + timedelta(days=days - 1)
    grid = {}
    for s in screens:
        rules = load_rules(session, s)
        used, _ = usage(rules, load_uses(session, s.id, start, end), start, end)
        grid[s.id] = [(d, *day_fill(rules, used, d)) for d in days_between(start, end)]
    return grid


def hourly_detail(session, screen, day):
    """Each operating hour of a broadcast day: (date, hour, used %, [uses])."""
    rules = load_rules(session, screen)
    used, who = usage(rules, load_uses(session, screen.id, day, day), day, day)
    cap = rules.capacity or 1.0
    return [(d, h, 100.0 * used.get((d, h), 0.0) / cap, who.get((d, h), []))
            for d, h in sorted(operating_slots(rules, day))]
