# Billboard Ops

Bookings, creative checks and the owner's dashboard for a network of digital screens. It is a standalone app
with its own Railway project, its own Postgres database and its own logins. The build plan has five phases. This
repository holds:

- **Phase 1:** screens, specs and the booking calendar;
- **Phase 2:** proposal intake;
- **Phase 3:** creative collection and automatic checks;
- **Phase 4:** the play plan, the client portal and going live;
- **Phase 5:** the owner dashboard, hardening and launch.

How the app is secured is in [SECURITY.md](SECURITY.md).

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

## What Phase 3 does

- **Asking for the files.** Confirming a booking queues a file request:
    - a Drive folder for the booking (inside a folder per client, in a Shared Drive), shared with the client's
      email address;
    - a private upload link, which works without a Google account;
    - a deadline: the start date minus a set number of working days (5 by default; the weekend days are a
      setting);
    - a message in the client's language listing each screen's exact spec: size and orientation, exact length,
      file types, frame rate, largest size, sound, and the language rule.

  The message is a draft until a person approves it.
- **Receiving files.** Every route becomes the next numbered version (v1, v2 …) of the booking's file, and is
  checked within minutes:
    - the Drive folder, checked every 2 minutes;
    - the upload link;
    - an email or WhatsApp reply to the request, or a message with media from a known contact of a booking that
      is waiting for files;
    - the team's own upload on the booking page.

  A file is matched to the booked screens whose spec has its exact size. The team can change which screens it is
  for.
- **Measured checks** (`creative_checks.py`, using ffprobe, ffmpeg and Pillow), against each booked screen's spec
  and the booked ad length:
    - file type, codec, and exact size and orientation;
    - length, frame rate, file size and sound;
    - black frames at the start or end, a frozen picture, black borders (letterboxing), and whether the file plays
      all the way through;
    - colours (a CMYK image fails).
- **Content checks** (Claude reads stills taken at each scene change, every 2 seconds, and from the end card):
    - the **language rule**: Arabic only, or Arabic plus English. English only fails, and any other language fails,
      named with its frame;
    - Arabic shown correctly (letters joined, right to left);
    - spelling;
    - whether the brand matches the booked client;
    - text inside the safe area, and readable;
    - the owner's content rules (Settings → General).

  If either Claude or the code sees another language, the check fails. Without a Claude key, these come out as
  "check by eye".
- **The report** (Creative → a file):
    - the file plays in the screen's shape;
    - each check shows what was measured against what is needed, and clicking a time jumps the player there;
    - evidence frames sit beside the content findings;
    - the version history, and what changed since the last version ("2 of 2 earlier problems fixed").
- **Feedback.** The team gets a one-line technical summary, for example "fails 2 of 18 checks: ad length: 12.0s,
  needs 10s; sound …". The client gets the same points in plain words, in their language, each with how to fix
  it. That message is a draft for approval. It can go out automatically when the owner switches that step to
  automatic (Settings → Automation): measured faults and content findings are separate switches.
- **Deciding.** Ops (or a Manager, or the owner) approves a file that passed. Approving a file that failed needs
  a Manager and a reason. Rejecting a file sends the note to the client. When every booked screen has an approved
  file, the booking moves to **Creative approved**.
- **Reminders.** Two days before the deadline, a reminder to the client is drafted. When the start date is within
  the lead time and the files aren't approved, the team gets an alert in the app and by email, once a day.
- **The check test set** (Creative → Check test set). Real files with known right answers, checked again on
  demand. It reports how often the measured and content checks were right, the problems it missed, and its false
  alarms. The bar is every measured check right every time, and no language-rule breach missed.
- **Storage.** Files go to the booking's Drive folder when Drive is set up, otherwise into the database (up to
  150 MB). Railway's build installs ffmpeg (`railpack.json`).

## What Phase 4 does

From a confirmed booking to the end of the campaign, for the team and for the client.

