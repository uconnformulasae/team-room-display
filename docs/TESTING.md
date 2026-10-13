# Testing

Every acceptance test from [PLAN.md](PLAN.md), by ID. Tests marked **(auto)** in the
plan run with `pytest`. Everything else is a checklist here, to run on the real Pi and
TV. Fill in the **Result** column (date, initials, pass/fail) as you go.

## Automated tests

```sh
.venv/bin/pytest                 # everything; about 45 seconds
```

The browser tests in `tests/test_display.py` need Playwright and are skipped without it:

```sh
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
```

If Chromium or Chrome is already installed somewhere else, you can skip the download:
`PLAYWRIGHT_CHROMIUM=/path/to/chrome .venv/bin/pytest`.

| Plan ID | What | Where |
|---|---|---|
| 1.2.2 | Fixtures match `events.schema.json`; the malformed one fails | `tests/test_schema.py` |
| 2.1.2 | App starts, creates the database in WAL mode, serves the login page | `tests/test_smoke.py` |
| 2.1.3 | No `.db`, `.env`, generated JSON or photos can be committed; no secret values | `tests/test_repo.py` |
| 2.3.1 – 2.3.2 | Login, rate limit, every admin page needs a login (on the Pi each of gunicorn's 2 workers counts wrong passwords separately; see SPEC.md §11) | `tests/test_auth.py` |
| 2.3.3 – 2.3.11 | CSRF, validation, escaping, publishing, audit log, concurrency (2.3.5 and 2.3.6, weekly series in the admin pages, no longer exist: repeats are Google Calendar's job) | `tests/test_admin.py`, `tests/test_models.py`, `tests/test_publish.py` |
| (calendar) | Reading the Google Calendar: repeats with skipped and moved weeks, time zones, the clock change, all-day events, cancelled events, a link that fails or returns a web page, the link never appearing in logs | `tests/test_calendar_feed.py` |
| 2.4.1 – 2.4.4 | Photo resizing, orientation, EXIF removal, rejects, the 20 MB limit | `tests/test_photos.py` |
| (Drive photos) | Reading the Drive folder against a fake Drive API: new, changed, removed and unusable files, the per-round download budget, every error Google sends, the API key never in a URL; the read-only admin Photos page | `tests/test_drive_photos.py`, `tests/test_admin_photos.py` |
| (2.5.4) | Status page, Restart display / Reboot buttons (commands faked) | `tests/test_status.py` |
| (3.2.4, 3.2.6) | `backup.sh` copies, prunes to 14, fails loudly without the USB stick | `tests/test_backup.py` |
| (Phase 3) | Unit files point at real scripts; nginx/gunicorn on 127.0.0.1 only; sudo allows exactly two commands; `kiosk.sh` waits for nginx and reopens the browser | `tests/test_pi_files.py` |
| 2.2.1 – 2.2.6, 2.2.8 – 2.2.14 | The display page in headless Chromium (see below) | `tests/test_display.py` |

The display checks were **(manual)** in the plan. Most of them now also run
automatically. They're still listed below because the real TV is what counts.

## Viewing the display with test data

You don't need the Pi to look at the display:

```sh
cd web && mkdir -p data photos
cp ../fixtures/events_normal.json data/events.json
cp ../fixtures/settings.json ../fixtures/photos.json data/
cp ../fixtures/sample_photos/*.jpg photos/
python3 -m http.server 8080
```

Open `http://localhost:8080/?now=2026-09-22T14:10`. The `?now=` part starts the page's
clock at that moment (the fixtures are dated around it) and it ticks normally from there
(docs/SPEC.md §8). Swap other fixtures into `data/` to try the other states; the page
picks them up within a minute. `web/data` and `web/photos` are ignored by git.

---

## Phase 1: plan the webapp

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 1.1.1 | Show the display (or `docs/mockups/landscape.png`) on a monitor, from the farthest seat in the team room | Hero card and list readable (about 1 inch of text height per 10 ft) | |
| 1.1.2 | Ask 3 members "when's the next meeting?" while they glance at it | Each answers correctly within about 3 s | |
| 1.1.3 | Walk every edge rule in docs/SPEC.md §8 against `docs/mockups/edge-states.png` | Each rule has a drawn state, both orientations | |
| 1.2.1 | Map every displayed field to a column or setting | No gaps: done in docs/SPEC.md §9 | ✅ 2026-09-22 |
| 1.2.3 | Recurrence walkthrough | Each case in ≤ 3 admin actions: done in docs/SPEC.md §10 | ✅ 2026-09-22 |

## Phase 2: the webapp

### 2.1 Scaffold

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 2.1.1 | Fresh clone → `pip install -r requirements.txt` → `python scripts/init_db.py` → `flask run --port 8000` | `/admin` shows a login page; `python -m http.server` in `web/` with a fixture copied to `data/` renders the display | |

### 2.2 Display page (desktop Chromium with fixtures; also on the real TV)

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 2.2.1 | Load `events_empty.json` | Empty state; no console errors | |
| 2.2.2 | Load `events_40.json` | List truncates to 7 with "+N more" | |
| 2.2.3 | Swap in `events_malformed.json` mid-session | Previous render stays; stale badge; error logged; no crash | |
| 2.2.4 | `?now=2026-09-22T16:59` with `events_normal.json`, wait 2 minutes | "Machine shop open hours" and "Suspension design review" disappear without a reload | |
| 2.2.5 | `events_normal.json` | "Career fair" shows "All day"; "Testing weekend" shows "Oct 2 – 4", one row each | |
| 2.2.6 | DevTools device toolbar: 1920×1080, 1280×720, 1080×1920 | No overflow, no scrollbars, nothing clipped | |
| 2.2.7 | 24 h soak; watch the tab in Chromium's Task Manager (Shift+Esc) | Tab memory grows < 50 MB | |
| 2.2.8 | DevTools → Network → Offline for 20 min | Content stays; red "Can't load new data" badge; with data older than 60 min, amber "may be out of date" | |
| 2.2.9 | `?now=2026-10-31T23:59` and `?now=2026-11-01T01:59` (DST ends at 2 AM) | Clock, date and countdown correct across midnight and the DST change | |
| 2.2.10 | DevTools Network panel, filter out localhost | Zero external requests | |
| 2.2.11 | `time_synced: false` in `data/events.json` | Countdown hidden; rest normal | |
| 2.2.12 | Set `banner_text` | Banner shows; layout reflows without overlap | |
| 2.2.13 | `font_scale` 0.8 → 1.5 (the most the setting allows) | Text scales; no overflow at 1080p (fewer list rows is fine; at 1.5 with a banner the list may hide itself) | |
| 2.2.14 | `photos.json` with zero photos | Logo placeholder; photo rotation keeps running without errors | |

### 2.3 – 2.5 Admin, photos, Tailscale

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 2.3.12 | Add an event to the team's Google Calendar from a phone, then delete another one | Both changes on screen within about a minute, nobody touching the admin page (Google itself can add a few minutes) | |
| 2.3.14 | In Google Calendar: make a weekly event, delete one week of it, move another week to a different day | The screen and **Status → Calendar** follow each change | |
| 2.3.15 | Unplug the Pi's network for 70 minutes | Events stay on screen; amber "Schedule may be out of date" appears; Status says "Could not reach the calendar". Plug it back in: the badge clears within a minute | |
| 2.3.16 | Press **Reset** on the secret address in Google Calendar | Status shows the error within a minute; the new link in `/etc/display/env` fixes it (TROUBLESHOOTING.md) | |
| 2.3.17 | `sudo journalctl -u display-publish -u display-admin \| grep -c private-` | `0`: the secret link is never logged | |
| 2.3.13 | `sudo systemctl kill display-admin` | Display unaffected; service back in < 10 s (`systemctl status display-admin`) | |
| 2.4.7 | From the Google Drive app on a phone, put 10 photos (some HEIC) in the team folder | All on the screen within a few minutes, upright; **Photos** lists them | |
| 2.4.8 | Move every photo out of the folder | Logo placeholder within a minute; no crash | |
| 2.4.9 | Put a PDF and a 25 MB photo in the folder | The PDF is ignored; the photo is listed under "Files the screen can't show" | |
| 2.4.10 | `sudo journalctl -u display-publish -u display-admin \| grep -c AIza` | `0`: the API key is never logged | |
| 2.5.1 | From a phone on cellular (Tailscale on), open the `.ts.net` URL | HTTPS, valid certificate, login page | |
| 2.5.2 | `sudo ss -tlnp` on the Pi | nginx and gunicorn only on `127.0.0.1`; only tailscaled on other addresses | |
| 2.5.3 | `curl http://<pi-lan-ip>/admin` from another computer on campus | Connection refused | |
| 2.5.4 | Status → **Restart display**, remotely | Screen blanks and returns in < 15 s | |
| 2.5.5 | Status → **Reboot Pi**, remotely | Back on screen in < 2 min; admin reachable again | |
| 2.5.6 | `sudo -l -U displayapp` (the account the web app runs as; see SETUP.md step 4) | Exactly the two commands: `display-restart ""` and `/sbin/reboot ""` | |
| 2.5.7 | Edit an event, then look at **Status → Recent changes** | Your Tailscale login and the time are recorded | |
| 2.5.8 | Admin console → Machines → daq-display | "Expiry disabled" | |
| 2.5.9 | Reboot the Pi | `tailscale status` connected in < 60 s, no login prompt | |
| 2.5.10 | Remove a test user from the tailnet | They can no longer load the admin URL | |

## Phase 3: Raspberry Pi

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 3.1.1 | Reboot with no keyboard or mouse | Desktop logs in automatically, no prompts | |
| 3.1.2 | `timedatectl` | NTP synchronized: yes; Time zone: America/New_York | |
| 3.1.3 | `ssh display@daq-display.local` from the room; `tailscale ssh display@daq-display` from off campus | Both work | |
| 3.1.4 | `vcgencmd get_throttled` after 10 min | `throttled=0x0` | |
| 3.2.1 | `curl localhost`, `curl localhost/data/events.json`, `curl -I localhost/admin` | 200, 200, 302 to `/admin/login` | |
| 3.2.2 | `systemctl is-enabled nginx display-admin display-publish display-backup.timer display-netcheck.timer display-reboot.timer` | All `enabled` | |
| 3.2.3 | Reboot; wait 90 s | Display up; `/admin` up; `generated_at` and `synced_at` in `events.json` are after the boot | |
| 3.2.4 | Run the backup (`sudo systemctl start display-backup`); on a **scratch card**, corrupt the live DB copy; restore per TROUBLESHOOTING.md | Restore works in < 10 min | |
| 3.2.5 | `git grep -i -E "password\|secret"` | Every hit is a name, placeholder or docs, never an actual password, hash or key (also checked by `tests/test_repo.py`) | |
| 3.2.6 | Pull the USB stick; `sudo systemctl start display-backup` | Fails loudly in `journalctl -u display-backup`; Status page shows "Backup failed"; display unaffected | |
| 3.2.7 | Restore from the USB backup onto a freshly flashed card using SETUP.md | Same events and photos on screen | |
| 3.3.1 | Cold boot, no keyboard or mouse | App fullscreen in < 60 s; no browser bars | |
| 3.3.2 | `pkill -x chromium` over SSH | Back in < 10 s | |
| 3.3.3 | Pull the power, plug it back in | Boots to the app; no "restore pages" bubble; no disk-check prompt | |
| 3.3.4 | Leave it 2 h | Screen still on; no mouse pointer | |
| 3.3.5 | Unplug and replug HDMI; power-cycle the TV | Picture returns without rebooting the Pi (if not, see `docs/pi-config-notes.md`) | |
| 3.3.6 | Boot with Wi-Fi down | App loads from local files with the events read before; countdown hidden; stale badge once the last calendar read is 60 min old | |
| 3.4.1 | Pull the power 10× at random moments (mid-boot, mid-upload) | Boots clean every time; `sudo -u displayapp sqlite3 /srv/display/data/display.db "PRAGMA integrity_check"` says `ok` | |
| 3.4.2 | `echo c \| sudo tee /proc/sysrq-trigger` | Watchdog reboots it in < 30 s | |
| 3.4.3 | `sudo nmcli radio wifi off` for 10 min, then `on` | Reconnects; Tailscale returns; admin reachable | |
| 3.4.4 | Fill the disk: `fallocate -l <free space>G /srv/display/data/fill` (delete it afterwards!) | Display still shows; admin pages say the SD card is probably full (the Status page still shows the disk line); `journalctl -u display-publish` shows one complaint, not one per 30 s; after deleting the file everything works again by itself; `integrity_check` ok | |
| 3.4.5 | 7-day soak: `uptime`, `free -m`, Chromium memory, `journalctl -p err -b` | No manual intervention; errors zero or explained | |

## Phase 4: team room

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 4.1 | Repeat 1.1.1 / 1.1.2 on the real display | Pass from the farthest seat (tune **Settings → Text size**) | |
| 4.2 | Check at the brightest time of day | Readable; no glare on the hero card | |
| 4.3 | Watch a meeting in front of it | Photo changes not distracting (feedback) | |
| 4.4 | 7 days of normal use | Nobody touched the Pi; ≥ 1 event added to the calendar by a non-DAQ member appeared | |
| 4.5 | Change the TV input by accident | The recovery in TROUBLESHOOTING.md works | |

## Phase 5: handoff

| ID | Do this | Pass when | Result |
|---|---|---|---|
| 5.1 | Give UPDATING.md to a non-DAQ member | They add an event (in Google Calendar) and a photo with zero questions | |
| 5.2 | Give a fresh SD card and SETUP.md to a DAQ member | Working Pi without your help | |
| 5.3 | Break something on purpose (stop nginx; unplug Ethernet; pull the USB stick) | A member fixes it using only TROUBLESHOOTING.md | |

---

## What has been verified so far, and by whom

All of this was on a Mac, so **not** on the Pi, and not on the real TV.

- **2026-09-22 (the build):** `pytest` passes, including the browser tests in headless
  Chromium. The app ran under `gunicorn -w 2` with a real env file made by
  `set_password.py`; from a phone-sized browser: log in, upload photos (including a
  rejected file), hide and delete photos, change settings, Status page, log out.
  Mockups in `docs/mockups/` are screenshots of the real page with the fixtures.
- **2026-09-29 (Google Calendar):** reading a Google-style calendar file with a weekly
  event that has a skipped and a moved week, across the November clock change; the new
  libraries are pure Python (a test checks this); the full stack under `gunicorn -w 2`
  with **Read calendar now**. Owen's real calendar link was read successfully the same
  day.
- **2026-09-30 (30-second cycle):** `publish.py --every 1` against a calendar file that
  gains and then loses an event: the screen's file follows both, unattended; the page
  polls every 30 s and redraws only when a file changed.

- **2026-10-06 (Google Drive photos):** the reader against a fake Drive API (every
  error reply Google documents, a slow first batch, unusable files); the demo photo
  folder through `publish.py --every 1`. Not yet against a real Drive folder and key.

Still to do by a person: Phase 1's in-room checks, 2.2.7 (24-hour soak), everything that
needs the Pi, Tailscale, the real calendar or the room (2.3.12 onward), and Phases 3 – 5.
