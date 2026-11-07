"""The guided import: source → match columns → trial run → import → (bookings) match the calendar."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from .. import importer
from ..activity import log
from ..google_client import NotConnected, get_client, service_account_email, spreadsheet_id_from
from ..models import ImportRun
from ..web import current_user, f_bool, f_str, get_db, needs

bp = Blueprint("imports", __name__)

KIND_LABEL = {"specs": "Specs", "screens": "Screens", "bookings": "Bookings"}


@bp.route("/imports")
@needs("import")
def import_list():
    s = get_db()
    runs = s.scalars(select(ImportRun).order_by(ImportRun.id.desc()).limit(50)).all()
    cfg = current_app.config["APP"]
    return render_template("imports.html", runs=runs, kinds=KIND_LABEL,
                           sa_email=service_account_email(cfg), connected=bool(cfg.service_account_info()))


@bp.route("/imports/new", methods=["POST"])
@needs("import")
def import_new():
    s = get_db()
    kind = f_str("kind")
    if kind not in KIND_LABEL:
        abort(400)
    run = ImportRun(kind=kind, created_by=current_user().email)
    try:
        upload = request.files.get("file")
        if upload and upload.filename:
            run.source_kind = "file"
            run.source_label = upload.filename
            run.header, run.rows = importer.read_file(upload.filename, upload.read(), f_str("tab"))
        else:
            sheet = spreadsheet_id_from(f_str("sheet"))
            tab = f_str("tab")
            if not sheet or not tab:
                raise ValueError("Paste the sheet's link and the tab's name, or upload a file.")
            values = get_client(current_app.config["APP"]).read_values(sheet, tab)
            run.source_kind, run.spreadsheet_id, run.tab = "sheet", sheet, tab
            run.source_label = f"Google Sheet {sheet[:10]}…, tab {tab}"
            run.header, run.rows = importer.split_header(values)
    except NotConnected as e:
        flash(str(e) + " Or upload the sheet as .xlsx instead.", "error")
        return redirect(url_for("imports.import_list"))
    except Exception as e:  # noqa: BLE001 - unreadable file, sheet not shared, wrong tab...
        flash(f"Couldn't read that: {_plain_error(e)}", "error")
        return redirect(url_for("imports.import_list"))
    if not run.header:
        flash("That sheet looks empty.", "error")
        return redirect(url_for("imports.import_list"))
    run.mapping = importer.guess_mapping(kind, run.header)
    s.add(run)
    s.flush()
    log(s, current_user().email, "started", "import", run.id, f"Read {len(run.rows)} rows of {kind} from {run.source_label}")
    s.commit()
    return redirect(url_for("imports.import_map", run_id=run.id))


def _plain_error(e):
    text = str(e)
    if "403" in text or "PERMISSION_DENIED" in text:
        return "the sheet isn't shared with the app's Google account (see Settings → Connections)."
    if "Unable to parse range" in text or "400" in text:
        return "there's no tab with that name."
    if "404" in text:
        return "no sheet with that link."
    return text[:300]


def _run(s, run_id):
    run = s.get(ImportRun, run_id)
    if run is None:
        abort(404)
    return run


@bp.route("/imports/<int:run_id>/map", methods=["GET", "POST"])
@needs("import")
def import_map(run_id):
    s = get_db()
    run = _run(s, run_id)
    if run.status == "committed":
        return redirect(url_for("imports.import_preview", run_id=run.id))
    if request.method == "POST":
        mapping = {}
        for field, *_ in importer.field_list(run.kind):
            v = request.form.get(f"map_{field}", "")
            if v.isdigit() and int(v) < len(run.header):
                mapping[field] = int(v)
        run.mapping = mapping
        importer.preview(s, run)
        s.commit()
        return redirect(url_for("imports.import_preview", run_id=run.id))
    return render_template("import_map.html", run=run, fields=importer.field_list(run.kind), kinds=KIND_LABEL,
                           sample=run.rows[:5])


@bp.route("/imports/<int:run_id>")
@needs("import")
def import_preview(run_id):
    s = get_db()
    run = _run(s, run_id)
    if run.status == "new":
        return redirect(url_for("imports.import_map", run_id=run.id))
    return render_template("import_preview.html", run=run, p=run.preview or {}, kinds=KIND_LABEL,
                           fields=importer.field_list(run.kind), show_filter=request.args.get("show", "problems"))


@bp.route("/imports/<int:run_id>/reread", methods=["POST"])
@needs("import")
def import_reread(run_id):
    """Read the sheet again after it was fixed, keeping the column matches."""
    s = get_db()
    run = _run(s, run_id)
    if run.status == "committed" or run.source_kind != "sheet":
        abort(400)
    try:
        values = get_client(current_app.config["APP"]).read_values(run.spreadsheet_id, run.tab)
    except Exception as e:  # noqa: BLE001
        flash(f"Couldn't read the sheet: {_plain_error(e)}", "error")
        return redirect(url_for("imports.import_preview", run_id=run.id))
    run.header, run.rows = importer.split_header(values)
    importer.preview(s, run)
    s.commit()
    flash("Read the sheet again and re-ran the trial.", "ok")
    return redirect(url_for("imports.import_preview", run_id=run.id))


@bp.route("/imports/<int:run_id>/commit", methods=["POST"])
@needs("import")
def import_commit(run_id):
    s = get_db()
    run = _run(s, run_id)
    try:
        result = importer.commit(s, run, current_user(), skip_errors=f_bool("skip_errors"),
                                 import_clashes=f_bool("import_clashes"))
    except importer.ImportBlocked as e:
        s.rollback()
        flash(str(e), "error")
        return redirect(url_for("imports.import_preview", run_id=run_id))
    s.commit()
    flash("Imported: " + ", ".join(f"{v} {k}" for k, v in result.items() if not k.startswith("_")), "ok")
    if run.kind == "bookings":
        return redirect(url_for("imports.import_calendar", run_id=run.id))
    return redirect(url_for("imports.import_preview", run_id=run.id))


@bp.route("/imports/<int:run_id>/discard", methods=["POST"])
@needs("import")
def import_discard(run_id):
    s = get_db()
    run = _run(s, run_id)
    if run.status != "committed":
        run.status = "discarded"
        s.commit()
    return redirect(url_for("imports.import_list"))


@bp.route("/imports/<int:run_id>/calendar", methods=["GET", "POST"])
@needs("import")
def import_calendar(run_id):
    """Match existing Google Calendar events to the imported bookings, then start mirroring."""
    s = get_db()
    run = _run(s, run_id)
    if run.kind != "bookings" or run.status != "committed":
        abort(400)
    result = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "match":
            try:
                g = get_client(current_app.config["APP"])
                result = importer.match_calendar(s, run, g, spreadsheet_id_from(f_str("shared_calendar")))
            except NotConnected as e:
                flash(str(e), "error")
            except Exception as e:  # noqa: BLE001
                flash(f"Couldn't read the calendar: {_plain_error(e)}", "error")
            if result is not None:
                run.preview = {**run.preview, "calendar": result}
                s.commit()
        elif action == "mirror":
            n = importer.start_calendar_mirror(s, run)
            run.preview = {**run.preview, "mirror_started": True}
            log(s, current_user().email, "mirror_started", "import", run.id, f"Calendar mirroring for {n} bookings")
            s.commit()
            flash(f"Mirroring {n} imported bookings to Google Calendar. Matched events are updated; the rest are "
                  "created.", "ok")
            return redirect(url_for("imports.import_preview", run_id=run.id))
    return render_template("import_calendar.html", run=run, result=result or (run.preview or {}).get("calendar"))
