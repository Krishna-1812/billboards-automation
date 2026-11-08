"""Any table on the dashboard as CSV, Excel, PDF, or a tab in Google Sheets (Phase 5)."""

import csv
import io
from datetime import date, datetime

from flask import Response


def _plain(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 1)
    return "" if v is None else v


def to_csv(headers, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(headers)
    for r in rows:
        w.writerow([_plain(v) for v in r])
    return out.getvalue().encode("utf-8-sig")


def to_xlsx(headers, rows, title="Export"):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Export"
    ws.append(list(headers))
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([_plain(v) for v in r])
    for i, h in enumerate(headers):
        width = max([len(str(h))] + [len(str(_plain(r[i]))) for r in rows[:200]] + [8])
        ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = min(60, width + 2)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(headers, rows, title):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28,
                            bottomMargin=28, title=title)
    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=7.5, leading=9)
    data = [[Paragraph(f"<b>{h}</b>", cell) for h in headers]]
    data += [[Paragraph(str(_plain(v)).replace("&", "&amp;").replace("<", "&lt;"), cell) for v in r] for r in rows]
    if len(data) == 1:
        data.append([Paragraph("Nothing to show.", cell)] + [""] * (len(headers) - 1))
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe3")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    doc.build([Paragraph(title, styles["Heading2"]), Spacer(1, 6), table])
    return buf.getvalue()


def respond(fmt, headers, rows, title):
    """A download in the asked format, or None for an unknown one."""
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title).strip("-")
    if fmt == "csv":
        return Response(to_csv(headers, rows), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if fmt == "xlsx":
        return Response(to_xlsx(headers, rows, title),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    if fmt == "pdf":
        return Response(to_pdf(headers, rows, title), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return None


def to_sheet(session, headers, rows, title):
    """Write the table to its own tab of the mirror spreadsheet. Returns the tab name; raises when Sheets isn't set
    up."""
    from . import runtime, settings
    from .google_client import get_client
    sid = settings.get(session, "mirror_spreadsheet_id")
    if not sid:
        raise ValueError("Set the mirror sheet in Settings → General first; exports go to a tab of that sheet.")
    tab = f"{title} (export)"[:90]
    get_client(runtime.config).write_values(sid, tab, [list(headers)] + [[_plain(v) for v in r] for r in rows])
    return tab
