"""Reading proposals: what is sent to Claude, how its answer is checked, and the cost ledger."""

import io
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from billboard_ops import ai, documents, extract
from billboard_ops.models import AiCall, Attachment

from conftest import ai_answer, ai_line, future


def test_the_answer_schema_meets_structured_output_rules():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        for key in ("items",):
            if key in node:
                walk(node[key])
        for alt in node.get("anyOf", []):
            walk(alt)
        assert not {"minimum", "maximum", "minLength", "maxLength"} & set(node)
    walk(extract.SCHEMA)


def msg(body, attachments=(), when=None):
    from datetime import datetime
    return SimpleNamespace(direction="in", status="received", body=body, created_at=when or datetime(2026, 11, 7, 9),
                           attachments=list(attachments))


def test_request_shape(s, inventory, fake_ai):
    extract.read(s, messages=[msg("Two slots on SZR-01 from 1 Dec for a week please")], channel="email",
                 sender="ads@acme.example", subject="Booking", today=date(2026, 11, 7))
    kw = fake_ai.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kw and "thinking" not in kw
    system = kw["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert "SZR-01 | Sheikh Zayed Rd North | SZR" in system["text"] and "Acme Foods" in system["text"]
    assert "2026-11-07" not in system["text"]          # the date changes daily; it stays out of the cached part
    content = kw["messages"][0]["content"]
    assert "TODAY: Saturday 07 November 2026" in content[0]["text"]
    assert "Two slots on SZR-01" in content[1]["text"]


def test_every_call_is_costed(s, inventory, fake_ai):
    extract.read(s, messages=[msg("x")], channel="email", sender="", subject="", today=date(2026, 11, 7),
                 actor="sales@example.com", client_id=inventory["client"].id)
    s.flush()
    row = s.scalars(select(AiCall)).one()
    # 2000 in at $4, 500 out at $20, 1000 cache reads at $0.20 (per million)
    assert row.usd == pytest.approx((2000 * 4 + 500 * 20 + 1000 * 0.20) / 1e6)
    assert (row.actor, row.client_id, row.purpose, row.ok) == ("sales@example.com", inventory["client"].id,
                                                               "read_proposal", True)


def test_a_declined_or_cut_off_answer_is_logged_and_raised(s, inventory, fake_ai):
    fake_ai.stop_reason = "refusal"
    with pytest.raises(ai.AIFailed, match="declined"):
        extract.read(s, messages=[msg("x")], channel="email", sender="", subject="", today=date(2026, 11, 7))
    fake_ai.stop_reason = "max_tokens"
    with pytest.raises(ai.AIFailed, match="cut off"):
        extract.read(s, messages=[msg("x")], channel="email", sender="", subject="", today=date(2026, 11, 7))
    s.flush()
    assert [r.ok for r in s.scalars(select(AiCall))] == [False, False]


def test_network_trouble_is_retryable(s, inventory, fake_ai):
    import anthropic
    import httpx2
    fake_ai.error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(ai.AITransient):
        extract.read(s, messages=[msg("x")], channel="email", sender="", subject="", today=date(2026, 11, 7))


def test_without_a_key_nothing_is_called(s, inventory, app):
    with pytest.raises(ai.AIUnavailable):
        extract.read(s, messages=[msg("x")], channel="email", sender="", subject="", today=date(2026, 11, 7))


# ── Attachments ─────────────────────────────────────────────────────────────────

def _pdf(text):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in text.splitlines():
        c.drawString(40, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def test_attachments_become_text_documents_or_images():
    pdf = Attachment(filename="brief.pdf", content_type="application/pdf",
                     data=_pdf("Campaign brief\n" + "Screens SZR-01 and SZR-02, 1 to 14 December, two slots each.\n" * 3))
    pdf.size = len(pdf.data)
    blocks = documents.blocks_for(pdf, "Attachment 1.1")
    assert blocks[0]["type"] == "text" and "1 to 14 December" in blocks[0]["text"]

    scan = Attachment(filename="scan.pdf", content_type="application/pdf", data=_pdf(""))
    scan.size = len(scan.data)
    blocks = documents.blocks_for(scan, "Attachment 1.2")
    assert blocks[1]["type"] == "document" and blocks[1]["source"]["media_type"] == "application/pdf"

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1000), "white").save(buf, format="PNG")
    img = Attachment(filename="photo.png", content_type="image/png", data=buf.getvalue())
    blocks = documents.blocks_for(img, "Attachment 1.3")
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["media_type"] == "image/jpeg"   # shrunk

    import docx
    d = docx.Document()
    d.add_paragraph("نرغب بحجز شاشة مارينا")
    buf = io.BytesIO()
    d.save(buf)
    word = Attachment(filename="req.docx", content_type="", data=buf.getvalue())
    assert "مارينا" in documents.blocks_for(word, "A")[0]["text"]

    pending = Attachment(filename="voice.ogg", external_ref="media-1")
    assert "not downloaded yet" in documents.blocks_for(pending, "A")[0]["text"]
    other = Attachment(filename="x.bin", content_type="application/octet-stream", data=b"\0\1")
    assert "can't be read" in documents.blocks_for(other, "A")[0]["text"]


