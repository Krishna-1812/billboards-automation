# Billboard Ops

Bookings, creative checks and the owner's dashboard for a network of digital screens. This app is separate from
the main platform: it has its own Railway project, its own Postgres database and its own logins. The build plan
has five phases. This folder holds **Phase 1: screens, specs and the booking calendar** and **Phase 2: proposal
intake**.

## What Phase 1 does

- **Screens and specs.** Each screen has its loop (for example 120 seconds of 10-second slots), the share of
  airtime that can be sold, operating hours (including hours past midnight), time zone, map pin and Google
  Calendar. Specs hold the file rules that Phase 3's checks will read. Blackout days and special hours (such as a
  holiday month) can be set per screen or for every screen.
- **The vacancy engine** (`billboard_ops/vacancy.py`). It answers whether there is room in the loop for given
  screens, dates, hours and plays per hour, with one of three answers: fits, fits in part, or doesn't fit. It
  names the bookings in the way, suggests the nearest dates that fit, and lists other screens with the same spec
  in the same area. Holds count against capacity, the same as confirmed bookings.
- **Bookings.** Holds expire after a set number of days, with a reminder first. A Manager confirms them.
  Anything over a screen's capacity is refused unless a Manager overrides it and gives a reason, and the team is
  told. Every change is written to the activity log.
- **Calendar grid.** Screens against days, each cell shaded by how full that day's busiest hour is. Clicking a
  cell shows each hour and who is booked in it.
- **Import from Google Sheets** (or an uploaded .xlsx or .csv file). The steps are:
    1. The app guesses which column is which.
    2. A trial run reads every row and saves nothing. Rows it can't read and bookings that clash are listed.
    3. The real import runs. Importing the same sheet again never creates duplicates.
    4. Existing Google Calendar events are matched to the imported bookings, so they are updated rather than
       duplicated.
- **Mirroring.** Each booking is an all-day event on its screen's Google Calendar, coloured by status. One tab
  of a Google Sheet is rewritten with every booking. During the switch-over, the old sheets are re-read every
  10 minutes, and any direct edit to them is flagged for review.
- **Roles.**

  | Role | What they can do |
  | --- | --- |
  | Owner | Everything |
  | Manager | Confirm, override and import |
  | Sales | Place and manage holds |
  | Ops | Edit screens and specs |
  | Client | Nothing yet. The portal comes in Phase 4. |

- **Languages.** The interface works in English and Arabic, and the layout runs right to left for Arabic. The
  Arabic text should be checked by a native speaker on the client's team.

## What Phase 2 does

- **One inbox for every channel.** The **Proposals** page collects requests from three places:
    - the proposals mailbox, read every minute over IMAP;
    - WhatsApp, through Meta's Cloud API webhook;
    - anything pasted or uploaded by the team: call notes, screenshots, PDFs, messages from other apps.
- **Replies join their conversation.** A client's reply goes into the request it answers, matched by the email
  headers, by sender and subject, or by WhatsApp number within 3 days. Several WhatsApp messages sent in a row are
  read together. The same email arriving twice is stored once.
- **Claude reads every request** (`extract.py`). It gets the whole conversation, including attachments: PDFs,
  scans, photos, and Word and Excel files. It also gets the list of screens and known clients, and the date each
  message arrived, so relative dates like "next Sunday" resolve correctly. It returns a fixed JSON shape:
    - the client and campaign;
    - one line per group of screens, with dates, hours, ad length and frequency;
    - how sure it is of each field, and the words each field came from;
    - what's missing, questions to ask the client, and anything odd.
- **Its answer is checked in code.** Unknown screen codes, past dates and impossible hours lower the confidence.
  The frequency is converted to plays per hour for each screen's loop. Vacancy is checked straight away, with the
  nearest free dates and screens when something doesn't fit.
- **Message content is treated as data.** Anything in a message telling Claude what to do is flagged, never
  followed. Nothing Claude returns can do more than fill in a draft.
- **The review page** shows the conversation on the left. On the right is the draft booking, with confidence
  badges, the client's own words and the vacancy result for each line, and every field can be edited. The
  actions are:
    - **Place hold**
    - **Save changes and check again**
    - **Ask the client**: a question drafted in the client's language
    - **Decline and offer alternatives**: a polite reply naming the free dates and screens
    - **Not a booking request**
    - **Read again**
- **Duplicates.** If the same request arrives by email and on WhatsApp, the two are merged automatically. A
  near-duplicate (same client and screen, dates within two days) is flagged for a person to decide.
- **The Outbox.** Every message to a client is a draft until a person approves it. That covers questions, hold
  confirmations, alternatives and hold-expiry reminders.
    - Email is sent by SMTP as a reply in the client's own thread.
    - WhatsApp messages can only be sent within 24 hours of the client's last message (Meta's rule). Outside that
      window, the message is held back with a note saying to send it by email instead.
    - A pasted request with no email address can't be sent by the app: the team copies the text, sends it, and
      marks it sent.
