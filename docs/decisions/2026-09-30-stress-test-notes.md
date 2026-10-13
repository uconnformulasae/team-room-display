# Stress-test notes (2026-09-30)

An afternoon of trying to break the project on purpose: hostile calendar feeds, a server
that misbehaves, boundary values and odd HTTP on the admin site, a completely full disk,
nine clients plus the backup script plus a held database lock at once, the screen with
extreme data and sizes and a simulated 24 hours, and the scripts with awkward input.
Each finding says how to reproduce it and what it means. The scripts that produced them
are throwaway; the numbers below come from a Mac, so timings on a Pi will be slower.

**Status (same day):** every High and Medium finding and L1, L3 (the cut-short half), L4,
L5, L6, L7 and L8 were fixed, each with a test that reproduced it first (SPEC.md §11,
decisions 39–43). Left as they are, on purpose: L2 (how the library repairs odd shapes
Google never produces) and the gzip half of L3 (the reader never asks for gzip).

Severity: **High** would confuse or lock out the team in normal use. **Medium** needs an
unusual situation but then misbehaves. **Low** is cosmetic, or needs someone hostile
with access to the tailnet, the env file or the database.

## High

| # | Where | Finding | How to see it |
|---|---|---|---|
| H1 | `app/auth.py` `login()` | **Any non-GET request to `/admin/login` counts as a wrong password.** A `HEAD` request (uptime checkers, link previews in chat apps, some browsers' prefetch) gets a 401, writes an `auth.login_failed` audit row, and after five of them the IP is locked out for 10 minutes, right password or not. The view treats "not GET" as "a login attempt" instead of checking for POST. | Six `HEAD /admin/login` → 401 ×5, then 429; a real login from the same IP → 429. |
| H2 | `web/style.css`, settings | **The text-size setting allows values the layout can't take.** At 1920×1080 with the normal fixture: the list shrinks to one row at 1.3 with a banner and to zero rows at 1.4; rows are cut off from 1.5 (with banner) or 1.8; at 1.8–2.0 the page itself overflows (hero and photo panel off screen). The form allows up to 2.0. Tests only cover 0.8–1.3. Portrait fares a little better (zero rows from 1.6). | Settings → Text size 1.6, then look at the screen; or run the overflow check at 2.0. |
| H3 | `app/db.py`, every admin page | **A completely full SD card takes down the whole admin site, including the Status page that would say "almost full".** Each request opens a fresh SQLite connection, and in WAL mode a fresh connection needs a 32 KB `-shm` file, which can't be created → "disk I/O error" → 500 on every page. The screen keeps working from its files. Plan test 3.4.4 expects "an admin change fails with a clear error", which is not what happens. | Fill the disk to under ~40 KB free, open `/admin/status`. |
| H4 | `scripts/publish.py --every`, `display-publish.service` | **On a full disk the calendar loop can die at start-up** (creating the app applies the schema, which needs to write) and systemd then restarts it every 5 seconds forever, each start logging a traceback. Once the disk had slightly more room the loop survived and logged a traceback per cycle instead. Either way the journal fills while the disk is full. | Same disk, start `publish.py --every 1`. |

## Medium

| # | Where | Finding | How to see it |
|---|---|---|---|
| M1 | `app/calendar_feed.py` `parse()` | **A repeating event with `FREQ=SECONDLY` (or MINUTELY) and no end takes minutes to expand**: the library generates every occurrence in the 62-day window (5.4 million for SECONDLY) before the 500-event cap applies. Measured 188 s for SECONDLY, 3 s for MINUTELY. While that runs the 30-second loop stops reading, and **Read calendar now** hits gunicorn's 120 s timeout. Google's UI can't create a SECONDLY rule, but an imported `.ics` can. | Feed with `RRULE:FREQ=SECONDLY`. |
| M2 | `app/calendar_feed.py` `parse()` | **One malformed line makes the whole calendar unreadable**, not just that event: an invalid date, a garbage `RRULE`, a missing `DTSTART`, or an unknown zone in `X-WR-TIMEZONE` all raise inside the library before the per-event guard runs. The saved events stay (good), but nothing new gets in until the bad event is fixed in the calendar, and the Status page only says "did not return a calendar". | Feed with `DTSTART:20261501T000000Z` or `RRULE:FREQ=WEEKLY;UNTIL=garbage`. |
| M3 | `app/calendar_feed.py` `fetch()` | **The 20-second timeout is per read, not per request.** A server that drips 100 bytes every half second is read to the end however long it takes (15 s for a 3 KB body in the test; unbounded in principle). Same effect as M1 on the loop and the button. Google doesn't drip, but a captive portal or a proxy might. | Local server that writes 100 bytes every 0.5 s. |
| M4 | `app/calendar_feed.py` `fetch()` | **Redirects are followed anywhere**, including from `https://` to plain `http://` on another host, because the `https` rule is checked only on the configured link. The redirected request doesn't carry the secret path, so the secret doesn't leak, but the reader will happily accept a calendar from wherever the redirect points. `file://` and redirect loops are refused (urllib gives a 302 error, whose message wrongly says "copy the secret address again"). | Local server answering 302 to `http://example.com/`. |
| M5 | `app/admin/routes.py`, `app/auth.py` | **A write lock held by another process for longer than 10 s turns logins and every write into a 500** after the 10-second SQLite timeout. Login is affected because it writes an audit row. Reads (`GET /admin/photos`) keep working. The only realistic holder is a person running `sqlite3` by hand on the live database, which TROUBLESHOOTING warns against, but the page they'd get is the generic "Something went wrong". | `sqlite3 display.db` → `BEGIN IMMEDIATE`, then log in. |
| M6 | `scripts/backup.sh` | **`sync` at the end of the backup stalls every write on the machine.** Nine clients averaged 6 s per request while four backups ran, against 0.04 s otherwise. On the Pi this happens once a night at 03:30, so it's mostly harmless, but it's why anything else that writes at that moment (a calendar read, an upload) will hang for a few seconds. | Run `backup.sh` in a loop while posting settings. |
| M7 | `web/app.js` photos | **200 photos whose files are missing cause 400 requests in 30 s** at a 5-second interval: each turn tries every photo, every one 404s, and the turn ends on the placeholder. Photos only go missing if someone deletes files by hand, or after a partial restore from backup. | `photos.json` listing 200 names that don't exist. |
| M8 | `app/admin/routes.py` photo routes | **A photo id above 2⁶³ gives a 500 instead of a 404**: the `<int:photo_id>` converter accepts any size of integer, SQLite can't bind it (`OverflowError`). Harmless, but it's a 500 with a traceback in the log for anyone poking at URLs. | `POST /admin/photos/9223372036854775808/toggle`. |
| M9 | `app/models.py` `audit()` | **`audit_log.who` is stored verbatim with no length cap.** A 10 KB `Tailscale-User-Login` header (only tailscale serve can set it for real, or a local process) gets a 10 KB row and a 10 KB cell on the Status page. Same for `photo.upload` details (capped at 100) versus `calendar.read` (capped at 200): inconsistent but fine. | Any write with a huge `Tailscale-User-Login` header. |
| M10 | `app/__init__.py` 413 page | **Any request over the form limit gets the photo message** ("That upload is too big. Each photo can be at most 20 MB"), including a 1 MB password or a 1 MB banner (Werkzeug's 500 KB form-field limit answers 413 before the form validates). Only a hostile client sends those. | POST a 1 MB `banner_text`. |
| M11 | `scripts/publish.py` | **`--every nan` runs forever with no wait; `--every inf` crashes with a traceback.** The check only rejects values ≤ 0. Nobody types these, but the unit file is the one place the value is set. | `publish.py --every nan`. |
| M12 | `scripts/init_db.py`, `scripts/set_password.py` | **Plain failures print Python tracebacks instead of a sentence**: a read-only folder, a missing folder (`--write /etc/display/env` before `mkdir`), a corrupt database file (`file is not a database`), or a database path that is a folder. The SETUP.md steps avoid all four, and TROUBLESHOOTING has no "the database is corrupt" section (restore from USB is the answer). | `set_password.py --write /nonexistent/dir/env`. |
| M13 | `scripts/set_password.py` | **A trailing `\r` from Windows-style input stays in the password.** Piping `password\r\n` (a file saved on Windows, or PowerShell) sets a password nobody can type. Twelve spaces are also accepted as a password. | `printf 'twelve chars ok\r\n' \| set_password.py`, then log in with `twelve chars ok`. |

## Low

| # | Where | Finding |
|---|---|---|
| L1 | `parse()` | Control characters and the Unicode right-to-left override in a title or location are kept (only whitespace is collapsed). A `‮` in a title reverses how the rest of the row reads on the screen. Needs someone with write access to the calendar, and it looks wrong in Google Calendar too. |
| L2 | `parse()` | Odd shapes are silently "fixed" by the library: an end before the start becomes start↔end swapped; a negative `DURATION` moves the start earlier; an all-day start with a timed end becomes a timed event that starts at midnight UTC (20:00 the day before, local). Google never produces these. |
| L3 | `fetch()` | A gzip-encoded body (a server that ignores the missing `Accept-Encoding`) is not decompressed and reads as "not a calendar". A body cut short by the server (wrong `Content-Length`) is accepted as is; if the cut lands between events it parses as a shorter calendar. |
| L4 | `web/app.js` | An event whose `end` equals its `start` shows "3 – 3 PM" in the hero card. The Pi's reader never publishes that shape (it nulls the end), so only a hand-edited file shows it. |
| L5 | `web/app.js` | A `competition_date` that isn't a real date (2027-02-30) is rolled forward by the browser to Mar 2 and counted down to silently. The settings form refuses such dates; only a hand-edited database gets it through. |
| L6 | `scripts/backup.sh` | If the data folder itself is missing (wrong `DISPLAY_DB`), the failure can't be recorded in `last_backup.json`, so the Status page never learns of it. Only the journal shows it. |
| L7 | `app/__init__.py` `read_env_file()` | `export KEY=value` is read as a key named `export KEY` (systemd would reject the line outright, so the app would never see it on the Pi). |
| L8 | `pi/systemd/display-publish.service` | The service reads `CALENDAR_ICS_URL` once at start; after changing the link in `/etc/display/env` the docs say to restart `display-admin`, but the loop also needs `sudo systemctl restart display-publish` or it keeps reading the old link until the nightly reboot. (Found while reading the unit; TROUBLESHOOTING's "the link changed" steps restart only the admin app.) |

## Held up under pressure

- **The calendar parser** on: every-minute recurrence (3 s, capped at 500), 100 000-count
  daily rules (0.01 s), 5 MB descriptions, 10 000-character titles, folded lines, a BOM,
  LF-only files, Latin-1 bytes, tentative and cancelled events, todos, duplicate UIDs,
  orphan `RECURRENCE-ID`, unknown `TZID`s, floating times, the ambiguous 1:30 AM on the
  clock-change night, years 0001 and 9999.
- **The fetcher** on: 404, 500, redirect loops, `file://` redirects, a 6 MB chunked body
  (refused at 5 MB), headers that never finish (timeout), TLS against a plain port, an
  unresolvable host, a directory or an empty string as the source, `webcal://`.
- **The admin site** on: 1 MB and NUL-bearing passwords, tampered, forged and 5 KB session
  cookies, a 20 KB `next`, every settings boundary (0.5/2.0, 5/600, Feb 29 2027 refused,
  years 0001 and 9999 accepted, 140 emoji, 141 characters refused, an unknown extra
  field ignored), file names with traversal, 300 emoji, only an extension, zero bytes,
  200 photos in one request, a 1×1 image, 25 MB + 1 (413), traversal in `/photos/thumbs/`
  and `/admin/static/`, dotfiles, deleting twice, negative and leading-zero ids, and 12
  threads writing at once (integrity ok, files and rows match, no temp files left).
- **Nine parallel clients** doing uploads, settings and calendar reads against gunicorn
  with two workers (0.04 s median), with the backup script's copies all consistent.
- **The screen** on: 500 events with 200-character titles at 1920×1080, 1280×720,
  1080×1920, 3840×2160 and 800×480 (no overflow, no errors); a three-year all-day
  event; identical start times; New Year's Eve, the 2027 spring clock change and the 2028
  leap day; nonsense `?now=` values; wrong types in `settings.json` (defaults used); a
  5 MB caption and 5 000 photo entries; a 20 MB `events.json` (7 rows in 3 s); the 04:00
  reload; and 24 simulated hours (2 622 polls) with the heap ending smaller than it
  started and the same 76 DOM nodes.
- **The disk-full case for the screen's files**: a settings change during "disk full"
  left `events.json`, `photos.json` and `settings.json` intact and valid (the friendly
  "saved but the screen was not updated" page), and everything recovered by itself once
  space was freed: reads, uploads, integrity all fine.
- **The scripts** with spaces in every path, a read-only USB stick (fails loudly), a missing
  database (fails loudly), `--every 0`, `-5`, `abc`, a missing value and an unknown
  flag (all refused with a one-line message).
