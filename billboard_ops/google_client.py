"""Google Sheets and Calendar through a service account.

The client shares their sheets and calendars with the service account's email address (shown in Settings →
Connections); nothing else needs a Google login. Tests swap in a fake with `set_client()`.
"""

import logging

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/drive"]
FOLDER = "application/vnd.google-apps.folder"

_override = None


class NotConnected(Exception):
    pass


def set_client(client):
    """Tests: use a fake instead of Google."""
    global _override
    _override = client


def get_client(config):
    if _override is not None:
        return _override
    info = config.service_account_info()
    if not info:
        raise NotConnected("Google isn't connected: set GOOGLE_SERVICE_ACCOUNT_JSON.")
    return GoogleClient(info)


def service_account_email(config):
    info = config.service_account_info() or {}
    return info.get("client_email", "")


def spreadsheet_id_from(text):
    """Accepts a full sheet link or a bare ID."""
    text = (text or "").strip()
    if "/d/" in text:
        text = text.split("/d/", 1)[1].split("/", 1)[0]
    return text.split("?", 1)[0].split("#", 1)[0]


class DriveMixin:
    """Drive, through a Shared Drive the service account belongs to (service accounts have no storage of their
    own, so files must live in a Shared Drive). Every call says supportsAllDrives."""

    def _drive_api(self):
        if self._drive is None:
            from googleapiclient.discovery import build
            self._drive = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._drive

    def drive_folder(self, name, parent_id):
        """The folder called `name` inside `parent_id`, made if it doesn't exist."""
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{safe}' and '{parent_id}' in parents and mimeType = '{FOLDER}' and trashed = false"
        res = self._drive_api().files().list(q=q, fields="files(id)", supportsAllDrives=True,
                                             includeItemsFromAllDrives=True, corpora="allDrives").execute()
        if res.get("files"):
            return res["files"][0]["id"]
        body = {"name": name, "mimeType": FOLDER, "parents": [parent_id]}
        return self._drive_api().files().create(body=body, fields="id", supportsAllDrives=True).execute()["id"]

    def drive_share(self, file_id, email, role="writer"):
        self._drive_api().permissions().create(
            fileId=file_id, body={"type": "user", "role": role, "emailAddress": email},
            sendNotificationEmail=False, supportsAllDrives=True).execute()

    def drive_list(self, folder_id):
        out, token = [], None
        while True:
            res = self._drive_api().files().list(
                q=f"'{folder_id}' in parents and trashed = false and mimeType != '{FOLDER}'",
                fields="nextPageToken, files(id, name, mimeType, size, md5Checksum, createdTime)",
                supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
                pageToken=token).execute()
            out.extend(res.get("files", []))
            token = res.get("nextPageToken")
            if not token:
                return out

    def drive_download(self, file_id, path):
        from googleapiclient.http import MediaIoBaseDownload
        req = self._drive_api().files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(path, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()

    def drive_upload(self, folder_id, filename, path, mimetype):
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(path, mimetype=mimetype or "application/octet-stream", resumable=True)
        return self._drive_api().files().create(body={"name": filename, "parents": [folder_id]}, media_body=media,
                                                fields="id", supportsAllDrives=True).execute()["id"]

    def drive_copy(self, file_id, folder_id, name):
        """A copy of a Drive file in another folder, under a new name (the Ready to load folder, Phase 4)."""
        return self._drive_api().files().copy(fileId=file_id, body={"name": name, "parents": [folder_id]},
                                              fields="id", supportsAllDrives=True).execute()["id"]


def folder_url(folder_id):
    return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""


class GoogleClient(DriveMixin):
    def __init__(self, info):
        from google.oauth2 import service_account
        self._creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self._sheets = None
        self._calendar = None
        self._drive = None

    def _sheets_api(self):
        if self._sheets is None:
            from googleapiclient.discovery import build
            self._sheets = build("sheets", "v4", credentials=self._creds, cache_discovery=False)
        return self._sheets

    def _calendar_api(self):
        if self._calendar is None:
            from googleapiclient.discovery import build
            self._calendar = build("calendar", "v3", credentials=self._creds, cache_discovery=False)
        return self._calendar

    # Sheets
    def sheet_tabs(self, spreadsheet_id):
        meta = self._sheets_api().spreadsheets().get(spreadsheetId=spreadsheet_id,
                                                     fields="properties.title,sheets.properties.title").execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]

    def read_values(self, spreadsheet_id, tab):
        res = self._sheets_api().spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=_quote(tab), valueRenderOption="FORMATTED_VALUE").execute()
        return res.get("values", [])

    def write_values(self, spreadsheet_id, tab, rows):
        """Replace a tab's contents, creating the tab if needed."""
        api = self._sheets_api().spreadsheets()
        if tab not in self.sheet_tabs(spreadsheet_id):
            api.batchUpdate(spreadsheetId=spreadsheet_id,
                            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
        api.values().clear(spreadsheetId=spreadsheet_id, range=_quote(tab)).execute()
        api.values().update(spreadsheetId=spreadsheet_id, range=_quote(tab) + "!A1",
                            valueInputOption="RAW", body={"values": rows}).execute()

    # Calendar
    def list_events(self, calendar_id, time_min, time_max):
        out, token = [], None
        while True:
            res = self._calendar_api().events().list(
                calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True,
                maxResults=250, pageToken=token).execute()
            out.extend(res.get("items", []))
            token = res.get("nextPageToken")
            if not token:
                return out

    def upsert_event(self, calendar_id, event_id, body):
        events = self._calendar_api().events()
        if event_id:
            try:
                return events.update(calendarId=calendar_id, eventId=event_id, body=body).execute()["id"]
            except Exception as e:  # noqa: BLE001 - the event was deleted by hand; make a new one
                if "404" not in str(e) and "410" not in str(e):
                    raise
        return events.insert(calendarId=calendar_id, body=body).execute()["id"]

    def delete_event(self, calendar_id, event_id):
        try:
            self._calendar_api().events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as e:  # noqa: BLE001
            if "404" not in str(e) and "410" not in str(e):
                raise


def _quote(tab):
    return "'" + tab.replace("'", "''") + "'"
