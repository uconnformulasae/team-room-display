# Clean-up notes (2026-09-30)

A read-through of the whole codebase after the Google Calendar change, looking for
leftovers, duplication, wasted work and anything a new DAQ member would trip over.
Each note says what, why, and what to do.

**Status (2026-09-30, same day):** every note with an action was carried out, and the
notes marked "Nothing" or "Leave it" (B1, B8, B10, C1, C2) were left as they are on
purpose. B11 turned out to be already the case: the wording section sits above the
JSON-reading section. The test suite went from 321 to 323 tests, all passing.

Priority: **A** worth doing before handing the repo to the team, **B** nice to have,
**C** only if someone is already in that file.

## 1. Leftovers from the admin event pages

| # | Where | What | Do |
|---|---|---|---|
| A1 | `app/admin/static/admin.css` | `.page-head`, `.checkbox` and `.more-actions` (with its `p` and `form` rules) styled the Events page and event form, which no longer exist. Nothing uses them. | Delete the three blocks. |
| A2 | `app/admin/templates/status.html` | `{% if info.published_minutes > 60 %}` "over an hour old" pill and the whole "Screen data" row date from when only the admin app wrote the files. With the 30-second reader the row is nearly always "just now" and the Calendar row above says the same thing better. | Keep the row (it catches a dead `display-publish`), but drop nothing yet; see C3 for the wording. |
| A3 | `app/models.py` | `photo_by_filename()` is only called by tests. | Delete it and make the two tests look up by id. |
| A4 | `docs/PLAN.md` | Still describes admin event pages, weekly series in the admin, a daily publish and `User=display`. SPEC.md §11 lists every departure, but PLAN.md itself gives no hint that it is now history. A new member reading top-down will be misled for the first 600 lines. | Add a five-line note at the top of PLAN.md: "This is the original plan. What was built differs in these places: … see SPEC.md §11." Do not edit the body. |
| B1 | `fixtures/events_40.json`, `events_normal.json` | Event titles like "Chassis subteam" and locations are fine, but the fixtures were written when events had types; the `40` file's mix of types is no longer visible on screen. | Nothing to do; noted so nobody looks for the badge data. |

## 2. Duplication

