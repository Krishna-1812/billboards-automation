"""The accuracy test: scoring, the gate, cases saved from proposals, and a full run."""

from sqlalchemy import select

from billboard_ops import evaluate, intake
from billboard_ops.models import AiCall, EvalCase, EvalRun

from conftest import future


def draft(lines, client="Acme Foods"):
    return {"client_name": client, "lines": [
        {"screen_code": c, "start_date": a, "end_date": b, "start_hour": "", "end_hour": "", "plays_per_hour": pph,
         "exclusive": False} for c, a, b, pph in lines]}


EXPECTED = {"client": "Acme Foods", "lines": [
    {"screen": "SZR-01", "start": "2026-12-01", "end": "2026-12-14", "start_hour": None, "end_hour": None,
     "plays_per_hour": 60, "exclusive": False},
    {"screen": "SZR-02", "start": "2026-12-01", "end": "2026-12-14", "start_hour": None, "end_hour": None,
     "plays_per_hour": None, "exclusive": False}]}


def test_scoring():
    right = draft([("SZR-01", "2026-12-01", "2026-12-14", 61), ("SZR-02", "2026-12-01", "2026-12-14", 30)])
    assert evaluate.score(EXPECTED, right) == {"client": True, "screens": True, "dates": True, "hours": True,
                                               "frequency": True, "core": True}
    wrong_date = draft([("SZR-01", "2026-12-01", "2026-12-13", 60), ("SZR-02", "2026-12-01", "2026-12-14", 30)])
    sc = evaluate.score(EXPECTED, wrong_date)
    assert sc["screens"] and not sc["dates"] and not sc["core"]
    missing_screen = draft([("SZR-01", "2026-12-01", "2026-12-14", 90)], client="ACME foods")
    sc = evaluate.score(EXPECTED, missing_screen)
    assert sc["client"] and not sc["screens"] and not sc["frequency"] and not sc["core"]
    no_freq = {"client": "x", "lines": [dict(EXPECTED["lines"][1])]}
    assert evaluate.score(no_freq, draft([("SZR-02", "2026-12-01", "2026-12-14", 5)]))["frequency"] is None


def test_summary_and_gate(s, app):
    good = {"score": {"client": True, "screens": True, "dates": True, "hours": True, "frequency": None, "core": True}}
    bad = {"score": {"client": True, "screens": True, "dates": False, "hours": True, "frequency": None, "core": False}}
    summary = evaluate.summarise([good] * 29 + [bad])
    assert summary["cases"] == 30 and summary["core"] == 96.7 and summary["frequency"] is None and summary["passed"]
    assert not evaluate.summarise([good] * 20)["passed"]                 # too few cases
    assert not evaluate.summarise([good] * 28 + [bad] * 2)["passed"]     # 93.3%
    assert evaluate.gate(s)["passed"] is False
    s.add(EvalRun(status="done", summary=summary))
    s.flush()
    assert evaluate.gate(s)["passed"] is True


def test_a_held_proposal_becomes_a_test_case_and_the_run_scores_it(s, people, inventory, fake_ai):
    p, _, _ = intake.receive(s, channel="email", sender="ads@acme.example", subject="Booking",
                             body="Two slots on SZR-01 from the 10th for a week")
    intake.process(s, p)
    intake.place_hold(s, people["sales"], p, client=inventory["client"], lines=intake.lines_from_draft(p.draft["lines"]))
    s.flush()
    case = evaluate.case_from_proposal(s, p, "manager@example.com")
    s.flush()
    assert case.expected["lines"][0]["screen"] == "SZR-01" and case.source_proposal_id == p.id
    # A second case the agent will get wrong (it reads SZR-01 where SZR-02 was booked).
    s.add(EvalCase(name="wrong", text="SZR-02 please", received_on=future(0), expected={
        "client": "Acme Foods", "lines": [{"screen": "SZR-02", "start": future(10).isoformat(),
                                           "end": future(16).isoformat(), "start_hour": None, "end_hour": None,
                                           "plays_per_hour": None, "exclusive": False}]}))
    run = EvalRun(started_by="manager@example.com")
    s.add(run)
    s.commit()
    evaluate._run_job(s, {"run_id": run.id})
    assert run.status == "done" and run.summary["cases"] == 2 and run.summary["core"] == 50.0
    assert run.usd > 0 and not run.summary["passed"]
    purposes = {c.purpose for c in s.scalars(select(AiCall).where(AiCall.eval_run_id == run.id))}
    assert purposes == {"eval"}
