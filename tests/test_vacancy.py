"""The vacancy engine on plain data: loops, hours past midnight, blackouts, special hours and whole-screen bookings."""

from datetime import date, timedelta

from billboard_ops import vacancy as v

D = date(2026, 3, 1)


def rules(**kw):
    base = dict(screen_id=1, open_hour=6, close_hour=24, sellable_pct=100.0)
    base.update(kw)
    return v.Rules(**base)


def use(code, start=D, end=None, sh=None, eh=None, ad=10, pph=30, exclusive=False, status="confirmed"):
    return v.Use(1, code, status, start, end or start, sh, eh, ad, pph, exclusive)


def req(start=D, end=None, sh=None, eh=None, ad=10, pph=30, exclusive=False):
    return v.Request(start, end or start, sh, eh, ad, pph, exclusive)


# ── Hours ───────────────────────────────────────────────────────────────────────

def test_window_daytime():
    assert v.window(D, 6, 24) == {(D, h) for h in range(6, 24)}


def test_window_past_midnight_runs_into_next_date():
    w = v.window(D, 18, 2)
    assert (D, 23) in w and (D + timedelta(days=1), 0) in w and (D + timedelta(days=1), 1) in w
    assert (D + timedelta(days=1), 2) not in w and len(w) == 8


def test_window_all_day():
    assert len(v.window(D, 0, 24)) == 24
    assert len(v.window(D, 6, 6)) == 24     # open == close: round the clock from 06:00


# ── Capacity ────────────────────────────────────────────────────────────────────

def test_empty_screen_fits():
    r = v.check(rules(), [], req())
    assert r.status == "fits" and r.ok_days == 1 and r.blocking == []


def test_twelve_slots_fill_a_120s_loop_and_the_thirteenth_does_not_fit():
    # 120 s loop of 10 s slots: one slot = 30 plays an hour = 300 s of each hour.
    uses = [use(f"BK-{i}") for i in range(11)]
    assert v.check(rules(), uses, req()).status == "fits"            # the 12th slot
    uses.append(use("BK-11"))
    r = v.check(rules(), uses, req())
    assert r.status == "no" and r.reason == "full"
    assert len(r.blocking) == 12


def test_sellable_share_keeps_house_time_free():
    uses = [use(f"BK-{i}") for i in range(9)]                         # 2700 s of 2880 s
    assert v.check(rules(sellable_pct=80), uses, req(pph=6)).status == "fits"   # +60 s
    assert v.check(rules(sellable_pct=80), uses, req()).status == "no"          # +300 s


def test_asking_for_more_than_an_hour_holds():
    r = v.check(rules(), [], req(ad=15, pph=300))                     # 4500 s an hour
    assert r.status == "no" and r.reason == "more_than_capacity"


def test_end_before_start():
    assert v.check(rules(), [], req(start=D, end=D - timedelta(days=1))).reason == "end_before_start"


def test_whole_screen_needs_an_empty_screen_and_blocks_everyone():
    assert v.check(rules(), [use("BK-1", pph=1)], req(exclusive=True)).status == "no"
    assert v.check(rules(), [], req(exclusive=True)).status == "fits"
    assert v.check(rules(), [use("BK-X", exclusive=True)], req(pph=1)).status == "no"


# ── Day parts ───────────────────────────────────────────────────────────────────

def test_full_evening_makes_an_all_day_request_partial():
    evening = [use(f"E{i}", sh=18, eh=22) for i in range(12)]
    r = v.check(rules(), evening, req())
    assert r.status == "partial"
    assert r.days[0].full == [18, 19, 20, 21]
    assert r.days[0].ok == 18 - 4


def test_morning_request_ignores_a_full_evening():
    evening = [use(f"E{i}", sh=18, eh=22) for i in range(12)]
    assert v.check(rules(), evening, req(sh=6, eh=12)).status == "fits"


