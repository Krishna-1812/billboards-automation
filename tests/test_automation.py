"""Phase 4: what the agent does on its own when the owner allows it, the Automation settings page, each person's
notice choices, and the client's preferred channel."""

from sqlalchemy import select

from billboard_ops import creatives, intake, settings
from billboard_ops.models import Activity, Message, Proposal

from media import media, read  # noqa: F401
from test_creatives import ai_reads_ads, confirmed, small  # noqa: F401
from test_creative_checks import answer as content_answer


def form(**kw):
    return {"csrf_token": "tok", **kw}


def test_everything_asks_a_person_by_default(s, people, small, media, ai_reads_ads):
    b = confirmed(s, people, small)
    m = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    assert m.status == "draft"
    c = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c)
    assert c.status == "passed" and b.plan_status == "draft"


def test_routine_client_messages_go_out_on_their_own(s, people, small):
    settings.put(s, "auto_send_client", True)
    s.commit()
    confirmed(s, people, small)
    m = s.scalars(select(Message).where(Message.kind == "creative_request")).one()
    assert m.status == "approved" and m.approved_by == "agent"
    log = s.scalars(select(Activity).where(Activity.action == "sent_automatically")).one()
    assert log.actor == "agent" and "automatically" in log.summary


def test_a_passed_file_is_approved_by_the_agent(s, people, small, media, ai_reads_ads):
    settings.put(s, "auto_approve_passed", True)
    b = confirmed(s, people, small)
    c = creatives.add_file(s, b, filename="ad.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c)
    assert c.status == "approved" and c.decided_by == "agent" and b.status == "creative_approved"
    assert "approved automatically" in s.scalars(select(Activity).where(Activity.action == "approved_file")).one().summary
    # A file with something for a person to look at still waits.
    ai_reads_ads.default = lambda kw: content_answer(brand_match="unclear")
    c2 = creatives.add_file(s, b, filename="ad2.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c2)
    assert c2.status == "warnings"


def test_content_feedback_goes_out_only_when_allowed(s, people, small, media, ai_reads_ads):
    ai_reads_ads.default = lambda kw: content_answer(texts=[{"frame": 1, "text": "Sale", "language": "english",
                                                              "other_language": None, "placement": "inside",
                                                              "size": "large"}], language_verdict="english_only")
    settings.put(s, "auto_send_technical", True)
    b = confirmed(s, people, small)
    c = creatives.add_file(s, b, filename="en.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c)
    fb = s.scalars(select(Message).where(Message.kind == "creative_feedback")).one()
    assert c.status == "failed" and fb.status == "draft"                 # a content finding: a person decides
    settings.put(s, "auto_send_content", True)
    c2 = creatives.add_file(s, b, filename="en2.mp4", content_type="video/mp4", data=read(media["good.mp4"]))
    creatives.check(s, c2)
    fb2 = s.scalars(select(Message).where(Message.kind == "creative_feedback", Message.status != "discarded")
                    .order_by(Message.id.desc())).first()
    assert fb2.status == "approved" and fb2.approved_by == "agent"


def test_the_automation_page(app, s, people, login):
    c = login(people["owner"])
    page = c.get("/settings/automation").get_data(as_text=True)
    for label in ("Holds", "Client messages", "Technical feedback", "Content feedback", "Final approval", "Play plans"):
        assert f"<strong>{label}</strong>" in page
    r = c.post("/settings/automation", data=form(auto_send_client="auto", auto_confirm_plan="auto",
                                                 client_approves_plan="1", renewal_days="10",
                                                 dayparts="Day = 6-18\nNight = 18-6",
                                                 load_filename="{booking}-{screen}{ext}"))
    assert r.status_code == 302
    s.expire_all()
    assert settings.get(s, "auto_send_client") is True and settings.get(s, "auto_confirm_plan") is True
    assert settings.get(s, "auto_hold") is False and settings.get(s, "client_approves_plan") is True
    assert settings.get(s, "renewal_days") == 10 and settings.get(s, "dayparts") == "Day = 6-18\nNight = 18-6"
    r = c.post("/settings/automation", data=form(dayparts="Day = 6-18", load_filename="{booking}"))
    text = r.get_data(as_text=True)
    assert r.status_code == 400 and "aren&#39;t in any day part" in text and "must end with {ext}" in text
    assert login(people["manager"]).get("/settings/automation").status_code == 403


def test_my_notice_choices(app, s, people, login):
    c = login(people["sales"])
    page = c.get("/notices/settings").get_data(as_text=True)
    assert "Renewals" in page and "Client actions" in page
    c.post("/notices/settings", data=form(app_files="1", email_renewals="1"))
    s.expire_all()
    prefs = s.get(type(people["sales"]), people["sales"].id).notify_prefs
    assert prefs["files"] == {"app": True, "email": False} and prefs["plans"] == {"app": False, "email": False}
    assert prefs["renewals"] == {"app": False, "email": True}


def test_the_clients_preferred_channel(s, people, small):
    client = small["client"]
    client.whatsapp, client.preferred_channel = "+971500000001", "whatsapp"
    b = confirmed(s, people, small)
    who = intake.client_contact(s, b)
    assert who["channel"] == "whatsapp" and who["to"] == "+971500000001"
    p = Proposal(channel="email", sender="sara@acme.example", client_id=client.id, booking_id=b.id, status="held")
    s.add(p)
    s.commit()
    who = intake.client_contact(s, b)
    assert who["channel"] == "whatsapp" and who["reply"] == ""            # their preference wins over the thread
    client.preferred_channel = ""
    assert intake.client_contact(s, b)["channel"] == "email"
