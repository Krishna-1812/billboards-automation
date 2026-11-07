"""Demo data for showing Phase 1: `python -m billboard_ops.demo`.

Only runs on an empty database (no screens yet), so it can never mix with the client's real records.
"""

import random
from datetime import date, timedelta

from sqlalchemy import func, select

from . import bookings as svc
from . import db, runtime
from .config import Config
from .models import Base, Blackout, Client, HoursOverride, Screen, Spec, User

SCREENS = [
    ("SZR-01", "Sheikh Zayed Rd, Mall of the Emirates", "Sheikh Zayed Rd", 25.1181, 55.2006, "land"),
    ("SZR-02", "Sheikh Zayed Rd, Business Bay", "Sheikh Zayed Rd", 25.1866, 55.2626, "land"),
    ("SZR-03", "Sheikh Zayed Rd, Trade Centre", "Sheikh Zayed Rd", 25.2253, 55.2843, "land"),
    ("MAR-01", "Marina Walk", "Dubai Marina", 25.0805, 55.1403, "port"),
    ("MAR-02", "JBR The Walk", "Dubai Marina", 25.0780, 55.1338, "port"),
    ("DXB-01", "Airport Road Arrivals", "Airport", 25.2486, 55.3523, "land"),
    ("DT-01", "Downtown Boulevard", "Downtown", 25.1972, 55.2744, "port"),
    ("DT-02", "Dubai Mall Fashion Ave", "Downtown", 25.1985, 55.2796, "land"),
]
CLIENTS = ["Acme Foods", "Gulf Motors", "Noor Bank", "Sahara Telecom", "Bloom Cosmetics", "Desert Air"]


def seed(s, owner_email="owner@example.com"):
    if s.scalar(select(func.count()).select_from(Screen)):
        raise SystemExit("There are already screens; the demo only loads into an empty database.")
    owner = s.scalars(select(User).where(User.email == owner_email)).first()
    if owner is None:
        owner = User(email=owner_email, name="Owner", role="owner")
        s.add(owner)
    land = Spec(name="Landscape FHD", width_px=1920, height_px=1080, durations_s="10,15", max_file_mb=100)
    port = Spec(name="Portrait 4K", width_px=2160, height_px=3840, durations_s="10", max_file_mb=200)
    s.add_all([land, port])
    screens = []
    for code, name, area, lat, lng, kind in SCREENS:
        sc = Screen(code=code, name=name, area=area, lat=lat, lng=lng, spec=land if kind == "land" else port,
                    loop_seconds=120 if kind == "land" else 60, slot_seconds=10,
                    open_hour=6 if code != "MAR-02" else 16, close_hour=24 if code != "MAR-02" else 2)
        s.add(sc)
        screens.append(sc)
    clients = [Client(name=n) for n in CLIENTS]
    s.add_all(clients)
    s.flush()
    today = date.today()
    s.add(Blackout(screen_id=screens[5].id, start_date=today + timedelta(days=9),
                   end_date=today + timedelta(days=10), reason="LED panel maintenance"))
    s.add(HoursOverride(screen_id=None, start_date=today + timedelta(days=40), end_date=today + timedelta(days=69),
                        open_hour=8, close_hour=2, reason="Holiday month: late hours"))
    rnd = random.Random(7)
    for _ in range(36):
        sc = rnd.choice(screens)
        start = today + timedelta(days=rnd.randint(-10, 70))
        li = svc.LineIn(screen_id=sc.id, start_date=start, end_date=start + timedelta(days=rnd.choice([6, 13, 27])),
                        ad_seconds=10, plays_per_hour=sc.plays_per_hour_per_slot * rnd.choice([1, 1, 2, 3]))
        if rnd.random() < 0.2:
            li.start_hour, li.end_hour = 17, 23
        try:
            svc.create(s, owner, rnd.choice(clients), [li], title=rnd.choice(["Launch", "Summer", "Brand", "Offer"]),
                       status="confirmed" if rnd.random() < 0.7 else "hold")
        except svc.Overbooked:
            continue
    return len(screens)


def main():
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(db.engine())
    with db.session_scope() as s:
        n = seed(s, (cfg.OWNER_EMAILS or ["owner@example.com"])[0])
    print(f"Loaded {n} demo screens with bookings.")


if __name__ == "__main__":
    main()
