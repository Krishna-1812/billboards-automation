"""Turning attachments into something Claude can read.

- PDFs with a text layer are sent as text (cheaper); scans are sent as the PDF itself, which Claude reads page by
  page as images.
- Photos and screenshots are sent as images, shrunk to at most 1568 pixels on the long side.
- Word and Excel files, and CSV and text files, are turned into text here.
- Anything else is named but not read, so the team knows it is there.
"""

import base64
import csv
import io
import logging

log = logging.getLogger(__name__)

MAX_BYTES = 15 * 1024 * 1024
MAX_TEXT = 60_000            # characters kept from one file
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def kind_of(filename, content_type):
    name = (filename or "").lower()
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if ct in IMAGE_TYPES or name.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if name.endswith(".docx") or "wordprocessingml" in ct:
        return "docx"
    if name.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in ct:
        return "xlsx"
    if name.endswith(".csv") or ct == "text/csv":
        return "csv"
    if ct.startswith("text/") or name.endswith((".txt", ".eml")):
        return "text"
    return "other"


def _pdf_text(data):
    import pdfplumber
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages[:60]:
            parts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                parts.append("\n".join("\t".join(c or "" for c in row) for row in table))
    return "\n".join(parts), pages


def _docx_text(data):
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts.append("\t".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _xlsx_text(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets[:5]:
        parts.append(f"[sheet: {ws.title}]")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 300:
                parts.append("[… more rows not shown]")
                break
            if any(v is not None and str(v).strip() for v in row):
                parts.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(parts)


def _csv_text(data):
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))[:300]
    return "\n".join("\t".join(r) for r in rows)


def _shrink_image(data, content_type):
    """At most 1568 px on the long side (what Claude reads at full detail), re-encoded if it had to change."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if max(img.size) <= 1568 and len(data) < 4_500_000:
            return data, content_type
        img.thumbnail((1568, 1568))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - an image we can't open is sent as is, or skipped below
        return data, content_type


def text_of(att):
    """Text read out of an attachment (cached on it), or None if it has to be sent as a PDF or image."""
    if att.text is not None:
        return att.text
    if not att.data:
        return None
    kind = kind_of(att.filename, att.content_type)
    try:
        if kind == "pdf":
            text, pages = _pdf_text(att.data)
            # A real text layer has words on most pages; a scan has next to none.
            att.text = text[:MAX_TEXT] if len(text.strip()) >= 80 * max(1, min(pages, 5)) else None
        elif kind == "docx":
            att.text = _docx_text(att.data)[:MAX_TEXT]
        elif kind == "xlsx":
            att.text = _xlsx_text(att.data)[:MAX_TEXT]
        elif kind == "csv":
            att.text = _csv_text(att.data)[:MAX_TEXT]
        elif kind == "text":
            att.text = att.data.decode("utf-8", errors="replace")[:MAX_TEXT]
    except Exception as e:  # noqa: BLE001 - a damaged file is named, not fatal
        log.info("could not read %s: %s", att.filename, e)
        att.text = f"[this file could not be opened: {type(e).__name__}]"
    return att.text


def blocks_for(att, label):
    """Claude content blocks for one attachment, introduced by `label`."""
    if not att.data and att.external_ref:
        return [{"type": "text", "text": f"{label}: {att.filename or 'a file'} (not downloaded yet)"}]
    kind = kind_of(att.filename, att.content_type)
    if att.size and att.size > MAX_BYTES:
        return [{"type": "text", "text": f"{label}: {att.filename} is too large to read ({att.size // 1_000_000} MB)."}]
    text = text_of(att)
    if text is not None:
        return [{"type": "text", "text": f"{label}: {att.filename}\n{text}"}]
    if kind == "pdf":
        return [{"type": "text", "text": f"{label}: {att.filename} (scanned PDF follows)"},
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(att.data).decode()}}]
    if kind == "image":
        data, ct = _shrink_image(att.data, (att.content_type or "image/jpeg").split(";")[0])
        if ct not in IMAGE_TYPES:
            ct = "image/jpeg"
        return [{"type": "text", "text": f"{label}: {att.filename or 'an image'}"},
                {"type": "image", "source": {"type": "base64", "media_type": ct,
                                             "data": base64.standard_b64encode(data).decode()}}]
    return [{"type": "text", "text": f"{label}: {att.filename} ({att.content_type or 'unknown type'}) can't be read "
                                      "automatically."}]
