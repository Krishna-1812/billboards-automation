"""Demo data for showing the app: `python -m billboard_ops.demo`.

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
RATES = {"SZR-01": 28000, "SZR-02": 24000, "SZR-03": 22000, "MAR-01": 14000, "MAR-02": 12000, "DXB-01": 18000,
         "DT-01": 16000, "DT-02": 30000}     # one slot, one week, in AED (made-up figures for the demo)
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
                    loop_seconds=120 if kind == "land" else 60, slot_seconds=10, rate_week=RATES.get(code),
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
    seed_campaigns(s, owner, screens, clients, today)
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
    seed_quotes(s, owner, screens, clients, today)
    return len(screens)


def _price(b):
    from . import pricing
    return sum(pricing.list_price(l.screen, l.start_date, l.end_date, l.start_hour, l.end_hour, l.ad_seconds,
                                  l.plays_per_hour, l.exclusive) or 0 for l in b.lines)


def seed_quotes(s, owner, screens, clients, today):
    """Proposals at each step: one waiting for the client, one where they asked for changes and got a second
    version, and one accepted that waits for its purchase order."""
    from . import quotes
    from .models import Proposal
    d = lambda n: today + timedelta(days=n)  # noqa: E731
    by = {sc.code: sc for sc in screens}
    gulf_request = s.scalars(select(Proposal).where(Proposal.sender == "marketing@gulfmotors.example")).first()
    if gulf_request is not None:
        q = quotes.draft_from_request(s, owner, gulf_request)
        q.budget, q.message = 160000, "Thank you for thinking of us for the SUV launch. Option B swaps in the free screens."
        quotes.send(s, owner, q)

    sahara = clients[3]
    q = quotes.create(s, owner, sahara, "5G launch")
    line = lambda code, a, b, pph=30: {"screen_id": by[code].id, "start_date": d(a), "end_date": d(b),  # noqa: E731
                                       "ad_seconds": 10, "plays_per_hour": pph}
    quotes.add_option(s, q, "Option A: Sheikh Zayed Rd", [line("SZR-01", 20, 33), line("SZR-02", 20, 33)])
    quotes.add_option(s, q, "Option B: Downtown and Marina", [line("DT-02", 20, 33), line("MAR-01", 20, 33, 12)])
    s.flush()
    for o in q.options:
        s.refresh(o)
    quotes.send(s, owner, q)
    quotes.ask_changes(s, "ads@sahara.example", q, "Option A looks right. Can you do 10% off and add a bonus week?")
    q.options[0].discount_pct = 10
    q.options[0].value_adds = "A bonus week on SZR-02 after the campaign"
    quotes.send(s, owner, q, note="10% off Option A, bonus week on SZR-02")
    quotes.ask_changes(s, "ads@sahara.example", q, "Thanks. Could Option B start a week later?")

    acme = clients[0]
    q = quotes.create(s, owner, acme, "Summer menu")
    q.message = "Two ways to run the summer menu. Option B adds the Marina screens in the evenings."
    quotes.add_option(s, q, "Option A: Downtown", [line("DT-02", 40, 53), line("DT-01", 40, 53)])
    quotes.add_option(s, q, "Option B: Downtown and Marina", [
        line("DT-02", 40, 53), {"screen_id": by["MAR-02"].id, "start_date": d(40), "end_date": d(53),
                                "ad_seconds": 10, "plays_per_hour": 60, "start_hour": 18, "end_hour": 24}],
        discount_pct=8, value_adds="Weekly proof-of-play photos")
    s.flush()
    for o in q.options:
        s.refresh(o)
    quotes.send(s, owner, q)

    desert = clients[5]
    q = quotes.create(s, owner, desert, "Summer fares")
    quotes.add_option(s, q, "Option A: Airport", [line("DXB-01", 25, 38, 30)], discount_pct=5,
                      value_adds="Proof of play photos every week")
    s.flush()
    s.refresh(q.options[0])
    quotes.send(s, owner, q)
    try:
        quotes.accept(s, owner, q, q.options[0])
    except svc.Overbooked:
        pass


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


def _picture(text, size=(1920, 1080), colour=(18, 94, 160)):
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(img)
    d.rectangle([size[0] * 0.08, size[1] * 0.3, size[0] * 0.92, size[1] * 0.7], outline="white", width=12)
    d.text((size[0] * 0.12, size[1] * 0.45), text, fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def seed_campaigns(s, owner, screens, clients, today):
    """Phase 4: a campaign that is live with proof, one starting tomorrow that isn't ready, one whose play plan
    waits for the client, and a portal login for Acme Foods (sara@acme.example)."""
    from . import creatives, lifecycle, plans
    from .models import Creative, Proof
    acme, gulf, noor = clients[0], clients[1], clients[2]
    acme.email, acme.contact_name = "ads@acme.example", "Sara"
    s.add(User(email="sara@acme.example", name="Sara", role="client", client_id=acme.id))

    def book(client, scr, start, days, pph, title):
        b = svc.create(s, owner, client, [svc.LineIn(x.id, start, start + timedelta(days=days - 1), ad_seconds=10,
                                                     plays_per_hour=pph) for x in scr], title=title)
        b.agreed_amount, b.currency = _price(b), "AED"
        svc.confirm(s, owner, b, po_number=f"PO-{client.name[:4].upper()}-{b.id:03d}", po_amount=b.agreed_amount)
        s.flush()
        return b

    def approve_file(b, name):
        c = Creative(booking=b, version=1, filename=name, content_type="image/png", source="portal",
                     data=_picture(f"{b.client.name}: {b.title}"), line_ids=[l.id for l in b.lines], status="passed",
                     results={"targets": [], "content": [], "verdict": "passed"},
                     team_summary=f"v1 {name} for {b.code}: passes every check.")
        c.size = len(c.data)
        s.add(c)
        s.flush()
        creatives.approve(s, owner, c)
        return c

    live = book(acme, screens[:2], today - timedelta(days=3), 21, 30, "Spring menu")
    approve_file(live, "acme-spring.png")
    parts = plans.dayparts(s)
    plan = plans.build(parts, {"Morning peak": 30, "Daytime": 20, "Evening peak": 60, "Night": 10})
    plans.save(s, owner, live, {l.id: plan for l in live.lines})
    plans.confirm(s, owner, live)
    for l in live.lines:
        lifecycle.mark_loaded(s, owner, live, l)
    s.add(Proof(booking_id=live.id, line_id=live.lines[0].id, kind="photo", filename="szr01-evening.png",
                content_type="image/png", data=_picture("SZR-01 at 20:10", colour=(30, 30, 30)), taken_on=today,
                note="Evening peak", uploaded_by=owner.email))
    soon = book(gulf, [screens[2]], today + timedelta(days=1), 14, 30, "SUV launch")
    approve_file(soon, "gulf-suv.png")
    plans.draft(s, soon)
    waiting = book(noor, [screens[7]], today + timedelta(days=12), 14, 30, "Ramadan")
    plans.draft(s, waiting)
    from . import settings
    settings.put(s, "client_approves_plan", True)
    plans.confirm(s, owner, waiting)
    settings.put(s, "client_approves_plan", False)
    lifecycle.tick(s, on=today)


def main():
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    if cfg.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(db.engine())
    from . import evaluate, intake, loading, mail, outbox, plans, sync  # noqa: F401 - job handlers
    with db.session_scope() as s:
        n = seed(s, (cfg.OWNER_EMAILS or ["owner@example.com"])[0])
    print(f"Loaded {n} demo screens with bookings.")


if __name__ == "__main__":
    main()
