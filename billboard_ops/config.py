"""Settings read from the environment (Railway → Variables).

Nothing here is a secret in code: every key, password and token comes from the environment.
"""

import json
import os


def _bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name):
    return [p.strip().lower() for p in os.environ.get(name, "").split(",") if p.strip()]


def database_url():
    """Postgres on Railway; a local SQLite file otherwise. Railway hands out postgres:// URLs; SQLAlchemy wants
    the driver named, and newer SQLAlchemy defaults to a different one than the psycopg2 we install."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(__file__)), "billboard_ops.db")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


class Config:
    def __init__(self):
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "")
        self.DATABASE_URL = database_url()
        # Google sign-in for the team (a Google Identity Services client ID, like the main platform's).
        self.GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
        # The first owners. Anyone else is let in only once an owner adds them in Settings → People.
        self.OWNER_EMAILS = _list("OWNER_EMAILS")
        # Local development only: a plain email box instead of Google. Refused when GOOGLE_CLIENT_ID is set,
        # so a live service can never fall back to it.
        self.DEV_LOGIN = _bool("DEV_LOGIN") and not self.GOOGLE_CLIENT_ID
        # Sign-in is switched off for now: everyone who opens the app acts as the owner. It switches back on with
        # LOGIN_REQUIRED=1, and on its own once GOOGLE_CLIENT_ID is set (LOGIN_REQUIRED=0 keeps it off even then).
        raw = os.environ.get("LOGIN_REQUIRED", "").strip()
        self.LOGIN_REQUIRED = _bool("LOGIN_REQUIRED") if raw else bool(self.GOOGLE_CLIENT_ID)
        # A Google service account (its JSON key, pasted whole) for Sheets, Calendar and Drive.
        self.GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        # Email: SMTP for reminders and error alerts. Unset = messages are logged, not sent.
        self.SMTP_HOST = os.environ.get("SMTP_HOST", "")
        self.SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
        self.SMTP_USER = os.environ.get("SMTP_USER", "")
        self.SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
        self.MAIL_FROM = os.environ.get("MAIL_FROM", self.SMTP_USER)
        self.ALERT_EMAILS = _list("ALERT_EMAILS")
        # The address the app is served on, for links in emails and calendar events.
        self.APP_URL = os.environ.get("APP_URL", "").rstrip("/")
        self.TESTING = _bool("TESTING")
        # Phase 2: Claude reads proposals. The key is read by the Anthropic SDK from ANTHROPIC_API_KEY.
        self.ANTHROPIC_API_KEY_SET = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.PROPOSAL_MODEL = os.environ.get("PROPOSAL_MODEL", "claude-opus-5-5")
        self.PROPOSAL_EFFORT = os.environ.get("PROPOSAL_EFFORT", "medium")
        # The proposals inbox, read over IMAP (works with Google Workspace, Microsoft 365 and most hosts).
        self.IMAP_HOST = os.environ.get("IMAP_HOST", "")
        self.IMAP_PORT = int(os.environ.get("IMAP_PORT", "993") or 993)
        self.IMAP_USER = os.environ.get("IMAP_USER", "")
        self.IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
        self.IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
        # WhatsApp Business Cloud API (Meta).
        self.WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
        self.WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self.WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        self.WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
        self.WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")

    @property
    def email_in(self):
        return bool(self.IMAP_HOST and self.IMAP_USER and self.IMAP_PASSWORD)

    @property
    def whatsapp(self):
        return bool(self.WHATSAPP_TOKEN and self.WHATSAPP_PHONE_NUMBER_ID)

    def service_account_info(self):
        if not self.GOOGLE_SERVICE_ACCOUNT_JSON:
            return None
        try:
            return json.loads(self.GOOGLE_SERVICE_ACCOUNT_JSON)
        except ValueError:
            return None
