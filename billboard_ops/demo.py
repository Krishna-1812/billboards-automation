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
    seed_proposals(s, screens, today)
    return len(screens)


def _raw(client, lines, missing=(), questions=(), language="en", conf="high"):
    return {"is_booking_request": True, "language": language,
            "client": {"value": client, "confidence": conf, "evidence": client or ""},
            "contact_name": None, "contact_email": None, "contact_phone": None, "campaign": "Demo campaign",
            "lines": lines, "budget_amount": None, "budget_currency": None, "notes": "", "missing": list(missing),
            "questions": list(questions), "flags": []}


def _line(codes, start, end, unit="slots", value=2, conf="high", hint=None, evidence=""):
    return {"screen_codes": codes, "screen_hint": hint, "screen_confidence": conf, "alternatives": [],
            "start_date": start.isoformat() if start else None, "end_date": end.isoformat() if end else None,
            "dates_confidence": conf, "start_hour": None, "end_hour": None, "ad_seconds": None,
            "frequency_unit": unit, "frequency_value": value, "frequency_confidence": conf, "evidence": evidence}


def seed_proposals(s, screens, today):
    """Three sample requests, already 'read' (no Claude key needed), to show the Proposals inbox."""
    from . import extract, intake
    d = lambda n: today + timedelta(days=n)  # noqa: E731
    samples = [
        ("email", "marketing@gulfmotors.example", "Huda", "Booking request: SZR screens in December",
         "Hi team,\n\nWe'd like two slots on SZR-01 and SZR-03 from "
         f"{d(30):%d %B} for two weeks for the new SUV launch.\n\nThanks,\nHuda, Gulf Motors",
         _raw("Gulf Motors", [_line(["SZR-01", "SZR-03"], d(30), d(43), evidence="two slots on SZR-01 and SZR-03")])),
        ("whatsapp", "+971500000123", "Omar", "",
         "السلام عليكم، نرغب بحجز شاشة مارينا ووك لحملة رمضان. كم السعر؟",
         _raw("Noor Bank", [_line(["MAR-01"], None, None, unit="unknown", value=None, conf="medium",
                                   evidence="شاشة مارينا ووك")], missing=["dates", "frequency"],
              questions=["ما هي تواريخ بداية ونهاية الحملة؟", "كم مرة ترغبون بعرض الإعلان في الساعة؟"],
              language="ar", conf="medium")),
        ("manual", "", "Lina", "Phone call with Bloom",
         "Bloom Cosmetics called: they want 'the big screen near the airport' plus the Dubai Mall one, "
         f"{d(12):%d %b} to {d(25):%d %b}, about 40 plays an hour.",
         _raw("Bloom Cosmetics", [_line(["DT-02"], d(12), d(25), unit="plays_per_hour", value=40),
                                  _line([], d(12), d(25), unit="plays_per_hour", value=40, conf="low",
                                        hint="the big screen near the airport")], missing=["screens"],
              questions=["Do you mean the Airport Road Arrivals screen (DXB-01)?"])),
    ]
    for channel, sender, name, subject, body, raw in samples:
        p, _, _ = intake.receive(s, channel=channel, sender=sender, sender_name=name, subject=subject, body=body)
        draft = extract.normalise(s, raw, today, sender)
        p.reading, p.draft, p.language = raw, draft, draft["language"]
        p.client_id, p.ready, p.read_count = draft["client_id"], draft["ready"], 1
        p.checks = intake.check(s, draft["lines"], today)
        p.status = "needs_review"
        if draft["missing"]:
            intake._draft_clarify(s, p)


def main():
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(db.engine())
    from . import evaluate, intake, mail, outbox, sync  # noqa: F401 - job handlers
    with db.session_scope() as s:
        n = seed(s, (cfg.OWNER_EMAILS or ["owner@example.com"])[0])
    print(f"Loaded {n} demo screens with bookings.")


if __name__ == "__main__":
    main()
