"""Load test (Phase 5): fill an EMPTY database at a multiple of the busiest month, then time the pages and jobs the
team uses most.

    DATABASE_URL=... python -m billboard_ops.loadtest --per-month 300 --months 3 --screens 40

`--per-month` should be twice the client's busiest month of bookings. Each booking gets one to three screens, a
confirmed play plan, approved file versions (without file contents) and the proposal and messages it came from.
"""

import argparse
import random
import statistics
import time
from datetime import date, timedelta

from . import db, runtime
from .config import Config
from .models import (Base, Booking, BookingCounter, BookingLine, Client, Creative, Message, Proposal, Screen, Spec,
                     User, utcnow)


def seed(s, per_month, months, screens_n, today, rnd):
    from . import plans
    if s.query(Screen).count():
        raise SystemExit("The load test fills an empty database only.")
    owner = User(email="owner@example.com", name="Owner", role="owner")
    s.add(owner)
    spec = Spec(name="Landscape FHD", width_px=1920, height_px=1080, durations_s="10,15")
    s.add(spec)
    screens = [Screen(code=f"S-{i:03d}", name=f"Screen {i}", area=f"Area {i % 8}", spec=spec, loop_seconds=120,
                      slot_seconds=10, open_hour=6, close_hour=24, lat=25.0 + rnd.random() / 5,
                      lng=55.1 + rnd.random() / 4) for i in range(screens_n)]
    clients = [Client(name=f"Client {i}", email=f"ads{i}@client{i}.example") for i in range(max(20, per_month // 3))]
    s.add_all(screens + clients)
    s.flush()
    s.add(User(email="client0@client0.example", name="Client 0", role="client", client_id=clients[0].id))
    parts = plans.dayparts(s)
    n = 0
    start0 = today - timedelta(days=30)
    statuses = ["live", "scheduled", "plan_set", "creative_approved", "awaiting_creative", "hold", "completed"]
    for m in range(months):
        for _ in range(per_month):
            n += 1
            start = start0 + timedelta(days=30 * m + rnd.randint(0, 29))
            days = rnd.choice([7, 14, 28])
            client = rnd.choice(clients)
            status = "completed" if start + timedelta(days=days) < today else rnd.choice(statuses[:-1])
            b = Booking(code=f"BK-LT-{n:05d}", client=client, title="Load", status=status,
                        confirmed_at=utcnow() if status != "hold" else None, created_by="owner@example.com",
                        plan_status="confirmed" if status != "hold" else "none",
                        hold_expires_at=utcnow() + timedelta(days=2) if status == "hold" else None)
            s.add(b)
            for sc in rnd.sample(screens, rnd.randint(1, 3)):
                l = BookingLine(booking=b, screen=sc, start_date=start, end_date=start + timedelta(days=days - 1),
                                ad_seconds=10, plays_per_hour=rnd.choice([15, 30, 30, 60]))
                plan = plans.build(parts, [l.plays_per_hour, l.plays_per_hour / 2, l.plays_per_hour * 1.5, 0])
                l.plan = l.confirmed_plan = plan if status != "hold" else None
                s.add(l)
            s.flush()
            for v in range(rnd.randint(1, 3)):
                s.add(Creative(booking_id=b.id, version=v + 1, filename=f"ad-v{v + 1}.mp4", source="upload",
                               line_ids=[l.id for l in b.lines], status="approved" if v == 0 else "superseded",
                               results={"targets": [], "content": [], "verdict": "passed"}, checked_at=utcnow()))
            p = Proposal(ref=f"PR-LT{n:05d}", channel="email", sender=client.email, client_id=client.id,
                         booking_id=b.id, status="held", read_count=1)
            s.add(p)
            s.flush()
            s.add(Message(proposal_id=p.id, direction="in", channel="email", body="Please book " * 40,
                          status="received"))
            s.add(Message(proposal_id=p.id, booking_id=b.id, direction="out", channel="email", kind="hold_confirmation",
                          status="sent", body="Held " * 40, sent_at=utcnow() - timedelta(days=5)))
        s.flush()
    s.add(BookingCounter(year=today.year, last=n))
    return n


def timed(fn, runs=3):
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return statistics.median(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-month", type=int, default=300)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--screens", type=int, default=40)
    args = ap.parse_args(argv)
    cfg = Config()
    cfg.LOGIN_REQUIRED = True
    runtime.init(cfg)
    from . import create_app
    app = create_app(cfg)
    if cfg.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(db.engine())
    today = date.today()
    t = time.perf_counter()
    with db.session_scope() as s:
        n = seed(s, args.per_month, args.months, args.screens, today, random.Random(5))
    print(f"Seeded {n:,} bookings on {args.screens} screens in {time.perf_counter() - t:.1f}s.")
    with db.session_scope() as s:
        owner = s.query(User).filter_by(email="owner@example.com").one().id
        client_user = s.query(User).filter_by(role="client").first().id
        code = s.query(Booking).filter_by(status="live").first().code
    team, portal = app.test_client(), app.test_client()
    with team.session_transaction() as sess:
        sess["uid"] = owner
    with portal.session_transaction() as sess:
        sess["uid"] = client_user
    pages = [("Dashboard", "/"), ("Calendar (month)", "/calendar"), ("Bookings", "/bookings?status=open"),
             ("A booking", f"/bookings/{code}"), ("Play plan", f"/bookings/{code}/plan"),
             ("Playlist, one day, all screens", f"/playlists?from={today}&days=1"), ("Creative", "/creatives"),
             ("Performance", "/performance"), ("Weekly summary", "/summary"), ("Costs", "/settings/costs")]
    print(f"{'What':40} {'median ms':>10}")
    for label, url in pages:
        def get(url=url):
            r = team.get(url)
            assert r.status_code == 200, (url, r.status_code)
        print(f"{label:40} {timed(get):10.0f}")
    print(f"{'Client portal home':40} {timed(lambda: portal.get('/portal')):10.0f}")
    from . import lifecycle
    with db.session_scope() as s:
        print(f"{'Worker: the once-a-minute stage check':40} {timed(lambda: lifecycle.tick(s, today), 1):10.0f}")
        s.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
