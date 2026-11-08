# Security review

Reviewed for launch (Phase 5). It covers who can get in, what each person and each client can see, the links that
open without signing in, files, backups, and what's still open. Re-read it whenever sign-in or the portal changes.

## Signing in

- **The team** signs in with Google. The app checks the ID token's signature against its own client ID, and only
  lets in people an owner added (Settings → People), plus the first owners in `OWNER_EMAILS`.
- **The development login** (a plain email box) is refused whenever `GOOGLE_CLIENT_ID` is set.
- **Sign-in is currently switched off** at the owner's request: anyone with the address acts as the owner, and every
  page says so. It comes back on by itself once `GOOGLE_CLIENT_ID` is set. **This must be on before real client data
  goes in.** It's a must-have on Settings → Launch.
- **Clients** sign in to the portal with a one-time link sent to their email:
    - it works once, for 30 minutes;
    - the app keeps only a SHA-256 hash of it;
    - opening the link shows a button, and only pressing it signs in, so mail scanners that open links can't use it
      up;
    - a person can ask for at most 5 links an hour.
  Links the team copies by hand last 24 hours. Removing a person's portal access stops their links working at once.
- **Sessions** are signed cookies: HttpOnly, SameSite=Lax, Secure over HTTPS, and they last 14 days. A person whose
  access is removed is signed out on their next click.

## Who sees what

- **The team:** one table of roles and capabilities (`permissions.py`) decides every action, and the tests check it.
  - Overrides need a Manager and a written reason, which is logged: booking past capacity, approving a failed file,
    confirming a plan that overfills a loop.
  - Settings, people and deleting a client's data are owner-only.
- **Clients:**
  - Every portal lookup is limited to the signed-in client's own company. Anything else answers "not found", so
    nobody can even tell whether another client's booking exists.
  - A signed-in client sent to any team page is redirected to their portal.
  - The tests try every portal page and file link with another client's IDs, plus the team's file links.
- **Proof of play** reaches the client only when the team marks it as shared.

## Links that open without signing in

| Link | Protected by |
| --- | --- |
| `/upload/<token>` (the client's upload page) | A random 32-character token. It only works while the booking is collecting files, and it is rate-limited. |
| `/portal/link/<token>` | As above: one-time use, 30 minutes, only its hash stored, rate-limited |
| `/webhooks/whatsapp` | Meta's `X-Hub-Signature-256` signature, checked with the app secret. Everything is refused while no secret is set. |
| `/health`, `/health/full` | Report only "ok", the database kind and the connections' states: no data |

## Forms, pages and headers

- Every form and every in-page request carries a CSRF token, checked on every POST.
- Pages are templates with automatic escaping. The map builds its pop-ups with DOM text nodes, never as HTML.
- After sign-in and after a language switch, the app only returns to an address inside itself.
- Every response sets:
    - `X-Content-Type-Options: nosniff`;
    - `X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'`;
    - `Referrer-Policy: same-origin`, so tokens in addresses never reach other sites;
    - `Permissions-Policy`;
    - HSTS over HTTPS.
- Portal and upload pages aren't cached.
- The app trusts exactly one proxy, Railway's, for the scheme, host and client address.
- The database is only reached through SQLAlchemy, with no SQL built from user input. The backup restore names
  tables from the app's own model.

## Files

- **Size limits:** forms take up to 20 MB, and creative uploads up to 300 MB. Only video and image files go to the
  checks, and nothing a client sends is ever run.
- **Video tools:** ffprobe and ffmpeg read client files in the worker, with a 10-minute time limit per run. Temporary
  copies are deleted after each check.
- **Storage:** files live in the client's Shared Drive. Each booking's folder is shared only with that client's email.
  Without Drive, files stay in the database and are only served to someone signed in.

## Claude

- **What Claude does:** it reads proposals, ad stills and conversations, and answers in a fixed JSON shape that the
  app checks before using it.
- **What Claude can't do:** anyone can try to write instructions inside a proposal. Claude's answer can only fill in
  that form: it can't send messages, confirm bookings or change settings.
- **Messages to clients** are written from the app's own templates, not by Claude.
- **Acting alone:** holds the agent places on its own are limited to senders who are known contacts of that client,
  and only while the accuracy test passes.

## Data

- **Backups:** every night, to the Shared Drive, keeping the last 30 copies. Railway's Postgres backups keep
  everything else. Restoring is a command, and the launch checklist records when it was last tested.
- **Retention:** files and proof are kept 12 months after a campaign ends, and message text 24 months. Both periods
  are settings, and older data is removed every night.
- **Deleting a client:** on request, the owner can delete everything about one client, after typing the client's name.
- **Secrets** live only in Railway's variables, never in the repository.
- **Activity log:** every change, by a person or by the agent, is in the activity log.

## Checks that run on every push

- the full test suite, on SQLite and on Postgres;
- a check that the migrations match the models;
- `pip-audit` for known vulnerabilities in the dependencies. At the time of this review, none were found.

## Open points (accepted, or to do before launch)

1. **Sign-in is off.** Turn it on (set `GOOGLE_CLIENT_ID`) before real data goes in. *To do.*
2. **Rate limits** are counted in each web process's memory. They reset on deploy, and with two processes the real
   limit is up to double. That's enough against guessing tokens of this length. *Accepted.*
3. **Dependencies** are pinned to minimum versions, not exact ones. `pip-audit` runs on every push; pin exact
   versions if the client's IT policy asks for it. *Accepted.*
4. **A client's Drive folder** gives them write access to that folder. They could delete their own files there; the
   app keeps its record of each version either way. *Accepted.*
5. **Outside uptime check:** someone must point an uptime service (for example UptimeRobot) at `/health/full`, so the
   owner hears about it if the whole service stops. *To do: a must-have on Settings → Launch.*
