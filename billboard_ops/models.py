"""Every record the app keeps.

Dates on screens and bookings are the screen's local calendar dates (a booking "from 1 to 14 March" means
those days where the screen stands); timestamps (created, expires, logged) are UTC.
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
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