- **The play plan** (a booking → Play plan). How often the ad plays on each screen, by part of the day, weekday
  and date. On confirmation, the agent drafts it. Claude reads the conversation for agreed terms such as "double
  in the evening peak", "weekends only" or "nothing on National Day". Without Claude, or when nothing was said,
  every day part plays the booked frequency.
    - The team edits a grid of day parts per screen, with weekdays and dates with special terms. The page shows
      plays a day, plays in total and how full the busiest hour of the loop gets with everyone else's bookings.
    - A plan that would overfill a loop is refused, listing the hours and the bookings it shares them with. A
      Manager can confirm it anyway with a reason, which is logged.
    - Only a **confirmed** plan changes what a screen's loop is reserved for. Editing a draft never takes loop time
      from another booking. Changing a booking's screens, dates or frequency sends the plan back to draft.
    - Day parts are set in Settings → Automation, as `Morning peak = 6-10 | ذروة الصباح` (the Arabic name is
      what clients see in Arabic).
- **Ready to load.** For each screen, day and hour, a **playlist** shows which approved file plays and how often
  (Playlists, or a booking → Playlist). It exports as Excel, CSV or PDF.
    - A booking's approved files download as a ZIP, one folder per screen, renamed the way the screen software
      expects (the pattern is in Settings → Automation).
    - With Drive set up, approved files are also copied into a **Ready to load** folder in the Shared Drive.
    - Ops ticks **Loaded** for each screen. If a newer file is approved, or the plan changes, after a screen was
      loaded, the tick is cleared and the team is told the screen needs loading again.
- **The stages.** A booking moves on as its files are approved, its plan is confirmed and its screens are loaded:
  Creative approved → Play plan set → Scheduled → **Live** on the start date → Completed.
    - The day before the start, and on the day, a booking that isn't ready raises an alert in the app and by email,
      saying exactly what's missing.
    - On the start date the client gets a "your campaign is live" message.
    - A set number of days before the end (7 by default), the client gets a renewal offer and Sales gets a reminder.
    - After the end, the client gets a wrap-up, with their proof of play if any was added.
- **Proof of play** (a booking → Proof of play). Ops adds photos and videos of the screen, or the screen software's
  play report as CSV or Excel. A report is counted against the plan, for example "1,040 plays counted against
  1,080 planned". The app finds the screen, date, plays and file columns itself, and only counts this booking's
  files when the report names them. Each item can be shown to the client or kept internal.
- **The client portal** (`/portal`). Clients sign in with a one-time link sent to their email; the link works
  once, for 30 minutes, and only its hash is stored. The team gives access on the client's page, can email a link,
  or copy one (valid for 24 hours) to send by WhatsApp. In the portal, a client:
    - sees each booking's status and next step in plain words, in English or Arabic (right to left);
    - accepts a hold, optionally attaching the signed proposal or PO;
    - uploads files and sees the check result in plain words within minutes;
    - approves the play plan and the final preview of their ad, or asks for changes, when the owner turns that on;
    - sees shared proof of play and past campaigns;
    - sends a new request, which lands in the Proposals inbox and is read by the agent like any other.
  A client only ever sees their own company's records: every other page and file link answers "not found", and a
  client is never let into the team's pages. Automated tests try these on every portal page and file link.
- **Notices.** Each person chooses, by topic, what they see in the app and what they also get by email (Notices →
  Choose what I get). By default, "not ready to start" goes by email to the owner, Managers and Ops, renewals to
  Sales, and client actions to the owner, Managers and Sales.
- **Automation** (Settings → Automation). For each step, the owner chooses "ask a person first" or "do it
  automatically":
    - holds;
    - routine client messages;
    - technical feedback;
    - content feedback;
    - final approval of files that pass every check;
    - play plans the agent drafted with high confidence.

  Everything starts as "ask first", and everything the agent does on its own is logged under "agent".