| # | Where | What | Do |
|---|---|---|---|
| A5 | `scripts/init_db.py` and `scripts/publish.py` | `init_db.py` re-implements "read the calendar, then publish" that `publish.py` already has as `run_once()`. Two places to fix when that sequence changes. | Have `init_db.py` import and call `run_once` (or make `init_db` a `--init` flag of `publish.py`). |
| A6 | `app/auth.py` `audit()` vs `app/admin/routes.py` | `auth.audit()` wraps "open a transaction, write one audit row". The admin routes write audit rows in twelve places with the same three lines, without using it, because they also do other writes in that transaction. Two ways of writing the same row is a trap for the next person. | Either move `audit()` to `models` as `audit_alone(conn, …)` and use it from `auth`, `restart_display`, `reboot` and `read_calendar`, or delete it and let `auth` use the three-line form too. One style, not two. |
| A7 | `tests/` | Four files define their own `conn` fixture: `conftest.py` (a plain `sqlite3` connection, row factory only), and `test_publish.py`, `test_models.py`, `test_calendar_feed.py` (each `db.connect()`, which also turns on WAL and `synchronous=FULL`). Same name, different behaviour, so a test that moves files changes meaning. | Put one `db_conn` fixture (`db.connect`) in `conftest.py` next to the existing raw `conn`, give them different names, and delete the three copies. |
| A8 | `tests/` | Three helpers read a published file: `test_publish.read()`, `test_admin.published()`, `test_photos.published_photos()`. Two build events: `test_models.event()`, `test_publish.add_event()`, plus `test_display.event()` for the JSON shape. | Move `published(web_dir, name)` and a `calendar_event(...)` builder into `tests/helpers.py`. |
| B2 | `app/photos.py`, `app/admin/static/admin.js`, `app/admin/templates/photos.html`, `app/__init__.py`, `pi/nginx/display.conf` | The 20 MB per-photo limit is written out five times (`MAX_BYTES`, the JS `MAX_BYTES`, the hint text, the 413 page, the nginx comment) and the 25 MB request limit twice. Python, JS and nginx can't share a constant, but the template and the 413 page could read `photos.MAX_BYTES`. | Pass `max_mb` into `photos.html` and the 413 message from `photos.MAX_BYTES`; leave a comment in `admin.js` and `display.conf` pointing at `photos.py` as the place the number lives. |
| B3 | `web/style.css` | `--gold` and `--warn` are both `#ffc53d`. Two names for one colour means changing the team colour misses one. | Keep `--warn`, use it in the countdown, delete `--gold`. (If the countdown should be the team's second colour rather than the warning colour, name it `--accent-2` instead.) |
| B4 | `web/app.js` | Two "did the list change" caches: `lastText` (per file, in `poll`) and `photos.files` (in `setPhotos`). Since `poll` now skips an unchanged `photos.json`, `setPhotos` only runs when the file changed, and `photos.files` only tells apart a caption-only edit from a real change. | Keep both, but say so in the comment on `photos.files`, or compare the file lists directly instead of a joined string. |
| C1 | `scripts/*.py` | `sys.path.insert(0, …)` is repeated in three scripts so `import app` works. | Leave it; it is the boring option. (`python -m scripts.publish` would remove it but changes every command in the docs and the unit files.) |

## 3. Wasted work

| # | Where | What | Do |
|---|---|---|---|
| B5 | `app/db.py` `connect()` | Every request opens a connection and runs `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=FULL`. The first is a no-op read once the file is in WAL mode, but it still takes a lock. Also `Path(path).parent.mkdir()` runs on every request. | Move `mkdir` and the WAL pragma to `init_app` (once per process); keep `synchronous=FULL` per connection (it is per-connection state). Small, but it is on every admin request. |
| B6 | `tests/helpers.py` `AdminClient.post()` | Every POST first does a GET of `/admin/settings` to pick up a CSRF token. Roughly doubles the requests in the admin tests. | Fetch the token once per `AdminClient` and reuse it (the token is valid for the whole session). |
| B7 | `web/app.js` `renderClock()` | Builds a new `<span>` for AM/PM every minute instead of updating a text node. | Give the span an id in `index.html` and set its `textContent`. Tiny; only worth it while in the file. |
| B8 | `app/status.py` `gather()` | Runs five subprocesses one after another on every Status page view (`vcgencmd` ×2, `systemctl` ×3, `pgrep`). Fine on a Pi (well under a second), noted so nobody adds more without thought. | Nothing now. If the page ever feels slow, run them with a thread pool. |
| C2 | `app/models.py` `replace_events()` | Reads the whole table to compare before writing. Correct and cheap at 500 rows; a hash of the feed would be cheaper but less obvious. | Nothing. |

## 4. Organisation and readability

| # | Where | What | Do |
|---|---|---|---|
| A9 | `docs/superpowers/` | The design, plan and these notes live under a folder named after the tool that made them. A student won't know what "superpowers" means. | Rename to `docs/decisions/` (design docs) and `docs/plans/`, and fix the three links in SPEC.md and the plan. **Done 2026-09-30.** |
| A10 | `app/status.py` | Mixes three things: reading facts about the Pi, wording them (`describe_uptime`, `describe_throttled`), and doing things to the Pi (`restart_display`, `reboot_soon`). Fine at 158 lines, but the two action functions are the only code that runs `sudo`, and they deserve to be easy to find. | Move `RESTART_DISPLAY`, `REBOOT`, `restart_display`, `reboot_soon` to the top of the file under a heading "The two commands that run as root", or into `app/pi_actions.py`. |
| A11 | `app/admin/templates/status.html` | The Browser row calls `state_pill(none, '')` for the unknown case, which the macro already handles when passed `none` directly. Two branches where one line would do. | `{{ state_pill(info.chromium, 'Chromium is running', 'Chromium is not running') }}`. |
| B9 | `app/calendar_feed.py` | `_record()` is the only underscore-private function in the app; everything else is public and documented. | Rename to `record_failure()` with a docstring, matching the rest of the module. |
| B10 | `app/admin/routes.py` | Every write route repeats: get connection, transaction, audit, `publish_now()`, flash, redirect. Readable as is; the module docstring explains the pattern. | Leave it. A helper would hide the one thing a new person needs to see. |
| B11 | `web/app.js` | 557 lines in one file, in clear sections. The wording functions (`relativeLabel`, `absoluteWhen`, `listTime`, ~90 lines) are pure and are the part most likely to be tweaked by the team. | Leave as one file (no build step, one `<script>` tag is the plan's rule), but keep the section headers; consider moving the wording section above "Reading the JSON files" so the file reads top-down from rules to plumbing. |
| B12 | `tests/test_display.py` | 591 lines: page behaviour, layout, photos, and 30 parametrised wording cases. | Split the wording cases (`test_relative_labels`, `test_list_time_column`, `test_absolute_when`) into `tests/test_display_wording.py`, sharing `Screen` via `conftest.py`. |
| C3 | `app/admin/templates/status.html` | "Screen data: Last written …" is jargon. | "Screen files: written 12:16 (just now)". |
| C4 | `pi/config.txt.notes.md` | Only file in `pi/` that is documentation. | Move to `docs/pi-config-notes.md` and fix the two links. **Done 2026-09-30.** |

## 5. Tests

| # | Where | What | Do |
|---|---|---|---|
| A12 | `tests/test_status.py` `test_read_calendar_needs_post_and_a_login` | Takes a `client` fixture it never uses and does not test the login half of its name. | Either add the logged-out POST (`client.post(...)` → 302 to login) or rename to `..._needs_post`. |
| A13 | `tests/test_admin.py` `test_invalid_settings_are_rejected` | The one-line conditional that builds the form is hard to read. | Build the dict, then override the one field: `data = dict(SETTINGS, competition_name='', competition_date=''); data[field] = value`. |
| B13 | `tests/test_scripts.py` | The two `--every` tests start a real subprocess loop and poll files; they add ~6 s. Fine, but they are the slowest non-browser tests. | Leave; note it in the docstring so nobody "fixes" the timing. |
| B14 | `tests/test_pi_files.py` | Checks that the calendar libraries are pure Python by scanning the installed wheels. Good, but it depends on the developer's venv, not on `requirements.txt`. | Keep; add a one-line comment saying so. |

## 6. Docs

| # | Where | What | Do |
|---|---|---|---|
| A14 | `docs/SPEC.md` §11 | 38 numbered decisions in the order they were made. Useful as a log, hard to use as a reference. | Keep the numbers (tests and docs cite them), add a short grouped index at the top of §11: Accounts and security (12, 13, 17, 19, 33), Calendar (31–37), Screen (1–3, 6, 15, 30, 38), Pi (5, 18, 20–22, 28, 29), Photos (8–10, 27), Testing (7, 14, 16). |
| A15 | `README.md` | The "Before it goes on the wall" table is still the most useful thing for the team leads and is at the bottom. | Move it above "How it fits together". |
| B15 | `docs/TESTING.md` | Two "Results" sections at the bottom (build session, calendar change). | Merge into one dated list. |

## Checked and fine

- Every CSS variable in `web/style.css` is used.
- No function in `app/` is unused apart from A3; every template macro and every `admin.css` rule not listed in A1 is used (`.flash-success`/`.flash-info` are reached through `flash-{{ category }}`).
- No `|safe`, `innerHTML` or `eval` anywhere; every value from the calendar and the admin forms reaches the page as text.
- The three scripts that run on the Pi share `create_app()` for their settings, so there is one place that reads `/etc/display/env`.
- `app/clock.py` is the only place that asks for the time, as intended, and every test freezes it through that one function.
