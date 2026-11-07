"""The accuracy test: past proposals with the answer the team acted on, read again and scored field by field.

A case is saved from any proposal the team held (the booking it became is the right answer), or added by hand.
A run reads every active case with today's prompt and model, and scores:
    client      the same client
    screens     exactly the same set of screens
    dates       the same start and end date on every screen
    hours       the same day part on every screen
    frequency   plays per hour within 5% (or whole screen on both), where the answer says
    core        screens and dates both right, the measure the plan sets at 95%
The agent may place holds on its own only while the latest run passes: at least 30 cases and 95% core.

Run it from the app (Proposals → Accuracy) or the command line: `python -m billboard_ops.evaluate`.
"""

from datetime import datetime, time
from types import SimpleNamespace

from sqlalchemy import select

from . import ai, extract
from .jobs import handler
from .models import Attachment, EvalCase, EvalRun, utcnow
from .parsing import norm

MIN_CASES = 30
PASS_PCT = 95.0
FIELDS = ("client", "screens", "dates", "hours", "frequency", "core")


def expected_from_booking(booking):
    return {"client": booking.client.name, "lines": [
        {"screen": l.screen.code, "start": l.start_date.isoformat(), "end": l.end_date.isoformat(),
         "start_hour": l.start_hour, "end_hour": l.end_hour,
         "plays_per_hour": None if l.exclusive else l.plays_per_hour, "exclusive": l.exclusive}
        for l in booking.lines]}


def case_from_proposal(session, p, user_email):
    """Save a held proposal as a test case: its messages as the input, its booking as the answer."""
    if p.booking is None:
        raise ValueError("Only a proposal that became a hold has a known right answer.")
    inbound = [m for m in p.messages if m.direction == "in"]
    text = "\n\n".join(f"(received {m.created_at:%Y-%m-%d %H:%M} UTC)\n{m.body}" for m in inbound)
    case = EvalCase(name=f"{p.ref} {p.client.name if p.client else ''}".strip(), channel=p.channel,
                    sender=p.sender, subject=p.subject, text=text,
                    received_on=inbound[0].created_at.date() if inbound else None,
                    expected=expected_from_booking(p.booking), source_proposal_id=p.id, created_by=user_email)
    session.add(case)
    for m in inbound:
        for a in m.attachments:
            if a.data:
                case.attachments.append(Attachment(filename=a.filename, content_type=a.content_type, size=a.size,
                                                   data=a.data, text=a.text))
    return case


def _by_screen(lines, key):
    out = {}
    for l in lines:
        out.setdefault(l[key], []).append(l)
    return out


def score(expected, draft):
    """{field: True/False/None} for one case. None = the answer doesn't say, so it isn't counted."""
    exp = _by_screen(expected.get("lines") or [], "screen")
    got = _by_screen(draft.get("lines") or [], "screen_code")
    res = {"client": norm(expected.get("client")) == norm(draft.get("client_name"))}
    res["screens"] = set(exp) == set(got)
    dates_ok = hours_ok = freq_ok = True
    freq_counted = False
    for code, lines in exp.items():
        mine = got.get(code) or []
        want = sorted((l["start"], l["end"]) for l in lines)
        have = sorted((l["start_date"], l["end_date"]) for l in mine)
        dates_ok &= want == have
        want_h = sorted((l.get("start_hour"), l.get("end_hour")) for l in lines)
        have_h = sorted((None if l.get("start_hour") in ("", None) else int(l["start_hour"]),
                         None if l.get("end_hour") in ("", None) else int(l["end_hour"])) for l in mine)
        hours_ok &= want_h == have_h
        for w in lines:
            if w.get("exclusive"):
                freq_counted = True
                freq_ok &= any(m.get("exclusive") for m in mine)
            elif w.get("plays_per_hour"):
                freq_counted = True
                freq_ok &= any(abs(float(m.get("plays_per_hour") or 0) - w["plays_per_hour"])
                               <= 0.05 * w["plays_per_hour"] for m in mine)
    res["dates"] = dates_ok and bool(exp)
    res["hours"] = hours_ok
    res["frequency"] = freq_ok if freq_counted else None
    res["core"] = res["screens"] and res["dates"]
    return res