- **Preferred channel.** A client can be set to hear from the team by email or WhatsApp, whatever way they first
  wrote.

## What Phase 5 does

- **The owner dashboard** (the home page) answers "how full are we, what's at risk, and what needs me?" on one page:
    - **Top numbers.** Loop time sold this week and this month (seconds sold of seconds sellable, in each screen's
      open hours), campaigns live and starting, open holds and how many expire this week, files waiting for a
      decision or failing, and Claude's spend. Revenue is added once pricing is decided.
    - **At risk.** Campaigns starting within 7 days that aren't ready, holds expiring within 48 hours, and clients who
      haven't answered for 3 days. Each line says what's missing and who owns the next step.
    - **The next 12 weeks.** Every screen against the next 12 weeks, shaded by how full the loop is; pale cells are
      gaps worth selling. A click opens that week in the calendar.
    - **Pipeline.** Requests, holds, confirmed bookings and campaigns started, this month against last.
    - **Screens map.** Every screen with a map pin, coloured by this week's occupancy; a click opens the screen.
    - Every table exports as Excel, CSV or PDF, or to a tab of the mirror Google Sheet.
- **By client and by screen.** A client's page shows their campaigns, plays booked, how often their files fail and
  why, and Claude's cost for them. A screen's page shows its next 12 weeks, its main clients and its file mistakes.
- **Performance** (owner and Managers). It shows:
    - the median time from a request to a hold, and from confirmation to approved files;
    - the most common file mistakes;
    - what the agent did on its own;
    - how often people changed its work: its reading of proposals, its message drafts and its play plans.

  Use it with the accuracy tests to decide what to switch to automatic.
- **Costs.** Claude's cost by month, by step, by client and now by booking.
- **The Monday summary.** Every Monday from 07:00 local time, the owners get an email with:
    - the numbers;
    - the pipeline;
    - what's at risk;
    - what waits for a decision;
    - the emptiest screens of the week.

  Preview it, or send it now, from the dashboard. Extra recipients are set in Settings → Automation.
