"""Every record the app keeps.

Dates on screens and bookings are the screen's local calendar dates (a booking "from 1 to 14 March" means
those days where the screen stands); timestamps (created, expires, logged) are UTC.
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String,
                        Text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── People ──────────────────────────────────────────────────────────────────────

ROLES = ("owner", "manager", "sales", "ops", "client")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="sales")
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"), nullable=True)
    language: Mapped[str] = mapped_column(String(2), default="en")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    whatsapp: Mapped[str] = mapped_column(String(40), default="")
    language: Mapped[str] = mapped_column(String(2), default="en")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Screens and their specs ─────────────────────────────────────────────────────

class Spec(Base):
    """What a file must be to play on a screen. Several screens can share one spec."""
    __tablename__ = "specs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    file_types: Mapped[str] = mapped_column(String(200), default="mp4,jpg,png")   # comma list
    video_codecs: Mapped[str] = mapped_column(String(200), default="h264")
    durations_s: Mapped[str] = mapped_column(String(100), default="10")           # allowed ad lengths
    duration_tolerance_s: Mapped[float] = mapped_column(Float, default=0.2)
    fps_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fps_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_file_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    audio_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_margin_pct: Mapped[float] = mapped_column(Float, default=5.0)
    # The client's rule: Arabic only, or Arabic plus English. Kept as a field so it can't be lost.
    language_rule: Mapped[str] = mapped_column(String(40), default="arabic_or_arabic_english")
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def orientation(self):
        if self.width_px == self.height_px:
            return "square"
        return "landscape" if self.width_px > self.height_px else "portrait"

    @property
    def duration_list(self):
        out = []
        for p in (self.durations_s or "").split(","):
            try:
                out.append(float(p))
            except ValueError:
                pass
        return out


class Screen(Base):
    __tablename__ = "screens"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    area: Mapped[str] = mapped_column(String(120), default="")      # district or city, for "nearby" suggestions
    address: Mapped[str] = mapped_column(String(300), default="")
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    physical_size: Mapped[str] = mapped_column(String(60), default="")   # e.g. "12m x 4m"
    spec_id: Mapped[Optional[int]] = mapped_column(ForeignKey("specs.id"), nullable=True)
    # The loop: e.g. 120 seconds, made of 10-second slots = 12 advertisers taking turns.
    loop_seconds: Mapped[int] = mapped_column(Integer, default=120)
    slot_seconds: Mapped[int] = mapped_column(Integer, default=10)
    # Share of airtime that can be sold; the rest is kept for the owner's own content.
    sellable_pct: Mapped[float] = mapped_column(Float, default=100.0)
    # Operating hours as whole hours, 0–24. close <= open means the screen runs past midnight;
    # open 0 and close 24 means all day.
    open_hour: Mapped[int] = mapped_column(Integer, default=6)
    close_hour: Mapped[int] = mapped_column(Integer, default=24)
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Dubai")
    calendar_id: Mapped[str] = mapped_column(String(300), default="")    # its Google Calendar
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    spec: Mapped[Optional[Spec]] = relationship()

    @property
    def slots_per_loop(self):
        return max(1, self.loop_seconds // max(1, self.slot_seconds))

    @property
    def plays_per_hour_per_slot(self):
        return 3600.0 / max(1, self.loop_seconds)


class Blackout(Base):
    """Whole days a screen (or every screen, when screen_id is empty) does not play."""
    __tablename__ = "blackouts"
    id: Mapped[int] = mapped_column(primary_key=True)
    screen_id: Mapped[Optional[int]] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(300), default="")


class HoursOverride(Base):
    """Different operating hours for a stretch of days (for example a holiday month)."""
    __tablename__ = "hours_overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    screen_id: Mapped[Optional[int]] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    open_hour: Mapped[int] = mapped_column(Integer)
    close_hour: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300), default="")


# ── Bookings ────────────────────────────────────────────────────────────────────

STATUSES = ("enquiry", "hold", "confirmed", "awaiting_creative", "checking", "changes_requested",
            "creative_approved", "plan_set", "scheduled", "live", "completed", "expired", "cancelled")
# Statuses that take up room in a screen's loop.
OCCUPYING = tuple(s for s in STATUSES if s not in ("enquiry", "expired", "cancelled"))
# Confirmed and not yet over: every stage from confirmation to going live.
BOOKED = ("confirmed", "awaiting_creative", "checking", "changes_requested", "creative_approved", "plan_set",
          "scheduled", "live")
# Waiting for, or checking, the client's files (Phase 3).
CREATIVE_STAGES = ("confirmed", "awaiting_creative", "checking", "changes_requested")


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="hold")
    hold_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    hold_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(254), default="")
    cancel_reason: Mapped[str] = mapped_column(String(500), default="")
    # Set when a Manager let this booking go over a screen's capacity, and why.
    override_reason: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")     # manual | import | proposal
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # Phase 3: collecting the creative.
    drive_folder_id: Mapped[str] = mapped_column(String(200), default="")
    upload_token: Mapped[str] = mapped_column(String(64), default="")
    creative_deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    creative_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    creative_reminded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    creative_alerted_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    client: Mapped[Client] = relationship()
    lines: Mapped[list["BookingLine"]] = relationship(back_populates="booking", cascade="all, delete-orphan",
                                                      order_by="BookingLine.start_date")

    __table_args__ = (Index("ix_bookings_source_ref", "source_ref"),)

    @property
    def start_date(self):
        return min((l.start_date for l in self.lines), default=None)

    @property
    def end_date(self):
        return max((l.end_date for l in self.lines), default=None)


class BookingLine(Base):
    """One screen within a booking, with its own dates, hours and frequency."""
    __tablename__ = "booking_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Day part as whole hours; empty = all the screen's operating hours.
    start_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ad_seconds: Mapped[int] = mapped_column(Integer, default=10)
    plays_per_hour: Mapped[float] = mapped_column(Float, default=30.0)
    exclusive: Mapped[bool] = mapped_column(Boolean, default=False)    # the whole screen
    calendar_event_id: Mapped[str] = mapped_column(String(300), default="")

    booking: Mapped[Booking] = relationship(back_populates="lines")
    screen: Mapped[Screen] = relationship()

    __table_args__ = (Index("ix_lines_screen_dates", "screen_id", "start_date", "end_date"),)


# ── Import, sync and the log ────────────────────────────────────────────────────

class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))           # specs | screens | bookings
    source_kind: Mapped[str] = mapped_column(String(20))    # sheet | file
    source_label: Mapped[str] = mapped_column(String(500), default="")
    spreadsheet_id: Mapped[str] = mapped_column(String(200), default="")
    tab: Mapped[str] = mapped_column(String(200), default="")
    header: Mapped[list] = mapped_column(JSON, default=list)
    rows: Mapped[list] = mapped_column(JSON, default=list)          # the raw rows as read
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)       # field -> column index
    preview: Mapped[dict] = mapped_column(JSON, default=dict)       # the trial run's result
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | previewed | committed | discarded
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SheetWatch(Base):
    """A sheet we imported from, watched for direct edits during the switch-over."""
    __tablename__ = "sheet_watches"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_run_id: Mapped[int] = mapped_column(ForeignKey("import_runs.id"))
    spreadsheet_id: Mapped[str] = mapped_column(String(200))
    tab: Mapped[str] = mapped_column(String(200))
    key_column: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    row_hashes: Mapped[dict] = mapped_column(JSON, default=dict)    # row key -> hash
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SheetChange(Base):
    __tablename__ = "sheet_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("sheet_watches.id", ondelete="CASCADE"))
    row_key: Mapped[str] = mapped_column(String(300))
    change: Mapped[str] = mapped_column(String(20))        # added | changed | removed
    before: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(254), default="")


class Activity(Base):
    __tablename__ = "activity"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(254))           # an email, or "system"
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    summary: Mapped[str] = mapped_column(String(500), default="")
    changes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    __table_args__ = (Index("ix_activity_entity", "entity_type", "entity_id"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_email: Mapped[str] = mapped_column(String(254), default="")   # empty = the whole team
    kind: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(String(500))
    link: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Job(Base):
    """Work for the worker: calendar and sheet mirroring, emails, sheet watching."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued | running | done | failed | skipped
    dedupe_key: Mapped[str] = mapped_column(String(200), default="")
    run_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_jobs_due", "status", "run_at"), Index("ix_jobs_dedupe", "dedupe_key", "status"))


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class BookingCounter(Base):
    """Next booking number per year, so codes run BK-2026-0001, BK-2026-0002, ..."""
    __tablename__ = "booking_counters"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


