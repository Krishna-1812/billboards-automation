"""Phase 4: play plans. Day parts, how a plan changes what the loop holds, the overfill guard, Claude's draft,
confirming (by the team, the agent or the client), and the plan page."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import plans, settings, vacancy
from billboard_ops.models import AiCall, Activity, Booking, Message, Proposal

from conftest import future


def booking(s, people, inv, pph=30, screen="a", start=None, days=7, confirm=True, **kw):
    start = start or future(20)
    b = svc.create(s, people["sales"], inv["client"],
                   [svc.LineIn(inv[screen].id, start, start + timedelta(days=days - 1), ad_seconds=10,
                               plays_per_hour=pph, **kw)], title="Launch")
    if confirm:
        svc.confirm(s, people["manager"], b)
    s.commit()
    return b


def parts():
    return plans.parse_dayparts(settings.DEFAULTS["dayparts"])[0]


# ── Day parts ───────────────────────────────────────────────────────────────────

def test_day_parts():
    ps, errors = plans.parse_dayparts(settings.DEFAULTS["dayparts"])
    assert not errors and [plans.label(p["name"]) for p in ps] == ["Morning peak", "Daytime", "Evening peak", "Night"]
    assert [plans.label(p["name"], "ar") for p in ps][0] == "ذروة الصباح" and plans.label("Night", "ar") == "Night"
    assert plans.format_dayparts(ps) == settings.DEFAULTS["dayparts"]          # round trip, Arabic last
    assert plans.part_hours(22, 6) == [22, 23, 0, 1, 2, 3, 4, 5] and plans.part_hours(0, 24) == list(range(24))
    _, errors = plans.parse_dayparts("Morning = 6-12\nDay = 10-24\nNight = 0-6")
    assert any("10:00 is in both Morning and Day" in e for e in errors)
    _, errors = plans.parse_dayparts("Day = 6-22")
    assert any("aren't in any day part" in e for e in errors)
    _, errors = plans.parse_dayparts("Morning 6 to 10")
    assert any("Name = from-to" in e for e in errors)


def test_plan_plays_by_hour_weekday_and_date():
    p = plans.build(parts(), {"Morning peak": 10, "Evening peak": 60, "Daytime": 20, "Night": 0},
                    weekdays=[4, 5], exceptions=[{"from": "2026-12-04", "to": "2026-12-04", "plays": 5}])
    fri, sat, mon = date(2026, 11, 27), date(2026, 11, 28), date(2026, 11, 30)
    assert vacancy.plan_plays(p, fri, 7) == 10 and vacancy.plan_plays(p, fri, 18) == 60
    assert vacancy.plan_plays(p, sat, 23) == 0 and vacancy.plan_plays(p, mon, 18) == 0       # not on Mondays
    assert vacancy.plan_plays(p, date(2026, 12, 4), 18) == 5                                 # special date wins


# ── The loop ────────────────────────────────────────────────────────────────────

def test_a_confirmed_plan_frees_the_hours_it_doesnt_use(s, people, inventory):
    a = booking(s, people, inventory, pph=180)
    morning = vacancy.Request(a.start_date, a.end_date, 6, 10, ad_seconds=10, plays_per_hour=200)
    assert vacancy.check_screen(s, inventory["a"], morning).status == "no"           # 1800 + 2000 > 3600
    p = plans.build(parts(), {"Morning peak": 0, "Daytime": 180, "Evening peak": 240, "Night": 180})
    plans.save(s, people["ops"], a, {a.lines[0].id: p})
    s.commit()
    assert vacancy.check_screen(s, inventory["a"], morning).status == "no"           # a draft reserves nothing new
    plans.confirm(s, people["ops"], a)
    s.commit()
    assert a.plan_status == "confirmed" and a.lines[0].confirmed_plan == p
    assert vacancy.check_screen(s, inventory["a"], morning).status == "fits"
    evening = vacancy.Request(a.start_date, a.end_date, 16, 22, ad_seconds=10, plays_per_hour=150)
    assert vacancy.check_screen(s, inventory["a"], evening).status == "no"           # 2400 + 1500 > 3600
    # Editing it again takes it back to draft but keeps the loop reserved for what was confirmed.
    plans.save(s, people["ops"], a, {a.lines[0].id: plans.flat(a.lines[0], parts())})
    s.commit()
    assert a.plan_status == "draft" and a.lines[0].confirmed_plan == p
    assert vacancy.check_screen(s, inventory["a"], morning).status == "fits"


def test_a_plan_that_overfills_the_loop_is_refused(s, people, inventory):
    booking(s, people, inventory, pph=100)                                   # someone else: 1000 s an hour
    b = booking(s, people, inventory, pph=100)
    heavy = plans.build(parts(), {"Morning peak": 100, "Daytime": 100, "Evening peak": 300, "Night": 100})
    rows = plans.save(s, people["ops"], b, {b.lines[0].id: heavy})
    r = rows[0]
    assert not r["fits"] and r["over_count"] == 6 * 7 and r["over"][0]["hour"] == 16 and r["over"][0]["pct"] == 111
    assert r["over"][0]["with"] == [s.scalars(select(Booking).order_by(Booking.id)).first().code]
    with pytest.raises(plans.PlanError, match="overfill"):
        plans.confirm(s, people["ops"], b)
    with pytest.raises(plans.PlanError):
        plans.confirm(s, people["ops"], b, override_reason="ops can't override")
    plans.confirm(s, people["manager"], b, override_reason="Owner agreed to drop house ads that week")
    assert b.plan_status == "confirmed" and b.plan_override_reason.startswith("Owner agreed")


def test_totals(s, people, inventory):
    b = booking(s, people, inventory, pph=30, days=7)
    p = plans.build(parts(), {"Morning peak": 30, "Daytime": 30, "Evening peak": 60, "Night": 0}, weekdays=[4, 5])
    r = plans.evaluate(s, b, {b.lines[0].id: p})[0]
    # SZR-01 runs 06:00-24:00: morning 4h x 30 + day 6h x 30 + evening 6h x 60 + night 2h x 0 = 660 a day, 2 days a week
    assert r["per_day"] == 660 and r["total"] == 1320 and r["days"] == 2 and r["fits"]


# ── Claude's draft ──────────────────────────────────────────────────────────────

def _with_conversation(s, b, text):
    p = Proposal(channel="email", sender="ads@acme.example", subject="Spring", client_id=b.client_id,
                 booking_id=b.id, status="held")
    s.add(p)
    s.flush()
    s.add(Message(proposal_id=p.id, direction="in", channel="email", body=text, status="received"))
    s.commit()
    return p


def test_claude_drafts_the_plan_from_the_conversation(s, people, inventory, fake_ai):
    b = booking(s, people, inventory, pph=30)
    _with_conversation(s, b, "30 plays an hour, but double it in the evening peak, and nothing on the 3rd day.")
    third = (b.start_date + timedelta(days=2)).isoformat()
    fake_ai.default = {"said_anything": True, "confidence": "high", "notes": "Double in the evening; off one day.",
                       "lines": [{"screen_code": "SZR-01", "evidence": "double it in the evening peak",
                                  "parts": [{"name": "Morning peak", "plays_per_hour": 30},
                                            {"name": "daytime", "plays_per_hour": 30},
                                            {"name": "Evening peak", "plays_per_hour": 60},
                                            {"name": "Night", "plays_per_hour": 9999}],
                                  "weekdays": [], "exceptions": [
                                      {"from": third, "to": third, "plays_per_hour": 0, "note": "off"},
                                      {"from": "2020-01-01", "to": "2020-01-02", "plays_per_hour": 0, "note": "x"}]}]}
    plans.draft(s, b)
    s.commit()
    p = b.lines[0].plan
    assert [x["plays"] for x in p["parts"]] == [30, 30, 60, 360]        # clamped to what a 10s ad can play
    assert p["weekdays"] == list(range(7))                                # nothing said: every day
    assert p["exceptions"] == [{"from": third, "to": third, "plays": 0.0, "note": "off"}]   # out-of-range dropped
    assert p["evidence"] == "double it in the evening peak"
    assert b.plan_status == "draft" and b.plan_confidence == "high" and "Double" in b.plan_note
    call = s.scalars(select(AiCall).where(AiCall.purpose == "play_plan")).one()
    assert call.booking_id == b.id and call.client_id == b.client_id
    sent = fake_ai.calls[-1]
    assert "Evening peak: 16:00 to 22:00" in sent["system"]
    assert any("double it in the evening" in blk["text"] for blk in sent["messages"][0]["content"])


def test_without_claude_or_a_conversation_the_draft_is_the_booked_frequency(s, people, inventory):
    b = booking(s, people, inventory, pph=45)
    plans.draft(s, b)
    assert [x["plays"] for x in b.lines[0].plan["parts"]] == [45] * 4 and b.plan_confidence == "medium"
    assert b.plan_note.startswith("No conversation to read")


def test_confirming_a_booking_drafts_its_plan(s, people, inventory):
    from billboard_ops.jobs import run_due
    from test_creatives import jobs_now
    b = booking(s, people, inventory)
    jobs_now(s)
    run_due()
    s.expire_all()
    assert s.get(Booking, b.id).plan_status == "draft"


def test_the_agent_confirms_a_clear_plan_when_allowed(s, people, inventory, fake_ai):
    settings.put(s, "auto_confirm_plan", True)
    b = booking(s, people, inventory, pph=30)
    _with_conversation(s, b, "30 an hour please.")
    fake_ai.default = {"said_anything": False, "confidence": "high", "notes": "Nothing about day parts.", "lines": []}
    plans.draft(s, b)
    assert b.plan_status == "confirmed" and b.plan_confirmed_by == "agent"
    assert s.scalars(select(Activity).where(Activity.action == "confirmed_plan")).one().summary.endswith(
        "automatically (3,780 plays)")       # 18 hours x 30 x 7 days
    b2 = booking(s, people, inventory, pph=30, screen="b")
    _with_conversation(s, b2, "Not sure yet.")
    fake_ai.default = dict(fake_ai.default, confidence="medium")
    plans.draft(s, b2)
    assert b2.plan_status == "draft"                                   # not sure: a person confirms


def test_the_client_approves_the_plan_when_asked_to(s, people, inventory):
    settings.put(s, "client_approves_plan", True)
    b = booking(s, people, inventory)
    plans.draft(s, b)
    plans.confirm(s, people["ops"], b)
    assert b.plan_status == "waiting_client"
    m = s.scalars(select(Message).where(Message.kind == "plan_review")).one()
    assert m.status == "draft" and "https://ops.example.com/portal/bookings/" in m.body and "About 540 plays a day" in m.body
    client = type("U", (), {"email": "sara@acme.example"})()
    with pytest.raises(plans.PlanError, match="what you'd like"):
        plans.client_decide(s, client, b, approve=False, note="")
    plans.client_decide(s, client, b, approve=False, note="Less in the mornings please")
    assert b.plan_status == "draft" and b.plan_client_note == "Less in the mornings please"
    plans.confirm(s, people["ops"], b)
    plans.client_decide(s, client, b, approve=True)
    assert b.plan_status == "confirmed" and b.plan_client_approved_at is not None


def test_changing_the_booking_needs_the_plan_confirmed_again(s, people, inventory):
    b = booking(s, people, inventory)
    plans.draft(s, b)
    plans.confirm(s, people["ops"], b)
    l = b.lines[0]
    svc.update(s, people["manager"], b, [svc.LineIn(l.screen_id, l.start_date, l.end_date + timedelta(days=2),
                                                    ad_seconds=10, plays_per_hour=30, id=l.id)], title="Longer")
    assert b.plan_status == "draft" and b.lines[0].confirmed_plan is None
    plans.confirm(s, people["ops"], b)
    svc.update(s, people["manager"], b, [svc.LineIn(l.screen_id, l.start_date, l.end_date, ad_seconds=10,
                                                    plays_per_hour=30, id=l.id)], title="Just the title")
    svc.update(s, people["manager"], b, [svc.LineIn(l.screen_id, l.start_date, l.end_date, ad_seconds=10,
                                                    plays_per_hour=30, id=l.id)], title="Again")
    assert b.plan_status == "confirmed"                                 # only a title change: still confirmed


# ── The page ────────────────────────────────────────────────────────────────────

def _form(b, values, weekdays=range(7), **extra):
    lid = b.lines[0].id
    data = {"csrf_token": "tok", **{f"p_{lid}_{i}": str(v) for i, v in enumerate(values)},
            f"wd_{lid}": [str(d) for d in weekdays], f"ex_{lid}_from": [""], f"ex_{lid}_to": [""],
            f"ex_{lid}_plays": [""], f"ex_{lid}_note": [""]}
    data.update(extra)
    return data


def test_the_plan_page(app, s, people, inventory, login):
    b = booking(s, people, inventory)
    c = login(people["ops"])
    page = c.get(f"/bookings/{b.code}/plan").get_data(as_text=True)
    assert "Morning peak" in page and "not started" in page and "At most 360 plays an hour" in page
    r = c.post(f"/bookings/{b.code}/plan", data=_form(b, [30, 30, 60, 0], action="save"))
    assert r.status_code == 302
    s.expire_all()
    b = s.get(Booking, b.id)
    assert b.plan_status == "draft" and [p["plays"] for p in b.lines[0].plan["parts"]] == [30, 30, 60, 0]
    r = c.post(f"/bookings/{b.code}/plan", data=_form(b, [30, "lots", 60, 0], action="save"))
    assert r.status_code == 400 and "isn&#39;t a number" in r.get_data(as_text=True)
    r = c.post(f"/bookings/{b.code}/plan", data=_form(b, [30, 30, 60, 0], weekdays=[], action="save"))
    assert r.status_code == 400 and "at least one weekday" in r.get_data(as_text=True)
    lid = b.lines[0].id
    day = (b.start_date + timedelta(days=1)).isoformat()
    data = _form(b, [30, 30, 60, 0], action="confirm")
    data.update({f"ex_{lid}_from": [day], f"ex_{lid}_to": [day], f"ex_{lid}_plays": ["0"], f"ex_{lid}_note": ["Holiday"]})
    r = c.post(f"/bookings/{b.code}/plan", data=data)
    assert r.status_code == 302
    s.expire_all()
    b = s.get(Booking, b.id)
    assert b.plan_status == "confirmed" and b.lines[0].confirmed_plan["exceptions"][0]["note"] == "Holiday"
    assert "Confirmed by ops@example.com" in c.get(f"/bookings/{b.code}/plan").get_data(as_text=True)
    sales = login(people["sales"])
    r = sales.post(f"/bookings/{b.code}/plan", data=_form(b, [30, 30, 30, 30], action="confirm"))
    assert r.status_code == 403


def test_plans_in_plain_words_in_both_languages():
    p = plans.build(parts(), {"Morning peak": 30, "Daytime": 0, "Evening peak": 60, "Night": 0}, weekdays=[4, 5])
    assert plans.describe(p) == "Morning peak 30/h, Evening peak 60/h; Fri, Sat"
    assert plans.describe(p, "ar") == "ذروة الصباح 30 في الساعة، ذروة المساء 60 في الساعة؛ الجمعة، السبت"