- **Hardening:**
    - **Security.** Response headers; rate limits on sign-in links, upload links, sign-in and the WhatsApp webhook;
      `pip-audit` in CI; and tests that open every page as someone not signed in, and as a client. The full review
      is in [SECURITY.md](SECURITY.md).
    - **Backups.** Every night at 02:00 a compressed copy of every record goes to the Shared Drive's **Backups**
      folder (the last 30 are kept), plus a "Download a backup now" button. See "Testing a restore" below.
    - **Monitoring.** Every background job reports how its connection did: Drive, Sheets, Calendar, email in and out,
      client messages, Claude and backups. Settings → Connections shows each one. A connection down for 30 minutes
      alerts the owner and `ALERT_EMAILS`. `/health/full` answers 503 when the worker has stopped or a connection is
      down, for an outside uptime check.
    - **Data rules.** Files and proof are kept 12 months after a campaign ends, and message text 24 months; both are
      settings. Older contents are removed every night, while bookings and the activity log stay. On a client's
      request, the owner can delete everything about that client (their page → Delete all of this client's data).
    - **Load test.** `python -m billboard_ops.loadtest` fills an empty database and times the pages (results below).
- **Launch** (Settings → Launch). The launch checklist checks most items itself, for example:
    - Postgres, the worker, sign-in and email;
    - Claude, Google and Drive;
    - every screen having a spec;
    - the accuracy tests;
    - last night's backup and a tested restore;
    - the team and the test clients.

  The rest are ticked by the owner (Railway backups, the uptime check, training, the Arabic checked), with who and
  when. Settings → Launch also starts the 2-week **parallel run**. **Cut-over** (typing CUT OVER) makes the app
  the single source of truth and stops watching the old sheets.
- **Help.** A one-page guide for each role (owner, Managers, Sales, Ops, and clients in the portal), in English and
  Arabic, ready to print for training.

### Load test results

On a laptop-class machine with Postgres 16: 900 bookings (300 a month for 3 months, as twice an assumed busiest
month of 150), 40 screens, 1,840 booked screens, 1,800 messages. These are median times for one request:

| Page or job | Time |
| --- | --- |
| Dashboard | 0.7 s |
| Calendar (a month) | 0.3 s |
| Playlist (a day, all screens) | 0.4 s |
| Creative | 0.35 s |
| Bookings, a booking, the play plan, the portal | under 0.15 s |
| Monday summary (once a week) | 0.8 s |
| The worker's once-a-minute stage check | 1.2 s |

Run it again with the client's real busiest month once it's known:

```bash
DATABASE_URL=postgresql://… python -m billboard_ops.loadtest --per-month <twice the busiest month> --screens <screens>
```

Run it on an **empty** scratch database only.

### Testing a restore

Do this once before launch, on a scratch Postgres database (for example a second Railway Postgres service):

1. In Settings → Connections, choose **Download a backup now** (or take last night's file from the Drive's Backups
   folder).
2. Make the empty database's tables:

   ```bash
   DATABASE_URL=<the scratch database> alembic upgrade head
   ```

3. Load the backup into it:

   ```bash
   DATABASE_URL=<the scratch database> python -m billboard_ops.backup restore billboard-ops-YYYY-MM-DD.json.gz
   ```

4. Open the app against it and check a few bookings.

The restore records itself in the database it was loaded into. Since that's the scratch database, tick
**Restoring a backup was tested** on Settings → Launch yourself.

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
| `billboard_ops/creatives.py` | Phase 3: requesting, receiving, checking, feedback, approval, reminders, Drive polling |
| `billboard_ops/creative_checks.py`, `creative_store.py`, `creative_eval.py` | The checks themselves; where files are kept; the check test set |
| `billboard_ops/plans.py` | Phase 4: play plans, day parts, the overfill check, Claude's draft, confirming |
| `billboard_ops/lifecycle.py` | Phase 4: readiness, loading, the stages, going live, the day-before alert, renewals, wrap-ups |
| `billboard_ops/loading.py`, `proof.py` | Playlists and their exports, the ZIP and Ready to load folder; proof of play and play reports |
| `billboard_ops/client_messages.py`, `notices.py`, `automation.py` | Stage messages to clients; notices by topic and by email; the automation switches |
| `billboard_ops/views/portal.py`, `portal_text.py` | The client portal and its English and Arabic words |
| `billboard_ops/insights.py`, `summary.py`, `exports.py` | Phase 5: the dashboard's numbers, the Monday summary, exports to Excel, CSV, PDF and Sheets |
| `billboard_ops/security.py`, `monitor.py`, `backup.py`, `retention.py` | Phase 5: headers and rate limits; watching the connections; backups and restore; data rules and deleting a client |
| `billboard_ops/launch.py`, `help_text.py`, `loadtest.py` | Phase 5: the launch checklist and cut-over; the role guides; the load test |
| `billboard_ops/views/`, `templates/`, `static/` | The pages |
| `migrations/` | Alembic migrations, which Railway runs before each deploy |
| `tests/` | The test suite. It runs on SQLite, and on Postgres when `TEST_DATABASE_URL` is set |

## Running it locally

```bash
git clone https://github.com/Krishna-1812/billboards-automation.git && cd billboards-automation
```

```bash
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
For now sign-in is off altogether unless `LOGIN_REQUIRED=1` or `GOOGLE_CLIENT_ID` is set: see the variables table.

## Tests

```bash
python -m pytest tests -q
```

To run the same tests on Postgres, as CI does:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/billboard_test python -m pytest tests -q
```

CI runs both, plus a check that the migrations match the models (`.github/workflows/tests.yml`).

## Deploying on Railway

The app runs as one Railway project with three parts: **Postgres**, **web** and **worker**. Web and worker are
both built from the root of this repo.

1. **New project → Deploy from GitHub repo →** this repository. Then **Add → Database → PostgreSQL**.
2. On the **web** service, open **Settings** and set:
    - **Root Directory:** leave empty (the repo root).
    - **Pre-deploy Command:** `alembic upgrade head`. This builds or updates the tables before each release.
    - **Custom Start Command:** `gunicorn 'billboard_ops:create_app()' --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60`
    - **Healthcheck Path:** `/health`
    - **Networking:** generate a domain, or add the client's own (for example `ops.clientdomain.com`).

   `railway.json` records the same settings. Enter them by hand anyway: config files are deprecated for new
   Railway services.
3. **Add a second service** from the same repo for the **worker**:
    - **Root Directory:** leave empty (the repo root).
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
   | `GOOGLE_CLIENT_ID` | later (web) | A Google OAuth client ID ("Web application"), with the app's address as an authorised JavaScript origin |
   | `LOGIN_REQUIRED` | no | Sign-in is **off for now**: everyone who opens the app acts as the owner, and every page says so. It comes back on by itself once `GOOGLE_CLIENT_ID` is set, or with `LOGIN_REQUIRED=1`. `LOGIN_REQUIRED=0` keeps it off |
   | `OWNER_EMAILS` | yes | The owner's email address. Owners add everyone else in Settings → People. |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | for Sheets and Calendar | The whole JSON key of a Google service account (see below) |
   | `APP_URL` | recommended | For example `https://ops.clientdomain.com`, for links in calendar events, emails and portal sign-in links |
   | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | for email | Without these, reminders show only in the app |
   | `ALERT_EMAILS` | recommended | Who gets an email when something breaks, or a connection is down for 30 minutes |
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

### Connecting Google Drive (creative files)

1. In Google Cloud, also enable the **Google Drive API** for the service account's project.
2. In Google Drive, create a **Shared Drive** (for example "Billboard creatives"). Add the service account's email
   as a **Content manager**. Service accounts have no storage of their own, so files must live in a Shared Drive.
3. Make a folder in it, and paste the folder's link in **Settings → General → Creative**. The booking folders and
   the **Ready to load** folder are made inside it.

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

## Phase 3 checklist ("done when")

- [ ] Confirming a booking makes the file request, with specs, within 1 minute. It goes out once approved.
- [ ] A new file in Drive is checked, with its report ready, within 5 minutes for a typical 10 to 30 second video.
  Drive is checked every 2 minutes.
- [ ] The check test set has about 40 real files. Every measured check is right on all of them, and no
  language-rule breach is missed.
- [ ] Re-checks, reminders and start-date alerts work end to end on a real booking.
- [ ] The cost of each check is recorded against its booking (Admin → Costs).

## Phase 4 checklist ("done when")

- [ ] A full booking runs from proposal to Completed on a real screen, with every step in its history.
- [ ] Play plans can't overfill a loop, and the playlists open correctly in the screen team's tools (Excel, CSV,
  PDF).
- [ ] A test client signs in, sends a request, uploads a file, sees the check result and approves, in Arabic and in
  English.
- [ ] One client can never see another client's data (the portal tests cover every page and file link).
- [ ] The day-before readiness alert catches a booking that is deliberately left unready.
- [ ] SMTP is set up, so sign-in links and alerts actually go out by email.

## Phase 5 checklist ("done when")

- [ ] The owner can answer "how full are we, what's at risk, and what needs me?" from the dashboard in under a minute.
- [ ] The security review (SECURITY.md) is passed: sign-in is on, and its open points are accepted.
- [ ] A backup restore has been tested (Settings → Launch shows it).
- [ ] The 2-week parallel run ends with no differences left unexplained.
- [ ] Every team member and the test clients have been trained (Help has the guides).
- [ ] The old sheet is read-only, cut-over is done, and the app runs the business.