- **From hold to confirmed.** Before a hold expires, the team is reminded and a reminder to the client is
  drafted. A Manager confirms the booking, and can attach the signed proposal or purchase order.
- **The accuracy test** (Admin → Accuracy test). It holds past requests with the answer the team acted on.
    - Any held proposal can be saved as a case in one click, or a case can be added by hand.
    - A run reads every case again and scores client, screens, dates, hours and frequency.
    - The plan's bar is **screens and dates right in at least 95% of at least 30 cases**.
    - It also runs from the command line: `python -m billboard_ops.evaluate`.
- **Holding automatically** (Settings → General) is off by default. When it's switched on, the agent places a hold
  by itself only if all of these are true:
    - the latest accuracy run passed;
    - the request is complete, and every field is high confidence;
    - every line fits;
    - the client is already known, and the sender is one of that client's known contacts (their email address or
      domain, or their WhatsApp or phone number).

  Confirming a booking, and every message to a client, always need a person.
- **Costs** (Admin → Costs). Every Claude call is logged in `ai_calls` with its tokens, its price, who caused it
  and which client it was for. The page shows the totals by month, by kind and by client. This app has its own
  database, so these costs don't appear in the main platform's `/admin` panel, which reads that platform's
  database.

## Layout

| Path | What it is |
| --- | --- |
| `billboard_ops/__init__.py` | `create_app()`, the web app |
| `billboard_ops/worker.py` | `python -m billboard_ops.worker`, the background worker |
| `billboard_ops/models.py` | Every table |
| `billboard_ops/vacancy.py` | The vacancy engine, built on plain data so it can be tested without a database |
| `billboard_ops/bookings.py` | Creating, changing, confirming and cancelling bookings; hold expiry and reminders |
| `billboard_ops/importer.py`, `parsing.py` | The guided import, and reading messy dates, hours and statuses |
| `billboard_ops/sync.py`, `google_client.py` | Calendar and sheet mirroring, and the watch on the old sheet |
| `billboard_ops/jobs.py`, `mail.py` | The job queue (kept in Postgres) and email |
| `billboard_ops/permissions.py` | Who may do what, in one table |
| `billboard_ops/intake.py` | The proposals inbox: receiving, threading, reading, duplicates, holds, alternatives |
| `billboard_ops/extract.py`, `documents.py`, `ai.py` | What Claude is asked and how its answer is checked; reading attachments; the call and its cost |
| `billboard_ops/channels/` | The IMAP mailbox and the WhatsApp Cloud API |
| `billboard_ops/drafts.py`, `outbox.py` | Client messages in English and Arabic, and sending them once approved |
| `billboard_ops/evaluate.py` | The accuracy test |
| `billboard_ops/views/`, `templates/`, `static/` | The pages |
| `migrations/` | Alembic migrations, which Railway runs before each deploy |
| `tests/` | The test suite. It runs on SQLite, and on Postgres when `TEST_DATABASE_URL` is set |

## Running it locally

```bash
cd apps/billboard-ops
pip install -r requirements.txt
```

```bash
export DEV_LOGIN=1 OWNER_EMAILS=you@example.com
```

```bash
python -m billboard_ops.demo
```

The demo command loads 8 demo screens with bookings. It only runs on an empty database.

```bash
flask --app "billboard_ops:create_app()" run
```

```bash
python -m billboard_ops.worker
```

Run the worker in a second terminal. Without `DATABASE_URL`, both use a local SQLite file. `DEV_LOGIN` shows a
plain email box instead of Google sign-in. It is switched off automatically whenever `GOOGLE_CLIENT_ID` is set.

## Tests

```bash
python -m pytest tests -q
```

