"""What a line of a proposal costs on the rate card. The one place the pricing formula lives.

For now each screen has a weekly rate for one slot of its loop (Screen.rate_week): a 10-second slot in a 120-second
loop, every loop, for 7 days. A line pays for its share of the loop, its days and its hours:

    price = rate_week × slots × days / 7 × booked hours / operating hours

- slots: the share of airtime the line takes, counted in slots. 10 s × 30 plays an hour on a screen with 10 s
  slots in a 120 s loop is one slot (one slot plays 30 times an hour). The whole screen is every slot.
- hours: a line booked for part of the day (for example 18:00–24:00) pays for those hours only.

Every price can be changed on the proposal itself; the rate card only gives the starting point. To price another
way (per day, per thousand plays, per package), change `list_price` and the tests in tests/test_pricing.py.
"""


def hours_per_day(open_hour, close_hour):
    """Hours in a day from open to close; close <= open runs past midnight; 0–24 (or equal) is all day."""
    if open_hour == close_hour:
        return 24
    return (close_hour - open_hour) % 24 or 24


def slots(screen, ad_seconds, plays_per_hour, exclusive=False):
    if exclusive:
        return float(screen.slots_per_loop)
    one_slot_plays = 3600.0 / max(1, screen.loop_seconds)          # plays an hour of one slot
    return (plays_per_hour * ad_seconds) / (one_slot_plays * max(1, screen.slot_seconds))


def booked_hours(screen, start_hour, end_hour):
    operating = hours_per_day(screen.open_hour, screen.close_hour)
    if start_hour is None or end_hour is None:
        return operating, operating
    return min(operating, hours_per_day(start_hour, end_hour)), operating


def days(start_date, end_date):
    return max(0, (end_date - start_date).days + 1)


def list_price(screen, start_date, end_date, start_hour=None, end_hour=None, ad_seconds=10, plays_per_hour=30.0,
               exclusive=False):
    """The rate-card price, rounded to whole currency units, or None when the screen has no rate yet."""
    if screen is None or screen.rate_week is None:
        return None
    hours, operating = booked_hours(screen, start_hour, end_hour)
    share = (hours / operating) if operating else 1.0
    if exclusive:
        share = 1.0 if start_hour is None else share
    value = screen.rate_week * slots(screen, ad_seconds, plays_per_hour, exclusive) * days(start_date, end_date) / 7 \
        * share
    return float(round(value))


def plays(screen, start_date, end_date, start_hour=None, end_hour=None, plays_per_hour=30.0, exclusive=False):
    """About how many times the ad plays over the line (for the proposal; the play plan has the exact count)."""
    hours, _ = booked_hours(screen, start_hour, end_hour)
    per_hour = (3600.0 / max(1, screen.loop_seconds)) * screen.slots_per_loop if exclusive else plays_per_hour
    return int(round(per_hour * hours * days(start_date, end_date)))