# ── Checking the answer ─────────────────────────────────────────────────────────

def test_a_clear_answer_is_ready(s, inventory):
    d = extract.normalise(s, ai_answer(), future(0))
    assert d["ready"] and d["client_id"] == inventory["client"].id and d["missing"] == []
    ln = d["lines"][0]
    assert (ln["screen_code"], ln["freq_mode"], ln["freq_value"], ln["plays_per_hour"]) == ("SZR-01", "slots", "2", 60)


def test_frequency_units(s, inventory):
    a, c = inventory["a"], inventory["c"]     # a: 120 s loop, 06-24; c: 60 s loop, 18-02
    assert extract.frequency_for(a, "plays_per_hour", 45, 10)[:3] == ("pph", 45, 45)
    assert extract.frequency_for(a, "plays_per_day", 180, 10)[2] == 10      # 18 hours a day
    assert extract.frequency_for(c, "plays_per_day", 80, 10)[2] == 10       # 8 hours, past midnight
    assert extract.frequency_for(a, "share_pct", 25, 10)[2] == 90           # a quarter of 3600 s / 10 s
    assert extract.frequency_for(a, "share_pct", 100, 10)[3] is True
    assert extract.frequency_for(a, "whole_screen", None, 10)[3] is True
    assert extract.frequency_for(c, "slots", 1, 10)[2] == 60


def test_doubtful_answers_are_not_ready(s, inventory):
    raw = ai_answer(client="Brand New Co", client_conf="medium", lines=[
        ai_line(codes=("SZR-01", "NOPE-9"), start=future(-3), end=future(3)),
        ai_line(codes=(), hint="the big one by the airport", unit="unknown", value=None, conf="low"),
    ])
    d = extract.normalise(s, raw, future(0))
    assert not d["ready"] and d["client_id"] is None and d["client_name"] == "Brand New Co"
    assert set(d["missing"]) == {"screens", "frequency"}
    assert any("NOPE-9" in f for f in d["flags"]) and any("already passed" in f for f in d["flags"])
    assert d["lines"][0]["confidence"] == {"screen": "low", "dates": "low", "frequency": "high"}
    assert d["unmatched"][0]["hint"] == "the big one by the airport"


def test_client_found_by_sender_email(s, inventory):
    d = extract.normalise(s, ai_answer(client=None, client_conf="low"), future(0), sender="ADS@acme.example")
    assert d["client_id"] == inventory["client"].id and d["client_confidence"] == "medium"


def test_not_a_request(s, inventory):
    d = extract.normalise(s, ai_answer(is_request=False, lines=[]), future(0))
    assert d["is_booking_request"] is False and not d["ready"]


def test_language_guess():
    assert extract.guess_language("نرغب بحجز شاشة") == "ar"
    assert extract.guess_language("Please book SZR-01") == "en"
    assert extract.guess_language("Please book شاشة المارينا for us next week") == "mixed"