To run the same tests on Postgres, as CI does:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/billboard_test python -m pytest tests -q
```

CI runs both, plus a check that the migrations match the models (`.github/workflows/billboard-ops-tests.yml`).

## Deploying on Railway

The app runs as one Railway project with three parts: **Postgres**, **web** and **worker**. Web and worker are
both built from this repo, from the folder `apps/billboard-ops`.

1. **New project → Deploy from GitHub repo →** this repository. Then **Add → Database → PostgreSQL**.
2. On the **web** service, open **Settings** and set:
    - **Root Directory:** `apps/billboard-ops`
    - **Pre-deploy Command:** `alembic upgrade head`. This builds or updates the tables before each release.
    - **Custom Start Command:** `gunicorn 'billboard_ops:create_app()' --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60`
    - **Healthcheck Path:** `/health`
    - **Watch Paths:** `/apps/billboard-ops/**`
    - **Networking:** generate a domain, or add the client's own (for example `ops.clientdomain.com`).

   `railway.json` records the same settings. Enter them by hand anyway: config files are deprecated for new
   Railway services.
3. **Add a second service** from the same repo for the **worker**:
    - **Root Directory:** `apps/billboard-ops`
    - **Custom Start Command:** `python -m billboard_ops.worker`
    - **Healthcheck Path:** leave empty.
    - **Restart Policy:** Always.
    - **Networking:** add no domain.

   The worker's heartbeat shows on `/health` and on Settings → Connections.
4. **Variables.** Give both services the same values, using **Add Reference** for anything secret:

   | Variable | Needed | What it is |
   | --- | --- | --- |
   | `DATABASE_URL` | yes | `${{Postgres.DATABASE_URL}}` |
   | `SECRET_KEY` | yes (web) | A long random string. It signs the login cookies. |
   | `GOOGLE_CLIENT_ID` | yes (web) | A Google OAuth client ID ("Web application"), with the app's address as an authorised JavaScript origin |
   | `OWNER_EMAILS` | yes | The owner's email address. Owners add everyone else in Settings → People. |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | for Sheets and Calendar | The whole JSON key of a Google service account (see below) |
   | `APP_URL` | recommended | For example `https://ops.clientdomain.com`, for links in calendar events and emails |
   | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | for email | Without these, reminders show only in the app |
   | `ALERT_EMAILS` | recommended | Who gets an email when something breaks |
   | `ANTHROPIC_API_KEY` | for Phase 2 | Claude reads the proposals. Without it, proposals arrive and the team fills them in by hand. |
   | `PROPOSAL_MODEL`, `PROPOSAL_EFFORT` | optional | Defaults: `claude-opus-5-5`, `medium`. A cheaper model can be tried here, then checked with the accuracy test. |
   | `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | for email intake | The proposals mailbox. For Google Workspace: `imap.gmail.com`, port 993, and an app password. |
   | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | for WhatsApp | From the Meta app (below) |

5. **Backups.** Turn on Railway's Postgres backups (Postgres service → Backups).

### Connecting WhatsApp

1. In Meta for Developers, create a **Business** app and add the **WhatsApp** product. Add and verify the client's
   business phone number. Meta's business verification can take days to weeks, so start early.
2. Create a **system user** with a permanent token that has the `whatsapp_business_messaging` permission. Put the
   token in `WHATSAPP_TOKEN`, and the number's **Phone number ID** in `WHATSAPP_PHONE_NUMBER_ID`.
3. Put the app's **App secret** in `WHATSAPP_APP_SECRET`, and choose any long random string for
   `WHATSAPP_VERIFY_TOKEN`.
4. Under **WhatsApp → Configuration → Webhook**:
    - callback URL: `https://<the app's address>/webhooks/whatsapp`, as shown in Settings → Connections;
    - verify token: the same string as `WHATSAPP_VERIFY_TOKEN`;
    - subscribe to **messages**.
5. Calls without a valid signature are refused. If `WHATSAPP_APP_SECRET` isn't set, every webhook call is refused.

### Connecting Google Sheets and Calendar

1. In Google Cloud, create a project. Enable the **Google Sheets API** and the **Google Calendar API**.
2. Create a **service account**, add a JSON key, and paste the whole file into `GOOGLE_SERVICE_ACCOUNT_JSON` on
   both services.
3. Share with the service account's email address, which is shown in Settings → Connections:
    - every sheet to import from (viewer access is enough);
    - the mirror sheet (editor);
    - each screen's Google Calendar ("Make changes to events").
4. In the app, set each screen's **Google Calendar ID**, and set the mirror sheet in Settings → General.

## Switch-over checklist (Phase 1 "done when")

- [ ] Every screen and spec is in the app, and the team has checked them. Import specs first, then screens.
- [ ] All bookings from the last 6 months onward are imported, with every clash and unreadable row resolved
  (Import → trial run).
- [ ] The calendar is matched (Import → Calendar matching), and mirroring is started.
- [ ] A team member can check vacancy and place a hold in under a minute.
- [ ] Google Calendar and the mirror sheet show each change within a minute. The worker checks the queue every
  5 seconds.
- [ ] Settings → Sheet changes is empty: nobody has edited the old sheet behind the app's back.

## Phase 2 checklist ("done when")

- [ ] Proposals sent by email, WhatsApp, paste and upload appear in the inbox within 2 minutes. Email is checked
  every minute; WhatsApp waits 30 seconds for the rest of a burst.
- [ ] The accuracy test has 30 to 50 real past proposals, and screens and dates are right in at least 95%.
- [ ] A typical proposal goes from arrival to hold in under 3 minutes of team time.
- [ ] Holds expire on their own. The team and the client are reminded first; the client's reminder goes out once
  approved.
- [ ] The cost of every Claude call is recorded against its proposal (Admin → Costs).
