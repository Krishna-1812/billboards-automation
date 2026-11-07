"""The test set for the creative checks: real files with known answers, checked again and compared.

Each case is a file, the spec and ad length to check it against, the booked client, and the right answer for
the checks that matter for it ("pass" or "fail"). A run checks every case with today's code and prompt and
reports, per check, how often it agreed, and separately:
    missed       a real problem the checks passed (the dangerous kind)
    false alarms a fine file the checks failed (the annoying kind)
The plan's bar: every measured check right every time, and no language-rule breach missed.
"""

import os
import shutil
import tempfile

from sqlalchemy import func, select

from . import ai, settings
from . import creative_checks as cc
from .jobs import handler
from .models import AiCall, CreativeCase, CreativeRun, utcnow


def run_case(session, case, actor="agent"):
    work = tempfile.mkdtemp(prefix="creative-case-")
    try:
        path = os.path.join(work, "file" + os.path.splitext(case.filename)[1])
        with open(path, "wb") as fh:
            fh.write(case.data)
        info = cc.probe(path, case.filename)
        scan = cc.scan_video(path, info.get("duration")) if info["kind"] == "video" else None
        checks = cc.technical(info, scan, case.spec, case.ad_seconds, float(settings.get(session, "black_max_s")),
                              float(settings.get(session, "freeze_max_s")))
        wants_content = any(k in cc.CONTENT for k in case.expected)
        if wants_content and info.get("readable"):
            frames = cc.sample_frames(path, info, os.path.join(work, "frames"))
            rules = [r.strip() for r in (settings.get(session, "content_rules") or "").splitlines() if r.strip()]
            content, _ = cc.content(session, frames, case.spec, client_name=case.client_name, rules=rules,
                                    company=settings.get(session, "company_name"), actor=actor)
            checks += content
    finally:
        shutil.rmtree(work, ignore_errors=True)
    got = {c["key"]: ("fail" if c["status"] == "fail" else "pass") for c in checks if c["status"] != "skip"}
    rows = []
    for key, want in case.expected.items():
        if want not in ("pass", "fail"):
            continue
        have = got.get(key)
        if have is None:
            result = "not checked"
        elif have == want:
            result = "right"
        else:
            result = "missed" if want == "fail" else "false alarm"
        rows.append({"key": key, "want": want, "got": have, "result": result})
    return {"case_id": case.id, "name": case.name, "rows": rows}


def summarise(results):
    measured = [r for x in results for r in x.get("rows", []) if r["key"] in cc.TECHNICAL and r["got"] is not None]
    content = [r for x in results for r in x.get("rows", []) if r["key"] in cc.CONTENT and r["got"] is not None]

    def pct(rows):
        return round(100.0 * sum(r["result"] == "right" for r in rows) / len(rows), 1) if rows else None
    missed = [r for x in results for r in x.get("rows", []) if r["result"] == "missed"]
    language_missed = [r for r in missed if r["key"] == "language"]
    out = {"cases": len(results), "measured": pct(measured), "content": pct(content), "missed": len(missed),
           "false_alarms": sum(r["result"] == "false alarm" for x in results for r in x.get("rows", [])),
           "language_missed": len(language_missed),
           "per_check": {k: pct([r for x in results for r in x.get("rows", []) if r["key"] == k and r["got"]])
                         for k in cc.TECHNICAL + cc.CONTENT
                         if any(r["key"] == k for x in results for r in x.get("rows", []))}}
    out["passed"] = bool(results) and out["measured"] in (100.0, None) and not language_missed
    return out


@handler("creative_eval_run")
def _run_job(session, payload):
    run = session.get(CreativeRun, payload["run_id"])
    if run is None:
        return
    run.status = "running"
    session.commit()
    results = []
    for case in session.scalars(select(CreativeCase).where(CreativeCase.active.is_(True)).order_by(CreativeCase.id)):
        try:
            results.append(run_case(session, case, run.started_by or "agent"))
        except ai.AITransient as e:
            results.append({"case_id": case.id, "name": case.name, "rows": [], "error": str(e)})
        run.results = list(results)
        session.commit()
    run.summary = summarise(results)
    run.usd = float(session.scalar(select(func.coalesce(func.sum(AiCall.usd), 0.0))
                                   .where(AiCall.purpose == "check_creative", AiCall.creative_id.is_(None),
                                          AiCall.booking_id.is_(None), AiCall.at >= run.started_at)) or 0.0)
    run.status = "done"
    run.finished_at = utcnow()