# ── Phase 2: proposals ──────────────────────────────────────────────────────────

PROPOSAL_STATUSES = ("new", "reading", "needs_review", "waiting_client", "held", "declined", "not_proposal",
                     "merged", "failed")
OPEN_PROPOSAL = ("new", "reading", "needs_review", "waiting_client", "failed")


class Proposal(Base):
    """One client request, however it arrived, with everything said about it since."""
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    channel: Mapped[str] = mapped_column(String(20))            # email | whatsapp | manual | portal
    sender: Mapped[str] = mapped_column(String(254), default="")  # an email address or a phone number
    sender_name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"), nullable=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    reading: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)   # what Claude returned, as is
    draft: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)     # the booking it would make, editable
    checks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)    # vacancy per draft line
    ready: Mapped[bool] = mapped_column(Boolean, default=False)           # everything needed, all sure
    duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposals.id"), nullable=True)
    booking_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    auto_held: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    read_count: Mapped[int] = mapped_column(Integer, default=0)
    last_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    client: Mapped[Optional[Client]] = relationship()
    booking: Mapped[Optional[Booking]] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="proposal", order_by="Message.id")

    __table_args__ = (Index("ix_proposals_status", "status"), Index("ix_proposals_sender", "sender"))


class Message(Base):
    """A message in or out: an email, a WhatsApp message, a pasted note, or a draft waiting for approval."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposals.id"), nullable=True)
    booking_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(3))             # in | out
    channel: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(30), default="inbound")  # inbound | clarify | hold_confirmation | decline | hold_reminder | other
    status: Mapped[str] = mapped_column(String(20), default="received")  # received | draft | approved | sent | failed | discarded
    sender: Mapped[str] = mapped_column(String(254), default="")
    recipient: Mapped[str] = mapped_column(String(254), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(5), default="en")
    external_id: Mapped[str] = mapped_column(String(300), default="")   # email Message-ID or WhatsApp message id
    in_reply_to: Mapped[str] = mapped_column(String(300), default="")
    approved_by: Mapped[str] = mapped_column(String(254), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    proposal: Mapped[Optional[Proposal]] = relationship(back_populates="messages")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="message", order_by="Attachment.id")

    __table_args__ = (Index("ix_messages_external", "external_id"), Index("ix_messages_status", "status"))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    eval_case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("eval_cases.id", ondelete="CASCADE"),
                                                        nullable=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    external_ref: Mapped[str] = mapped_column(String(300), default="")   # a WhatsApp media id not fetched yet
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # text read out of it, kept for re-reads
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Optional[Message]] = relationship(back_populates="attachments")


class AiCall(Base):
    """The cost ledger: every Claude call, who it was for, and what it cost."""
    __tablename__ = "ai_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    purpose: Mapped[str] = mapped_column(String(40))            # read_proposal | eval
    model: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    actor: Mapped[str] = mapped_column(String(254), default="agent")   # who caused it
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id"), nullable=True)  # whose work
    proposal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposals.id"), nullable=True)
    eval_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("eval_runs.id"), nullable=True)
    booking_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookings.id"), nullable=True)
    creative_id: Mapped[Optional[int]] = mapped_column(ForeignKey("creatives.id", ondelete="SET NULL"),
                                                       nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_reason: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class EvalCase(Base):
    """A past proposal with the answer the team acted on: the accuracy test set."""
    __tablename__ = "eval_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(20), default="email")
    sender: Mapped[str] = mapped_column(String(254), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    received_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)   # "today" when it was sent
    expected: Mapped[dict] = mapped_column(JSON, default=dict)
    source_proposal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposals.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    attachments: Mapped[list[Attachment]] = relationship(order_by="Attachment.id")


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued | running | done | failed
    model: Mapped[str] = mapped_column(String(80), default="")
    started_by: Mapped[str] = mapped_column(String(254), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    usd: Mapped[float] = mapped_column(Float, default=0.0)


class BookingDocument(Base):
    """The signed proposal or purchase order a booking was confirmed on."""
    __tablename__ = "booking_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[str] = mapped_column(String(254), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Phase 3: creative ───────────────────────────────────────────────────────────

class Creative(Base):
    """One version of one file a client sent for a booking, and what the checks found."""
    __tablename__ = "creatives"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="upload")   # drive | upload | email | whatsapp | team
    drive_file_id: Mapped[str] = mapped_column(String(200), default="")
    data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)   # kept here only without Drive
    line_ids: Mapped[list] = mapped_column(JSON, default=list)          # the booked screens it is for
    status: Mapped[str] = mapped_column(String(20), default="received")
    # received | checking | passed | warnings | failed | error | approved | rejected | superseded
    probe: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # what ffprobe measured
    results: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    team_summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id"), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(254), default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")

    booking: Mapped[Booking] = relationship()
    frames: Mapped[list["CreativeFrame"]] = relationship(order_by="CreativeFrame.at_seconds",
                                                         cascade="all, delete-orphan")

    __table_args__ = (Index("ix_creatives_booking", "booking_id"), Index("ix_creatives_drive", "drive_file_id"))


class CreativeFrame(Base):
    """A still taken from a video (or the image itself), shown as evidence and sent to Claude."""
    __tablename__ = "creative_frames"
    id: Mapped[int] = mapped_column(primary_key=True)
    creative_id: Mapped[int] = mapped_column(ForeignKey("creatives.id", ondelete="CASCADE"))
    at_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    jpeg: Mapped[bytes] = mapped_column(LargeBinary)


class CreativeCase(Base):
    """A file with known answers: the test set for the creative checks."""
    __tablename__ = "creative_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specs.id"))
    ad_seconds: Mapped[int] = mapped_column(Integer, default=10)
    client_name: Mapped[str] = mapped_column(String(200), default="")
    expected: Mapped[dict] = mapped_column(JSON, default=dict)          # check key -> pass | fail
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    spec: Mapped[Spec] = relationship()


class CreativeRun(Base):
    __tablename__ = "creative_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_by: Mapped[str] = mapped_column(String(254), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
