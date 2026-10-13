# DAQ Team Room Display — Implementation Plan

> **Read this first.** This is the original plan, kept as written. What was built differs
> in a few places, and those places win:
> - **Events come from the team's Google Calendar**, not from admin pages (2026-09-29).
>   §5.1's events table, §7 Step 2.3 and the recurrence rules in §6 no longer apply.
> - **Photos come from a Google Drive folder**, not from uploads on the admin page
>   (2026-10-06). §5.1's photos table and §7 Step 2.4's upload routes no longer apply; the
>   photo pipeline (resize, EXIF removal) still does.
> - The web app runs as a service account, `displayapp`, not as `display` (§7 2.5, §8 3.2).
> - The screen's files are rewritten every 30 seconds by an always-on service, not daily.
> - The screen has no "Updated N min ago" line and no type badges.
>
> Every departure, with its reason, is listed in [SPEC.md §11](SPEC.md#11-decisions-made-while-building-not-spelled-out-in-planmd).
> The Google Calendar design is in [decisions/](decisions/).

Formula SAE DAQ new-member project. A Raspberry Pi in the team room boots straight into
a full-screen web page showing upcoming events, a competition countdown, and rotating
team photos. Schedule and photos are managed from a phone-friendly admin page hosted on
the Pi and reached remotely over Tailscale.

---

## 0. How to use this document (read first)

- Work **one phase at a time, in order**, unless the phase says it can run in parallel.
  Each step lists **Tasks** and **Acceptance tests**. Do not start the next step until
  every acceptance test for the current one passes or is explicitly deferred with a note.
- Tests marked **(auto)** become `pytest` tests under `tests/`. Tests marked **(manual)**
  go into `docs/TESTING.md` as a checklist with the same IDs, so a human can run them on
  the real hardware.
- Anything that needs the physical Pi (Phase 3 onward) cannot be run here. For those
  steps, produce the config files, scripts, and a numbered checklist; do not simulate.
- **Guardrails** (do not deviate without asking):
  - Display page is vanilla HTML/CSS/JS. No framework, no bundler, no build step.
  - Backend is Python 3.11+, Flask, stdlib `sqlite3` (WAL mode), Jinja templates. No ORM
    unless there is a concrete reason.
  - The display page must work with **zero external network requests**. Self-host fonts.
  - The display page must **never depend on Flask being up** — it reads static JSON that
    Flask regenerates on every write.
  - Secrets live only in `/etc/display/env`. Never commit a secret, password hash, or
    Tailscale key. Add `.env*` and `*.db` to `.gitignore` on day one.
  - Store event times as **local wall-clock ISO strings without timezone**
    (`2026-10-14T18:30`). The Pi's OS timezone is America/New_York. No UTC conversion
    anywhere in the app.
  - Prefer boring and readable over clever. The next maintainer is a new DAQ member.
- Fill in every `TODO(owen)` in §2.2 before starting Phase 2.

---

## 1. Project brief (summary)

**Goal:** a screen that always shows what's coming up — competition dates, meeting
times, deadlines — plus team photos, so nobody digs through the group chat.

**Deliverables**
1. A working webapp displaying upcoming events and team photos.
2. A Raspberry Pi that boots directly into the webapp, unattended, and recovers from
   power loss.
3. Documentation for updating content and troubleshooting.

**Provided:** a Raspberry Pi and a display; existing schedule information; team photos.

---

## 2. Decisions

### 2.1 Locked

| Area | Decision | Why |
|---|---|---|
| Event data source | Entered by hand in an **admin page** on the Pi (SQLite is the source of truth) | Team's choice; no external calendar dependency |
| Hosting | **On the Pi**, `http://localhost` | Survives Wi-Fi outages; nothing external to maintain |
| Stack | **Flask + SQLite + Jinja** (admin), **vanilla HTML/CSS/JS** (display), **nginx** in front, **gunicorn** under systemd | Python is the team's common language; no build step; one-file DB |
| Display/Flask coupling | Admin writes → SQLite → regenerate static `events.json` / `photos.json` / `settings.json`; display reads only static files | Screen keeps working if Flask dies |
| Remote access | **Tailscale**, admin page exposed via `tailscale serve` (HTTPS on the tailnet); nginx and gunicorn bind to `127.0.0.1` only | No port forwarding; identity header gives an audit trail |
| Photos | **Uploaded through the admin page**, resized server-side | No Google/rclone dependency |
| Auth | One shared team password (hashed), Flask session, CSRF, login rate limit; `Tailscale-User-Login` header logged on every edit | Simple, plus an audit trail for free |
| Recurrence | "Repeat weekly until <date>" **materializes** one row per week sharing a `series_id` | No recurrence logic at display time; cancel = delete a row |
| Orientation | **Undecided** — build both layouts; decide in Phase 4 before mounting | — |
| Backups | Nightly `sqlite3 .backup` + photos tarball to a **USB stick in the Pi**, keep last 14; SD-card image in team Drive after first stable week | SD card is the only copy of the schedule |

### 2.2 Open — fill in before Phase 2

```
TODO(owen) Pi model and RAM:            ____________  (Pi 4 2GB+ or Pi 5 recommended)
TODO(owen) Display (TV/monitor, size, resolution, HDMI?): ____________
TODO(owen) Team room network (Ethernet jack / Wi-Fi only / registration required?): ____________
TODO(owen) Competition name(s) + date(s) for countdown: ____________
TODO(owen) Team colors / logo file:      ____________
TODO(owen) Number of admins (<= 6 for Tailscale Personal) and schedule owner: ____________
TODO(owen) Tailscale account to create the tailnet with (must be Gmail/GitHub, NOT uconn.edu): ____________
```

### 2.3 Defaults (all are settings/config, not code)

| Setting | Default |
|---|---|
| `photo_interval_s` | 15 |
| Display poll interval | 60 s |
| Nightly page reload | 04:00 |
| Nightly Pi reboot | 04:05 |
| Nightly backup | 03:30 |
| Stale badge threshold | `generated_at` older than 60 min |
| Published event window | everything with `end_at` (or `start_at`) after yesterday |
| Upcoming list length | 7, then "+N more this month" |
| Upload cap | 20 MB per file (nginx `client_max_body_size 25m`) |
| Photo output | JPEG, q85, max 1920 px on the long side, EXIF stripped |
| Photo cap | newest 200 enabled photos |
| `font_scale` | 1.0 (tuned in the room) |

---

## 3. Architecture

```
 phone/laptop (admin, on tailnet)
        │  https://daq-display.<tailnet>.ts.net/admin
        ▼
 tailscaled ── tailscale serve ──► nginx :80 (127.0.0.1 only)
                                     │  /admin, /admin/*  ──► gunicorn/Flask :8000 (127.0.0.1)
                                     │                            │ writes
                                     │                            ▼
                                     │                     display.db (SQLite, WAL)
                                     │                            │ publish() after every write
                                     │                            ▼
                                     │  /, /data/*.json, /photos/*  ◄── static files in web/
                                     ▼
 Chromium --kiosk http://localhost  (same Pi, labwc session)
```

**Failure modes and intended behavior**

| Failure | Display behavior |
|---|---|
| Flask crashes | Unaffected; systemd restarts it |
| nginx down | Chromium shows error; `kiosk.sh` loop reloads; systemd restarts nginx |
| Wi-Fi/Tailscale down | Display unaffected (all local); admin unreachable until back |
| Pi boots without network/NTP | Countdown hidden (`time_synced: false`); rest of page normal |
| Power pulled mid-write | JSON written via temp-file + `os.replace`, SQLite WAL → no corruption |
| Chromium exits | `kiosk.sh` relaunches within ~5 s |
| Kernel hang | Hardware watchdog reboots within ~30 s |
| Memory growth | Nightly page reload + nightly reboot |

---

## 4. Repo layout

```
team-display/
  app/
    __init__.py        # create_app() factory; loads /etc/display/env or .env
    db.py              # sqlite3 connection, WAL, migrations (schema.sql)
    schema.sql
    auth.py            # login, logout, login_required, rate limit
    models.py          # events/photos/settings/audit CRUD
    publish.py         # regenerate web/data/*.json atomically
    photos.py          # validate, orient, strip EXIF, resize, save
    status.py          # uptime, disk, temp, service states, restart/reboot
    admin/
      routes.py
      templates/       # base.html, login.html, events.html, event_form.html,
                       # photos.html, settings.html, status.html
      static/          # admin.css (mobile-first), admin.js (minimal)
  web/                 # served by nginx as-is
    index.html
    style.css          # landscape default + @media (orientation: portrait)
    app.js
    fonts/
    data/              # GENERATED: events.json, photos.json, settings.json (gitignored)
    photos/            # GENERATED: resized uploads (gitignored)
  scripts/
    init_db.py
    set_password.py    # writes ADMIN_PASSWORD_HASH line for /etc/display/env
    publish.py         # CLI: regenerate JSON (used at boot and nightly)
    backup.sh
  pi/
    nginx/display.conf
    systemd/display-admin.service
    systemd/display-publish.service + .timer   # nightly re-publish
    systemd/display-backup.service + .timer
    systemd/display-netcheck.service + .timer
    kiosk.sh
    labwc-autostart                            # copied to ~/.config/labwc/autostart
    sudoers-display                            # two whitelisted commands
    config.txt.notes.md
    env.example                                # template for /etc/display/env
  tests/
    conftest.py        # temp DB, app fixture, test client, tmp web/ dir
    test_auth.py  test_models.py  test_publish.py  test_photos.py  test_admin.py
  fixtures/
    events.schema.json
    events_normal.json  events_empty.json  events_40.json  events_malformed.json
    settings.json  photos.json
    img_12mp.jpg  img_portrait_exif.jpg  img_evil.jpg(not an image)  img_30mb.jpg
  docs/
    SETUP.md  UPDATING.md  TROUBLESHOOTING.md  TESTING.md
  requirements.txt  .gitignore  README.md
```

---

## 5. Data model and contracts

### 5.1 SQLite schema (`app/schema.sql`)

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE events (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  type        TEXT NOT NULL CHECK (type IN ('meeting','deadline','competition','social','other')),
  start_at    TEXT NOT NULL,          -- 'YYYY-MM-DDTHH:MM' local wall clock
  end_at      TEXT,                   -- nullable; for all_day, may equal start date
  all_day     INTEGER NOT NULL DEFAULT 0,
  location    TEXT,
  notes       TEXT,
  series_id   TEXT,                   -- uuid shared by a weekly series
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX idx_events_start ON events(start_at);

CREATE TABLE photos (
  id          INTEGER PRIMARY KEY,
  filename    TEXT NOT NULL UNIQUE,   -- random name, .jpg
  caption     TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  uploaded_at TEXT NOT NULL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
-- keys: competition_name, competition_date, photo_interval_s, font_scale, banner_text

CREATE TABLE audit_log (
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  who      TEXT,                      -- Tailscale-User-Login header or 'local'
  action   TEXT NOT NULL,             -- e.g. event.create, photo.delete, settings.update
  target   TEXT,
  detail   TEXT
);
```

### 5.2 Published JSON contracts (`web/data/`)

`events.json`
```json
{
  "generated_at": "2026-09-22T14:10:00",
  "time_synced": true,
  "events": [
    { "id": 12, "title": "Weekly Meeting", "type": "meeting",
      "start": "2026-09-24T18:30", "end": "2026-09-24T20:00",
      "all_day": false, "location": "Team Room", "notes": "" }
  ]
}
```
Sorted by `start`. Includes every event whose `end` (or `start` if no end) is after
yesterday 00:00. Validated against `fixtures/events.schema.json`.

`photos.json`
```json
{ "generated_at": "...", "photos": [ { "file": "a1b2c3.jpg", "caption": "" } ] }
```
Enabled photos only, newest first, capped at 200.

`settings.json`
```json
{ "competition_name": "FSAE Michigan", "competition_date": "2027-05-12",
  "photo_interval_s": 15, "font_scale": 1.0, "banner_text": "" }
```

### 5.3 `publish()` semantics

- Called after **every** successful write in the admin app, at service start
  (`ExecStartPost`), and nightly by `display-publish.timer`.
- Writes each file to `web/data/<name>.json.tmp` then `os.replace()` → readers never see
  a partial file.
- `time_synced` comes from `timedatectl show -p NTPSynchronized --value` (`true` if the
  command is unavailable, e.g. in tests, unless overridden).
- On any exception: log, leave existing files untouched, re-raise so the route returns
  an error — but the DB commit must already be durable.

---

## 6. Phase 1 — Plan the webapp

### Step 1.1 Decide what's on screen and sketch it

**Tasks**
1. Screen regions, in priority order (what survives if space is tight):
   1. Header: clock + date, team logo, competition countdown ("FSAE Michigan in 231 days").
   2. "Next up" hero card: the single next event, large, relative time
      ("in 2 h", "Tomorrow 6:30 PM"), location.
   3. Upcoming list: next 7 events within 30 days, grouped by day, type badge;
      `deadline` renders as "Due".
   4. Photo panel: crossfade every `photo_interval_s`.
   5. Footer: "Updated 4 min ago" + status dot; optional banner text above.
2. Edge rules (write them into `docs/SPEC.md` or the top of `app.js`):
   - No events → friendly empty state.
   - An event leaves the list when it **ends**, not when it starts.
   - All-day and multi-day events render as ranges, one row.
   - More than 7 upcoming → "+N more this month".
   - `banner_text` non-empty → banner row appears; layout reflows.
   - `competition_date` empty or `time_synced: false` → countdown hidden.
   - No enabled photos → team logo placeholder.
3. Produce a mockup at the display's real resolution, landscape and portrait.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 1.1.1 (manual) | Show mockup on a monitor from the farthest seat in the team room | Hero card and list readable (rule of thumb: ~1 in of text height per 10 ft) |
| 1.1.2 (manual) | Ask 3 members "when's the next meeting?" glancing at the mockup | Each answers correctly within ~3 s |
| 1.1.3 (manual) | Walk every edge rule against the mockup | Each rule has a drawn state, both orientations |

### Step 1.2 Data model and admin spec review

**Tasks**
1. Confirm §5 covers every field the mockup displays; nothing is computed at display
   time except "is it upcoming / has it ended".
2. Write `fixtures/events.schema.json`; both display and backend validate against it.
3. Walk the recurrence design with one real case: weekly meeting, one week cancelled,
   time changes mid-semester.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 1.2.1 | Map every displayed field to a column or setting | No gaps |
| 1.2.2 (auto) | Validate all `fixtures/events_*.json` (except malformed) against the schema | Pass; malformed fails |
| 1.2.3 | Recurrence walkthrough | Each case achievable in ≤ 3 admin actions |

---

## 7. Phase 2 — Build the webapp

### Step 2.1 Repo scaffold

**Tasks**
1. Create the layout in §4, `.gitignore` (`.env*`, `*.db*`, `web/data/`, `web/photos/`,
   `.venv/`), `requirements.txt` (flask, flask-wtf, gunicorn, pillow, jsonschema,
   pytest, pytest-flask or plain client fixture), `README.md` with a 5-line quickstart.
2. `create_app()` factory with config from env; `db.py` opens SQLite in WAL mode and
   applies `schema.sql` if empty.
3. `tests/conftest.py`: temp DB, temp `web/` dir, app + client fixtures, a fixed "now".
4. `scripts/init_db.py`, `scripts/set_password.py`.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 2.1.1 (manual) | Fresh clone → `pip install -r requirements.txt` → `python scripts/init_db.py` → `flask run` | `/admin` shows a login page; `python -m http.server` in `web/` with a fixture copied to `data/` renders the display |
| 2.1.2 (auto) | `pytest` | Runs and passes (smoke test) |
| 2.1.3 (auto) | `git ls-files` | Contains no `.db`, `.env`, or generated JSON/photos |

### Step 2.2 Display page (`web/`)

**Tasks**
1. CSS grid sized in `vw`/`vh`; landscape default, `@media (orientation: portrait)`
   stacks hero → list → photos. `--font-scale` CSS variable from `settings.json`.
2. Self-hosted fonts in `web/fonts/`. No CDN, no analytics, no external requests.
3. Clock ticks every 1 s; relative times recompute every 60 s.
4. Poll `data/events.json?t=<ts>`, `settings.json`, `photos.json` every 60 s. On fetch
   or parse failure: keep current DOM, set stale flag, `console.error`.
5. Stale badge when `generated_at` older than 60 min.
6. Countdown from `competition_date`; hidden when unset or `time_synced: false`.
7. Photo rotation: two stacked `<img>`, opacity crossfade, preload next, skip on
   `onerror`, shuffle order per page load. Reuse elements; never append unbounded DOM.
8. `location.reload()` daily at 04:00.
9. Keep GPU cost low: no `backdrop-filter`, no large shadows.
10. Consult the `modern-web-guidance` and `frontend-design` skills before writing CSS.

**Acceptance tests** (manual unless noted; run in desktop Chromium with fixtures)

| ID | Do this | Pass when |
|---|---|---|
| 2.2.1 | Load `events_empty.json` | Empty state; no console errors |
| 2.2.2 | Load `events_40.json` | List truncates to 7 with "+N more" |
| 2.2.3 | Swap in `events_malformed.json` mid-session | Previous render stays; stale badge; error logged; no crash |
| 2.2.4 | Mock `Date` to 1 min after an event's end | Event disappears without reload |
| 2.2.5 | Fixture with all-day and 3-day events | Rendered as ranges, one row each |
| 2.2.6 | Viewport 1920×1080, 1280×720, 1080×1920 | No overflow, no scrollbars, nothing clipped |
| 2.2.7 | 24 h soak, Chromium Task Manager | Tab memory grows < 50 MB |
| 2.2.8 | DevTools → Offline 20 min | Content stays; stale badge appears at threshold (mocked) |
| 2.2.9 | Mock `Date` across midnight and across 2026-11-01 (DST end) | Clock, date, countdown correct |
| 2.2.10 | DevTools Network, filter non-localhost | Zero external requests |
| 2.2.11 | `time_synced: false` in fixture | Countdown hidden; rest normal |
| 2.2.12 | Set `banner_text` | Banner shows; layout reflows without overlap |
| 2.2.13 | `font_scale` 0.8 → 1.3 | Scales proportionally; no overflow at 1080p |
| 2.2.14 | `photos.json` with zero photos | Logo placeholder; rotation loop does not error |

### Step 2.3 Admin backend (`app/`)

**Tasks**
1. Routes: `/admin/login`, `/admin/logout`, `/admin` (upcoming events; past collapsed),
   `/admin/events/new`, `/admin/events/<id>/edit`, `/admin/events/<id>/delete`,
   `/admin/events/<id>/duplicate`, `/admin/series/<series_id>/delete-future`,
   `/admin/photos` (+ upload/delete/toggle), `/admin/settings`, `/admin/status`.
2. Forms via Flask-WTF (CSRF on every POST). Validation: title required; `end_at` ≥
   `start_at`; type in enum; `repeat_weekly_until` ≥ start date and ≤ 52 weeks out.
3. Weekly repeat materializes rows at save time with a shared `series_id`; same
   wall-clock time each week.
4. Auth: `ADMIN_PASSWORD_HASH` (werkzeug) from env; session cookie (`HttpOnly`,
   `SameSite=Lax`, `Secure` when `X-Forwarded-Proto: https`); login rate limit 5 per
   10 min per IP (in-memory is fine).
5. Audit: every mutating route writes `audit_log` with `who` = `Tailscale-User-Login`
   header if present else `'local'`.
6. Every mutating route calls `publish()` on success.
7. Templates mobile-first: large tap targets, native `datetime-local` inputs, works on a
   phone at 375 px wide.
8. `gunicorn -w 2 -b 127.0.0.1:8000 'app:create_app()'`.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 2.3.1 (auto) | POST wrong password ×6 | Rejected; 6th rate-limited; no session cookie |
| 2.3.2 (auto) | GET `/admin` unauthenticated | Redirect to login |
| 2.3.3 (auto) | POST event form without CSRF token | 400 |
| 2.3.4 (auto) | `end_at` < `start_at`; empty title; bad type | Validation error; no row written |
| 2.3.5 (auto) | Weekly repeat, 10 weeks, crossing 2026-11-01 | 10 rows, same `series_id`, 7 days apart, same HH:MM |
| 2.3.6 (auto) | Delete one instance; delete-future from another | Row counts as expected; other series untouched |
| 2.3.7 (auto) | Title `<script>alert(1)</script>` and `'; DROP TABLE events;--` | Stored verbatim; rendered escaped in admin HTML; present verbatim in JSON |
| 2.3.8 (auto) | Any write | `events.json` regenerated, schema-valid, `generated_at` advanced |
| 2.3.9 (auto) | Force `os.replace` to fail (monkeypatch) | DB row committed; old JSON intact; error logged; route returns 500 |
| 2.3.10 (auto) | Write with `Tailscale-User-Login: alice@example.com` header | `audit_log.who == 'alice@example.com'` |
| 2.3.11 (auto) | Two concurrent writes (threads) | Both committed; `PRAGMA integrity_check` ok |
| 2.3.12 (manual) | Add an event from a phone | On screen within 60 s |
| 2.3.13 (manual) | `sudo systemctl kill display-admin` | Display unaffected; service back < 10 s |

### Step 2.4 Photos (`app/photos.py`, `/admin/photos`)

**Tasks**
1. Multi-file upload. Server side: extension allowlist (`jpg jpeg png webp heic?` —
   decide HEIC: convert via `pillow-heif` or reject with a clear message), 20 MB cap,
   `Image.verify()` then reopen, apply EXIF orientation (`ImageOps.exif_transpose`),
   strip EXIF, resize to ≤ 1920 px long side, save JPEG q85 under a random filename in
   `web/photos/`, insert row, `publish()`.
2. Grid view with delete and enable/disable toggles; caption optional.
3. Delete removes file and row, then `publish()`.
4. Cap enabled photos in `photos.json` at newest 200.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 2.4.1 (auto) | Upload `img_12mp.jpg` | Output < 500 KB, ≤ 1920 px, no EXIF |
| 2.4.2 (auto) | Upload `img_portrait_exif.jpg` | Output upright |
| 2.4.3 (auto) | Upload `img_evil.jpg` (not an image) | Rejected; nothing written |
| 2.4.4 (auto) | Upload > 20 MB | 413 with friendly message |
| 2.4.5 (auto) | Delete a photo | File gone, row gone, `photos.json` updated |
| 2.4.6 (auto) | Disable a photo | Absent from `photos.json`, file retained |
| 2.4.7 (manual) | Upload 10 photos at once from a phone on room Wi-Fi | All in grid; display rotates them within one poll |
| 2.4.8 (manual) | Disable all photos | Logo placeholder; no crash |

### Step 2.5 Tailscale exposure and status panel

**Tasks**
1. `pi/` docs + commands: install Tailscale on the Pi; `tailscale up`; in admin console
   **disable key expiry** for the Pi node; `tailscale serve --bg https / http://127.0.0.1:80`
   (verify exact syntax against current Tailscale docs at build time). Admin URL becomes
   `https://daq-display.<tailnet>.ts.net/admin`.
2. nginx and gunicorn bind `127.0.0.1` only; nginx trusts `X-Forwarded-Proto` and passes
   `Tailscale-User-Login` through to Flask.
3. Tailnet: create with a Gmail/GitHub identity (Personal plan: free, ≤ 6 users; a custom
   domain triggers a business trial). Admins join with the team account or via a node
   share from the owner.
4. `/admin/status`: uptime, disk free, CPU temp (`vcgencmd measure_temp`), last publish
   time, last backup result, `systemctl is-active` for nginx/display-admin/tailscaled,
   whether `chromium` is running. Buttons: **Restart display** (`sudo /usr/local/bin/display-restart`
   → `pkill chromium`) and **Reboot Pi** (`sudo /sbin/reboot`), each behind a confirm.
5. `pi/sudoers-display`: `display ALL=(root) NOPASSWD: /usr/local/bin/display-restart, /sbin/reboot`
   and nothing else.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 2.5.1 (manual) | From a phone on cellular, open the `.ts.net` URL | HTTPS, valid cert, login page |
| 2.5.2 (manual) | `ss -tlnp` on the Pi | nginx/gunicorn only on `127.0.0.1`; only tailscaled external |
| 2.5.3 (manual) | `curl http://<pi-lan-ip>/admin` from campus LAN | Connection refused |
| 2.5.4 (manual) | Click "Restart display" remotely | Screen blanks and returns < 15 s |
| 2.5.5 (manual) | Click "Reboot Pi" remotely | Back on screen < 2 min; admin reachable again |
| 2.5.6 (manual) | `sudo -l` as `display` | Exactly the two whitelisted commands |
| 2.5.7 (manual) | Edit an event, view audit log | Editor's Tailscale login and timestamp recorded |
| 2.5.8 (manual) | Admin console → Pi node | Key expiry disabled |
| 2.5.9 (manual) | Reboot Pi | `tailscale status` connected < 60 s, no login prompt |
| 2.5.10 (manual) | Remove a test user from the tailnet | They can no longer load the admin URL |

---

## 8. Phase 3 — Raspberry Pi

### Step 3.1 OS and base configuration

**Tasks** (produce `docs/SETUP.md` §1 as a numbered checklist)
1. Raspberry Pi Imager → Raspberry Pi OS **64-bit with desktop**. Pre-set hostname
   `daq-display`, user `display`, SSH public key, Wi-Fi (if used), locale, timezone
   `America/New_York`.
2. `sudo apt update && sudo apt full-upgrade -y`; install `nginx python3-venv
   python3-pip sqlite3 chromium` (+ Tailscale via its apt repo).
3. `raspi-config`: autologin to desktop; Display Options → screen blanking **off**;
   expand filesystem.
4. Disable Bluetooth and unused services. Do **not** enable unattended-upgrades.
5. Power: official PSU for the board (Pi 5 = 27 W USB-C). Never a TV USB port.
6. Static DHCP reservation if Ethernet is available.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 3.1.1 | Reboot with no peripherals | Desktop auto-logs in, no prompts |
| 3.1.2 | `timedatectl` | NTP synced; TZ America/New_York |
| 3.1.3 | `ssh display@daq-display.local` from the room; Tailscale SSH from off campus | Both work |
| 3.1.4 | `vcgencmd get_throttled` after 10 min | `0x0` |

### Step 3.2 Deploy the app

**Tasks**
1. `git clone` to `/srv/display`; `python3 -m venv .venv`; install requirements.
2. `/etc/display/env` (mode 600, owner root:display): `SECRET_KEY`,
   `ADMIN_PASSWORD_HASH` (from `scripts/set_password.py`), `DISPLAY_DB=/srv/display/data/display.db`,
   `DISPLAY_WEB=/srv/display/web`.
3. `scripts/init_db.py`; seed settings.
4. Install `pi/nginx/display.conf` → `/etc/nginx/sites-enabled/display`:
   - `listen 127.0.0.1:80;` `root /srv/display/web;`
   - `location /data/ { add_header Cache-Control "no-cache"; }`
   - `location /admin { proxy_pass http://127.0.0.1:8000; proxy_set_header Tailscale-User-Login $http_tailscale_user_login; proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto; }`
   - `client_max_body_size 25m;`
5. Install systemd units: `display-admin.service` (`User=display`, `EnvironmentFile=/etc/display/env`,
   `ExecStart=.venv/bin/gunicorn ...`, `ExecStartPost=.venv/bin/python scripts/publish.py`,
   `Restart=always`, `RestartSec=3`), `display-publish.timer` (daily 00:05),
   `display-backup.timer` (daily 03:30), `display-netcheck.timer` (every 5 min: restart
   NetworkManager if gateway unreachable 3 checks in a row).
6. `scripts/backup.sh`: `sqlite3 display.db ".backup /media/backup/display-$(date +%F).db"`,
   tar photos, prune to last 14, write result to `/srv/display/data/last_backup.json`
   for the status page. USB stick auto-mounted at `/media/backup` via fstab (`nofail`).
7. Install `pi/sudoers-display` → `/etc/sudoers.d/display` (`visudo -c`).

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 3.2.1 | `curl localhost`, `curl localhost/data/events.json`, `curl -I localhost/admin` | 200, 200, 302 to login |
| 3.2.2 | `systemctl is-enabled nginx display-admin display-publish.timer display-backup.timer display-netcheck.timer` | All enabled |
| 3.2.3 | Reboot; wait 90 s | Display up; `/admin` up; `events.json` timestamp post-boot |
| 3.2.4 | Run backup timer manually; corrupt the live DB copy on a scratch card; restore per `TROUBLESHOOTING.md` | Restore works in < 10 min |
| 3.2.5 | `git grep -i -E "password|secret"` | No secrets in repo |
| 3.2.6 | Pull the USB stick; run backup | Fails loudly in journal; status page shows "backup failed"; display unaffected |
| 3.2.7 | Restore from USB backup onto a freshly flashed card using `SETUP.md` | Same events and photos on screen |

### Step 3.3 Kiosk browser

**Tasks**
1. `pi/kiosk.sh`:
   - wait for `curl -sf http://localhost/` (up to 60 s);
   - `while true; do chromium --kiosk --noerrdialogs --disable-infobars --no-first-run --incognito --enable-features=OverlayScrollbar --start-maximized --check-for-update-interval=31536000 http://localhost; sleep 5; done`
   - `--incognito` prevents the "restore pages?" bubble after a power pull.
   - Re-verify the package name (`chromium`) and flags against the current official
     Raspberry Pi kiosk tutorial at build time; both have changed across releases.
2. `pi/labwc-autostart` → `~/.config/labwc/autostart`: `/srv/display/pi/kiosk.sh &`
   (current Raspberry Pi OS desktop is labwc; older lxsession paths no longer apply).
3. Hide the cursor (labwc/Wayland method; document what works on the installed release).
4. Orientation: set in the desktop Screen Configuration tool or `wlr-randr` in the labwc
   session; `config.txt` rotation flags do not apply on the modern KMS driver.
5. Screen blanking off (3.1.3). If the TV has its own sleep timer, disable it.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 3.3.1 | Cold boot, no keyboard/mouse | App fullscreen < 60 s; no browser chrome |
| 3.3.2 | `pkill chromium` over SSH | Back < 10 s |
| 3.3.3 | Pull power, replug | Boots to app; no restore-session bubble; no fsck prompt |
| 3.3.4 | Leave 2 h | Screen on; no cursor |
| 3.3.5 | Unplug/replug HDMI; power-cycle the TV | Picture returns without Pi reboot |
| 3.3.6 | Boot with Wi-Fi down | App loads from local files; countdown hidden; stale badge after threshold |

### Step 3.4 Recovery and long-term reliability

**Tasks**
1. Hardware watchdog: `dtparam=watchdog=on` in `/boot/firmware/config.txt`;
   `RuntimeWatchdogSec=15` and `RebootWatchdogSec=2min` in `/etc/systemd/system.conf`.
2. Nightly reboot 04:05 (root cron) — belt and braces for Chromium memory.
3. `journald` `SystemMaxUse=200M`; logrotate for anything else.
4. Unattended upgrades off; document a monthly manual `apt upgrade` over SSH with a
   post-upgrade check of 3.3.1.
5. Decision point after the first stable week: enable the read-only overlay filesystem
   (`raspi-config` → Performance). If enabled, `/srv/display/data`, `web/data`,
   `web/photos`, and the USB mount must be writable paths outside the overlay.
6. Image the finished SD card; store in team Drive.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 3.4.1 | Pull power 10× at random moments (mid-boot, mid-upload) | Boots clean every time; `PRAGMA integrity_check` ok |
| 3.4.2 | `echo c > /proc/sysrq-trigger` | Watchdog reboots < 30 s |
| 3.4.3 | `nmcli radio wifi off` 10 min, then on | Reconnects; Tailscale returns; admin reachable |
| 3.4.4 | Fill the disk (fallocate) | Display still serves; admin write fails with a clear error; nothing corrupted |
| 3.4.5 | 7-day soak: `uptime`, `free -m`, Chromium RSS, `journalctl -p err` | No manual intervention; errors zero or explained |

---

## 9. Phase 4 — Team room

**Tasks**
1. Decide orientation (TV on wall mount → landscape; narrow slot or bought monitor →
   portrait), set it, mount, run cables to a wall outlet (not a switched strip).
2. Measure viewing distance; set `font_scale` in `/admin/settings`.
3. Tune `photo_interval_s`.
4. One-week feedback form (readable? useful? missing? distracting? remove?). Iterate on
   layout/content in `web/` and redeploy with `git pull` + `systemctl restart display-admin`.
5. Label the TV remote / set TV auto-input so an accidental input change is recoverable.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 4.1 | Repeat 1.1.1 / 1.1.2 on the real display | Pass from the farthest seat |
| 4.2 | Check at the brightest time of day | Readable; no glare on hero card |
| 4.3 | Observe a meeting in front of it | Photo changes not distracting (feedback) |
| 4.4 | 7 days of normal use | Nobody touched the Pi; ≥ 1 event added by a non-DAQ member appeared |
| 4.5 | Change TV input by accident | Documented recovery works |

---

## 10. Phase 5 — Documentation and handoff

**Tasks**
1. `docs/UPDATING.md` (with screenshots): join the tailnet (4 steps), open the admin
   URL, add/edit/cancel an event, weekly repeat, add/remove/disable photos (tip: upload
   from the room Wi-Fi for speed), change competition date/banner/font scale, what the
   footer timestamp means.
2. `docs/TROUBLESHOOTING.md`: decision tree — black screen / frozen / stale badge /
   wrong time / admin won't load / Tailscale offline / backup failed; where credentials
   live (team password manager, never the repo); restart commands; log locations
   (`journalctl -u display-admin`, `-u nginx`); restore from USB backup; reflash from the
   SD image.
3. `docs/SETUP.md`: rebuild from scratch in ≤ 1 h.
4. `docs/TESTING.md`: every (manual) test above as a checklist.
5. Laminated card on the back of the display: "Unplug 10 s, replug. Still broken? Ping
   the DAQ lead." plus the admin URL.
6. Credentials and the SD image stored where the team can find them without you.

**Acceptance tests**

| ID | Do this | Pass when |
|---|---|---|
| 5.1 | Give `UPDATING.md` to a non-DAQ member | They add an event and a photo with zero questions |
| 5.2 | Give a fresh SD card and `SETUP.md` to a DAQ member | Working Pi without your help |
| 5.3 | Break something on purpose (stop nginx; unplug Ethernet; pull USB stick) | A member fixes it using only `TROUBLESHOOTING.md` |

---

## 11. Suggested Claude Code session order

1. Phase 2.1 scaffold + `conftest.py` + fixtures + schema; run `pytest`.
2. Phase 2.3 models + publish + auth with all (auto) tests green.
3. Phase 2.4 photos with (auto) tests green.
4. Phase 2.2 display page against fixtures (open `web/` with `python -m http.server`).
5. Phase 2.5 status page + `pi/` config files + `sudoers`.
6. Phase 3 files: nginx conf, systemd units, `kiosk.sh`, `labwc-autostart`, `backup.sh`,
   `docs/SETUP.md` checklist.
7. `docs/TESTING.md`, `UPDATING.md`, `TROUBLESHOOTING.md` skeletons; fill after Phase 4.