def summarise(results):
    out = {"cases": len(results), "errors": sum(1 for r in results if r.get("error"))}
    for f in FIELDS:
        counted = [r["score"][f] for r in results if r.get("score") and r["score"].get(f) is not None]
        out[f] = round(100.0 * sum(counted) / len(counted), 1) if counted else None
    out["passed"] = out["cases"] >= MIN_CASES and (out["core"] or 0) >= PASS_PCT
    return out


def _messages_for(case):
    when = datetime.combine(case.received_on, time(9, 0)) if case.received_on else utcnow()
    return [SimpleNamespace(direction="in", status="received", body=case.text, created_at=when,
                            attachments=list(case.attachments))]


def run_case(session, case, run_id=None, actor="agent"):
    today = case.received_on or extract.today_for(session)
    try:
        _, draft = extract.read(session, messages=_messages_for(case), channel=case.channel, sender=case.sender,
                                subject=case.subject, today=today, actor=actor, eval_run_id=run_id,
                                purpose="eval")
    except (ai.AIFailed, ai.AIUnavailable) as e:
        return {"case_id": case.id, "name": case.name, "error": str(e)}
    return {"case_id": case.id, "name": case.name, "score": score(case.expected, draft),
            "got": {"client": draft["client_name"],
                    "lines": [{k: l[k] for k in ("screen_code", "start_date", "end_date", "start_hour", "end_hour",
                                                 "plays_per_hour", "exclusive")} for l in draft["lines"]]}}


@handler("eval_run")
def _run_job(session, payload):
    run = session.get(EvalRun, payload["run_id"])
    if run is None:
        return
    run.status = "running"
    run.model = ai.runtime.config.PROPOSAL_MODEL if ai.runtime.config else ""
    cases = session.scalars(select(EvalCase).where(EvalCase.active.is_(True)).order_by(EvalCase.id)).all()
    results = []
    for case in cases:
        results.append(run_case(session, case, run.id, run.started_by or "agent"))
        run.results = list(results)          # progress is saved case by case
        session.commit()
    from sqlalchemy import func
    from .models import AiCall
    run.results = results
    run.summary = summarise(results)
    run.usd = float(session.scalar(select(func.coalesce(func.sum(AiCall.usd), 0.0))
                                   .where(AiCall.eval_run_id == run.id)) or 0.0)
    run.status = "done"
    run.finished_at = utcnow()


def latest(session):
    return session.scalars(select(EvalRun).where(EvalRun.status == "done").order_by(EvalRun.id.desc())).first()


def gate(session):
    run = latest(session)
    if run is None:
        return {"passed": False, "why": "The accuracy test hasn't been run yet.", "run": None}
    s = run.summary or {}
    if s.get("cases", 0) < MIN_CASES:
        return {"passed": False, "why": f"The test set has {s.get('cases', 0)} cases; it needs at least {MIN_CASES}.",
                "run": run}
    if (s.get("core") or 0) < PASS_PCT:
        return {"passed": False, "why": f"Screens and dates were right in {s.get('core')}% of cases; it needs "
                                        f"{PASS_PCT:g}%.", "run": run}
    return {"passed": True, "why": f"Passed: {s.get('core')}% of {s.get('cases')} cases.", "run": run}


def main():
    """Run the test from the command line and print the scores."""
    from . import db, runtime
    from .config import Config
    cfg = Config()
    runtime.init(cfg)
    db.init_engine(cfg.DATABASE_URL)
    with db.session_scope() as s:
        run = EvalRun(started_by="command line", status="queued")
        s.add(run)
        s.flush()
        _run_job(s, {"run_id": run.id})
        for r in run.results:
            mark = "ERROR " + r["error"] if r.get("error") else \
                " ".join(f"{f}:{'ok' if v else ('-' if v is None else 'X')}" for f, v in r["score"].items())
            print(f"{r['name'][:40]:40} {mark}")
        print({k: v for k, v in run.summary.items()}, f"cost ${run.usd:.2f}")


if __name__ == "__main__":
    main()