def test_overnight_booking_blocks_the_next_dates_early_hours():
    r_night = rules(open_hour=18, close_hour=2)
    night = [use(f"N{i}", start=D) for i in range(12)]          # broadcast day D: 18:00–02:00
    # The next broadcast day's 00:00–02:00 hours are different real hours (D+2), so it fits.
    assert v.check(r_night, night, req(start=D + timedelta(days=1))).status == "fits"
    # Broadcast day D-1 ends at 02:00 on D's date; day D's bookings start at 18:00, so they don't meet.
    assert v.check(r_night, night, req(start=D - timedelta(days=1))).status == "fits"
    # The same broadcast day is full, including its after-midnight hours.
    r = v.check(r_night, night, req(start=D))
    assert r.status == "no" and r.days[0].full == [18, 19, 20, 21, 22, 23, 0, 1]


def test_a_late_booking_on_a_daytime_screen_blocks_only_its_own_hours():
    late = [use(f"L{i}", sh=22, eh=24) for i in range(12)]
    assert v.check(rules(), late, req(start=D, sh=20, eh=22)).status == "fits"
    assert v.check(rules(), late, req(start=D, sh=21, eh=23)).status == "partial"


def test_request_outside_operating_hours():
    r = v.check(rules(), [], req(sh=2, eh=5))
    assert r.status == "no" and r.reason == "outside_hours" and r.days[0].outside == [2, 3, 4]
    r = v.check(rules(), [], req(sh=4, eh=8))
    assert r.status == "partial" and r.days[0].outside == [4, 5]


# ── Blackouts and special hours ─────────────────────────────────────────────────

def test_blackout_day_inside_a_run():
    r = v.check(rules(blackouts=[(D + timedelta(days=1), D + timedelta(days=1))]), [],
                req(start=D, end=D + timedelta(days=2)))
    assert r.status == "partial" and r.reason == "blackout" and [d.blackout for d in r.days] == [False, True, False]


def test_whole_run_blacked_out():
    r = v.check(rules(blackouts=[(D, D + timedelta(days=5))]), [], req(start=D, end=D + timedelta(days=2)))
    assert r.status == "no" and r.reason == "blackout"


def test_special_hours_override_the_usual_ones():
    rl = rules(overrides=[(D, D, 20, 24)])
    # "All operating hours" follows the day's special hours, so it still fits (playing 20:00–24:00 only)...
    assert v.check(rl, [], req()).status == "fits"
    # ...but asking for 06:00–24:00 by name finds the screen off until 20:00.
    r = v.check(rl, [], req(sh=6, eh=24))
    assert r.status == "partial" and r.days[0].outside == list(range(6, 20))
    assert v.check(rl, [], req(start=D + timedelta(days=1))).status == "fits"


def test_last_matching_override_wins():
    rl = rules(overrides=[(D, D + timedelta(days=9), 20, 24), (D, D, 0, 24)])
    assert v.hours_for(rl, D) == (0, 24)
    assert v.hours_for(rl, D + timedelta(days=1)) == (20, 24)


# ── Alternatives and the calendar ───────────────────────────────────────────────

def test_nearest_free_dates_are_suggested():
    full = [use(f"F{i}", start=D, end=D + timedelta(days=2)) for i in range(12)]
    r = req(start=D + timedelta(days=1), end=D + timedelta(days=2))
    alts = v.alternative_dates(rules(), full, r, today=D)
    assert alts[0] == (D + timedelta(days=3), D + timedelta(days=4))
    assert all(a >= D for a, _ in alts)


def test_alternatives_never_suggest_the_past():
    full = [use(f"F{i}", start=D, end=D + timedelta(days=20)) for i in range(12)]
    alts = v.alternative_dates(rules(), full, req(start=D, end=D), today=D, max_shift=30)
    assert alts and all(a > D + timedelta(days=20) for a, _ in alts)


def test_day_fill_reports_peak_and_average():
    uses = [use(f"E{i}", sh=18, eh=24) for i in range(6)]     # 50% for 6 of 18 hours
    used, _ = v.usage(rules(), uses, D, D)
    peak, avg, closed = v.day_fill(rules(), used, D)
    assert not closed and round(peak) == 50 and round(avg, 1) == round(50 * 6 / 18, 1)
    assert v.day_fill(rules(blackouts=[(D, D)]), used, D)[2] is True
