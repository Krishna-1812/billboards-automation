"""Placing, changing, confirming and cancelling bookings, and the worker's hold housekeeping."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from billboard_ops import bookings as svc
from billboard_ops import mail, vacancy
from billboard_ops.jobs import run_due
from billboard_ops.models import Activity, Job, Notification, utcnow
from billboard_ops.permissions import CAN, Forbidden, can

from conftest import future


def line(screen, start=None, end=None, slots=1, **kw):
    start = start or future(10)
    return svc.LineIn(screen_id=screen.id, start_date=start, end_date=end or start + timedelta(days=6),
                      plays_per_hour=slots * screen.plays_per_hour_per_slot, ad_seconds=screen.slot_seconds, **kw)


def fill(s, people, inv, screen, slots=12, start=None):
    return svc.create(s, people["manager"], inv["client"], [line(screen, start=start, slots=slots)], status="confirmed")


def test_a_hold_gets_a_code_an_expiry_a_log_and_sync_jobs(s, people, inventory):
    b = svc.create(s, people["sales"], inventory["client"], [line(inventory["a"])], title="Ramadan")
    s.commit()
    assert b.code == f"BK-{date.today().year}-0001" and b.status == "hold"
    assert timedelta(days=2, hours=23) < b.hold_expires_at - utcnow() <= timedelta(days=3)
    b2 = svc.create(s, people["sales"], inventory["client"], [line(inventory["b"])])
    assert b2.code.endswith("-0002")
    assert s.scalars(select(Activity).where(Activity.entity_id == b.code)).first().action == "created_hold"
    kinds = sorted(j.kind for j in s.scalars(select(Job)))
    assert kinds.count("calendar_line") == 2 and kinds.count("sheet_mirror") == 1   # the mirror is de-duplicated


def test_overbooking_is_refused_then_allowed_for_a_manager_with_a_reason(s, people, inventory):
    a = inventory["a"]
    fill(s, people, inventory, a)
    with pytest.raises(svc.Overbooked) as e:
        svc.create(s, people["sales"], inventory["client"], [line(a)])
    assert e.value.results[0][2].status == "no"
    with pytest.raises(Forbidden):
        svc.create(s, people["sales"], inventory["client"], [line(a)], override_reason="client is VIP")
    with pytest.raises(svc.Overbooked):
        svc.create(s, people["manager"], inventory["client"], [line(a)], override_reason="  ")
    b = svc.create(s, people["manager"], inventory["client"], [line(a)], override_reason="Owner agreed")
    assert b.override_reason == "Owner agreed"
    assert s.scalars(select(Notification).where(Notification.kind == "warning")).first() is not None


def test_only_managers_confirm(s, people, inventory):
    b = svc.create(s, people["sales"], inventory["client"], [line(inventory["a"])])
    with pytest.raises(Forbidden):
        svc.confirm(s, people["sales"], b, po_number="PO-1")
    svc.confirm(s, people["manager"], b, po_number="PO-1")
    assert b.status == "confirmed" and b.confirmed_by == "manager@example.com" and b.hold_expires_at is None
    with pytest.raises(svc.BookingError):
        svc.confirm(s, people["manager"], b, po_number="PO-1")
    with pytest.raises(Forbidden):
        svc.create(s, people["sales"], inventory["client"], [line(inventory["b"])], status="confirmed")


def test_editing_a_booking_does_not_clash_with_itself(s, people, inventory):
    a = inventory["a"]
    b = fill(s, people, inventory, a, slots=12)
    lines = [line(a, start=b.lines[0].start_date, end=b.lines[0].end_date + timedelta(days=2), slots=12,
                  id=b.lines[0].id)]
    svc.update(s, people["manager"], b, lines)
    assert b.lines[0].end_date == b.lines[0].start_date + timedelta(days=8)


def test_taking_a_screen_out_removes_its_calendar_event(s, people, inventory):
    b = svc.create(s, people["sales"], inventory["client"], [line(inventory["a"]), line(inventory["b"])])
    s.flush()
    b.lines[1].calendar_event_id = "ev-old"
    keep = b.lines[0]
    svc.update(s, people["sales"], b, [line(inventory["a"], start=keep.start_date, end=keep.end_date, id=keep.id)])
    deletes = [j for j in s.scalars(select(Job).where(Job.kind == "calendar_delete"))]
    assert len(b.lines) == 1 and deletes[0].payload == {"calendar_id": "cal-szr02", "event_id": "ev-old"}


def test_moving_a_line_to_another_screen_moves_its_calendar_event(s, people, inventory):
    b = svc.create(s, people["sales"], inventory["client"], [line(inventory["a"])])
    s.flush()
    old = b.lines[0]
    old.calendar_event_id = "ev-a"
    svc.update(s, people["sales"], b, [line(inventory["b"], start=old.start_date, end=old.end_date, id=old.id)])
    assert b.lines[0].screen_id == inventory["b"].id and b.lines[0].calendar_event_id == ""
    deletes = [j.payload for j in s.scalars(select(Job).where(Job.kind == "calendar_delete"))]
    assert deletes == [{"calendar_id": "cal-szr01", "event_id": "ev-a"}]


def test_cancelling_frees_the_time(s, people, inventory):
    a = inventory["a"]
    b = fill(s, people, inventory, a)
    with pytest.raises(svc.Overbooked):
        svc.create(s, people["sales"], inventory["client"], [line(a)])
    with pytest.raises(Forbidden):
        svc.cancel(s, people["sales"], b, "client pulled out")     # confirmed: managers only
    with pytest.raises(svc.BookingError):
        svc.cancel(s, people["manager"], b, "")
    svc.cancel(s, people["manager"], b, "client pulled out")
    assert svc.create(s, people["sales"], inventory["client"], [line(a)]).status == "hold"


def test_validation_messages(s, people, inventory):
    bad = svc.LineIn(screen_id=inventory["a"].id, start_date=future(5), end_date=future(3), start_hour=6)
    with pytest.raises(svc.BookingError) as e:
        svc.create(s, people["sales"], inventory["client"], [bad])
    assert "end date is before" in str(e.value) and "both a start and an end hour" in str(e.value)
    with pytest.raises(svc.BookingError):
        svc.create(s, people["sales"], inventory["client"], [])


def test_extend_hold(s, people, inventory):
    b = svc.create(s, people["sales"], inventory["client"], [line(inventory["a"])])
    before = b.hold_expires_at
    b.hold_reminded_at = utcnow()
    svc.extend_hold(s, people["sales"], b, 2)
    assert b.hold_expires_at - before == timedelta(days=2) and b.hold_reminded_at is None


def test_expiry_and_reminders(s, people, inventory):
    a = inventory["a"]
    b = svc.create(s, people["sales"], inventory["client"], [line(a, slots=12)])
    s.commit()
    now = utcnow()
    # A day before expiry: one reminder, to managers, the owner and whoever placed it.
    assert svc.remind_holds(s, b.hold_expires_at - timedelta(hours=20)) == [b]
    assert svc.remind_holds(s, b.hold_expires_at - timedelta(hours=19)) == []
    email = s.scalars(select(Job).where(Job.kind == "send_email")).first()
    assert set(email.payload["to"]) == {"owner@example.com", "manager@example.com", "sales@example.com"}
    # Not yet expired...
    assert svc.expire_holds(s, now) == []
    # ...then expired, and its time is free again.
    assert svc.expire_holds(s, b.hold_expires_at + timedelta(seconds=1)) == [b]
    assert b.status == "expired"
    assert vacancy.check_screen(s, a, line(a, slots=12).request()).status == "fits"


def test_finished_campaigns_complete(s, people, inventory):
    b = fill(s, people, inventory, inventory["a"], slots=1, start=future(1))
    assert svc.complete_finished(s, future(7)) == []
    assert svc.complete_finished(s, future(8)) == [b] and b.status == "completed"


def test_emails_without_smtp_are_kept_not_sent(s, people, inventory, app):
    mail.queue(s, ["a@example.com"], "Hello", "Body")
    s.commit()
    run_due()
    s.expire_all()
    j = s.scalars(select(Job).where(Job.kind == "send_email")).first()
    assert j.status == "skipped" and "SMTP" in j.last_error and mail._sent[0]["subject"] == "Hello"


def test_permission_table():
    class U:
        def __init__(self, role, active=True):
            self.role, self.active = role, active
    assert can(U("owner"), "settings") and not can(U("manager"), "settings")
    assert can(U("sales"), "hold") and not can(U("sales"), "confirm") and not can(U("ops"), "hold")
    assert can(U("ops"), "edit_screens") and not can(U("sales"), "edit_screens")
    assert not can(U("client"), "view") and not can(U("owner", active=False), "view") and not can(None, "view")
    assert all(set(roles) <= {"owner", "manager", "sales", "ops"} for roles in CAN.values())


@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="needs Postgres row locks")
def test_two_people_cannot_both_take_the_last_slot(s, people, inventory):
    import threading
    from billboard_ops import db
    a = inventory["a"]
    svc.create(s, people["manager"], inventory["client"], [line(a, slots=11)], status="confirmed")
    s.commit()
    first_holds_lock, outcome = threading.Event(), {}

    def first():
        s1 = db.new_session()
        try:
            svc.create(s1, s1.get(type(people["sales"]), people["sales"].id), s1.get(type(inventory["client"]),
                       inventory["client"].id), [line(a)])
            first_holds_lock.set()
            threading.Event().wait(0.5)          # keep the lock a moment while the second person tries
            s1.commit()
        finally:
            s1.close()

    def second():
        first_holds_lock.wait(5)
        s2 = db.new_session()
        try:
            svc.create(s2, s2.get(type(people["manager"]), people["manager"].id),
                       s2.get(type(inventory["client"]), inventory["client"].id), [line(a)])
            s2.commit()
            outcome["second"] = "booked"
        except svc.Overbooked:
            outcome["second"] = "refused"
        finally:
            s2.close()

    t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
    t1.start(), t2.start()
    t1.join(10), t2.join(10)
    assert outcome == {"second": "refused"}
