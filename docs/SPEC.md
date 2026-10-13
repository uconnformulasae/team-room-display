# Display spec

What the team-room screen shows and the exact rules behind it. `web/app.js` implements
this page; if you change a rule, change it in both places. Background and reasoning are
in [PLAN.md](PLAN.md) (§6 Phase 1).

**The events come from the team's Google Calendar** (since 2026-09-29; design in
[decisions/2026-09-29-google-calendar-design.md](decisions/2026-09-29-google-calendar-design.md))
**and the photos from a Google Drive folder** (since 2026-10-06;
[decisions/2026-10-06-google-drive-photos-design.md](decisions/2026-10-06-google-drive-photos-design.md)).
PLAN.md describes events typed and photos uploaded into the admin pages; those parts of it
no longer apply.

Mockups rendered from the real page at 1920×1080 and 1080×1920 are in
[docs/mockups/](mockups/).

---

## 1. Screen regions

In priority order (what survives if space gets tight):

1. **Header** — team logo, up to two competition countdowns ("Gas car: FSAE Michigan in 231
   days", one line each), clock and date.
2. **Next up** (hero card) — the single next event, large: what, when (relative and
   absolute), where.
3. **Upcoming list** — the following events in the next 30 days, one row each, grouped by day.
4. **Photo panel** — team photos, crossfading every `photo_interval_s` seconds.
5. **Bottom row** — empty, and taking no space, while all is well. When `banner_text` is
   set, the banner shows here. When the data is old or can't be loaded, a status dot and
   a warning badge show here (§7).

```
Landscape (default)                            Portrait (@media (orientation: portrait))
┌────────────────────────────────────────┐     ┌──────────────────────┐
│ logo   FSAE Michigan in 231 days  6:30 │     │ logo  countdown  6:30│
├───────────────────────┬────────────────┤     ├──────────────────────┤
│ NEXT UP               │                │     │ NEXT UP              │
│ Weekly Meeting        │                │     │ Weekly Meeting       │
│ in 2 h · Thu 6:30–8PM │     photo      │     ├──────────────────────┤
├───────────────────────┤                │     │ upcoming list        │
│ upcoming list         │                │     │                      │
│                       │                │     ├──────────────────────┤
├───────────────────────┴────────────────┤     │ photo                │
│ banner (only when set)                 │     ├──────────────────────┤
│ ● warning (only when needed)           │     │ banner / warning     │
└────────────────────────────────────────┘     └──────────────────────┘
```

All sizes are in `vmin` (the short side of the screen) multiplied by `--font-scale`, so
the text is the same physical size in either orientation and one setting (`font_scale`)
tunes it for the room.

---

## 2. Time rules

- Every time is a **local wall-clock string** with no timezone: `2026-10-14T18:30`. The page
  reads it as local time on the Pi (America/New_York).
- Google Calendar sends times in UTC or tagged with a zone. The Pi converts each one
  **once**, when it reads the calendar (`app/calendar_feed.py`), to the Pi's own local
  time. After that nothing converts to or from UTC: not the database, not the JSON, not
  the page. A weekly 6:30 PM meeting stays at 6:30 PM across a daylight-saving change.
- **When an event ends:**

  | Event | Ends at |
  |---|---|
  | Timed, with an end time | its end time |
  | Timed, no end time, or end equal to start | its **start** time (it disappears when it starts; deadlines work this way) |
  | All-day | midnight after its last day (`end` date if set, else the `start` date) |

- **Upcoming** = has not ended yet. **Happening now** = has started and has not ended.
- For all-day events the time part is always `00:00`, and `end` is the **last day,
  inclusive** (`2026-10-02T00:00` → `2026-10-04T00:00` is Fri–Sun, three days). A
  single-day all-day event has `end: null`. (Google's own end date is the day *after*
  the last day; the Pi takes one day off when it reads the calendar.)
- Left out when the calendar is read: cancelled events, and anything that ended before
  yesterday or starts more than 60 days from now. An event with no title is shown as
  `(No title)`, as in Google Calendar.

---

## 3. Next up (hero card)

- Shows the **earliest-starting event that has not ended**. That can be one that is
  happening now, in which case the heading says **Happening now** instead of **Next up**.
- **Except** all-day events that are already under way: a week-long "Sponsor week" shouldn't
  hide the next meeting for a week. Those are listed under `Today` instead. If an all-day
  event is the only thing left, it goes in the hero card after all.
- **Relative label** (the big accent text):

  | Situation | Label |
  |---|---|
  | Timed, happening now | `Now` |
  | All-day, happening now, last day is today | `Today` |
  | All-day, happening now, runs past today | `Through Sunday` |
  | Starts in less than 30 seconds | `Starting now` |
  | Starts in under an hour (minutes are rounded) | `in 25 min` |
  | Starts later today, under 3 h | `in 2 h 15 min` (`in 2 h` on the hour) |
  | Starts later today, 3 h or more | `in 5 h` (rounded down) |
  | Starts tomorrow | `Tomorrow` |
  | Starts within the next 6 days | the weekday, e.g. `Thursday` |
  | Later | `in 23 days` (calendar days) |

- **Absolute line:** date and time range, e.g. `Thu, Sep 24 · 6:30 – 8 PM`,
  `Fri, Sep 25 · 11:59 PM`, `Tue, Sep 29 · All day`, `Fri, Oct 2 – Sun, Oct 4`,
  `Fri, Oct 2, 5 PM – Sun, Oct 4, 2 PM`. Times on the hour drop the minutes (`8 PM`).
- Then the location, on one line, if the event has one. The title wraps to at most two
  lines. The event's description in Google Calendar is never shown.
- **No upcoming events at all:** the hero shows a friendly empty state ("Nothing on the
  schedule") and the list is hidden.

## 4. Upcoming list

- The events **after** the hero event that start within the next 30 days (events that are
  already happening count too). The hero event is not repeated.
- At most **7 rows**. If the rows don't fit on screen at the current text size, the page
  drops rows from the bottom until they do. Whatever isn't shown is counted in the last
  line: **`+N more in the next 30 days`**.
- One row per event: `day · time · title (location)`. The day label is shown
  on the first row of each day: `Today`, `Tomorrow`, or `Thu, Oct 8`. An event that
  started on an earlier day and is still going is listed under `Today`.
- Time column:

  | Event | Time column |
  |---|---|
  | Timed, same-day end | `6:30 – 8 PM` |
  | Timed, no end | `6:30 PM` |
  | Timed, ends on a later day | `5 PM – Sun` |
  | Timed, happening now | `Until 8 PM` (or `Until Sun` if it ends on a later day) |
  | All-day, one day | `All day` |
  | All-day, several days | `Oct 2 – 4` (`Oct 30 – Nov 2` across months) |

- If the hero exists but nothing else is coming up: `Nothing else in the next 30 days`.

## 5. Countdown

- Up to **two competitions** (Settings has two, e.g. one for the gas car and one for the EV
  car); `settings.json` lists those with a date as `competitions: [{name, date}]`. One line
  each, **sooner first**.
- `{name} in 231 days`, `{name} is tomorrow`, `{name} is today!`. Without a name it says
  `Competition in 231 days`. A name too long for the line is cut short with "…"; the number
  of days is never cut.
- With two lines the text is a little smaller (3.0 instead of 3.6 units), so the header
  stays the same height.
- Counted in **calendar days** between today's date and the competition's date, so
  daylight saving changes can't make it off by one.
- **Each line is hidden** once its date has passed (the other stays). **All are hidden**
  when no date is set, or when `time_synced` is `false` (a Pi that booted without internet
  may have the wrong date).

## 6. Photos

- `photos.json` lists the photos in the team's Drive folder that could be used, newest
  first, at most 200 (`app/drive_photos.py` reads the folder every 30 seconds). The page
  shuffles them once per page load, and again whenever photos are added to or taken out
  of the folder (a changed description only updates the caption). Each photo stays up for `photo_interval_s` seconds. A
  changed interval applies at the next poll, counted from when the current photo came up,
  so shortening it doesn't wait for the old timer.
- Two stacked `<img>` elements crossfade (opacity only, cheap for the Pi's GPU). The next
  photo is fully decoded before the fade starts; a photo that fails to load is skipped.
- No enabled photos → the team logo is shown in the panel instead.
- A caption, if set, sits along the bottom of the photo. It is the photo's description in
  Drive.

## 7. Data freshness, polling, and recovery

- The page polls `data/events.json?t=…`, `data/settings.json` and `data/photos.json` every
  30 s. Until the first successful load it retries every 10 s. A file whose text is the
  same as last time is not parsed again, and the screen is only redrawn when a file
  changed (or a fetch started or stopped failing).
- If a fetch fails (or gets no answer within 15 s), or the JSON doesn't have the expected
  shape, the page **keeps showing what it had**, logs the error to the console, and marks
  the data **stale**.
- Every 30 seconds the Pi reads the Google Calendar; it rewrites the JSON when the events
  changed, and every 5 minutes anyway (see §11). `generated_at` is when the files were
  written; `synced_at` is when the calendar was last read successfully (`null` if never).
  Both are heartbeats. The screen doesn't print them; it only uses them to decide when to
  warn. An event added to or removed from the calendar is on the screen within about a
  minute.
- If the calendar can't be read (no network, a link that was reset), the Pi keeps the
  events it saved last time and publishes those. The screen keeps its schedule.
- **Stale badge** ("Schedule may be out of date", amber dot) when `generated_at` is more
  than 60 minutes old (the Pi has stopped writing the files), or `synced_at` is more than
  60 minutes old or `null` (the Pi can't read the calendar). Red dot and "Can't load new
  data" when the page's last fetch of the files failed.
- The page checks the time every second but only redraws when the minute changes (the
  clock shows no seconds, so there is nothing to draw in between). At each minute change
  the clock, the relative times, what has ended and the countdown are all recomputed, so
  an event disappears within a minute of ending without a page reload.
- The page reloads itself every day at 04:00 (the Pi also reboots at 04:05).
- `font_scale` (0.5–1.5; a larger value in the file is treated as 1.5) sets `--font-scale`;
  `banner_text` shows the banner row. If not even one list row fits (a big text size with a
  banner), the list is hidden rather than shown cut off.

## 8. Edge rules (PLAN.md §6, Step 1.1)

| Rule | Behavior |
|---|---|
| No events | Hero shows "Nothing on the schedule"; list hidden |
| Event leaves when it **ends**, not when it starts | §2 "When an event ends" |
| All-day and multi-day events | One row with a range (§4 time column) |
| More than 7 upcoming | First 7 (or fewer, if they don't fit), then `+N more in the next 30 days` |
| `banner_text` non-empty | Banner row appears along the bottom; the grid reflows |
| No competition with a date, or `time_synced: false` | Countdown hidden |
| No enabled photos | Team logo placeholder in the photo panel |

For testing, the page accepts `?now=2026-11-01T01:58`. The page clock then starts at
that local time and ticks forward normally, so you can watch midnight, DST changes, or an
event ending without changing the computer's clock. The kiosk URL never uses it.

## 9. Where every displayed field comes from (acceptance test 1.2.1)

| On screen | Source |
|---|---|
| Clock and date | The Pi's system clock (browser `Date`) |
| Team logo (header and empty photo panel) | `web/img/logo.svg` (static file; replace with the real logo) |
| Countdown names and dates | `settings.competition_name`/`_date` and `competition2_name`/`_date` → `settings.json` `competitions` |
| Countdown hidden when clock not synced | `events.json` → `time_synced` (from `timedatectl`) |
| Event title | Google Calendar event title (`SUMMARY`) → `events.title` |
| Times, "Happening now", ranges | The event's start and end (`DTSTART`, `DTEND`) → `events.start_at`, `events.end_at`, `events.all_day` |
| Location | The event's location (`LOCATION`) → `events.location` |
| Photos | The Drive folder's image files (newest 200) → `photos.filename` where it isn't NULL |
| Caption | The Drive file's description → `photos.caption` |
| Seconds per photo | `settings.photo_interval_s` |
| Text size | `settings.font_scale` |
| Banner | `settings.banner_text` |
| Stale badge | `events.json` → `generated_at` and `synced_at` |

No gaps: the page only computes whether each event is upcoming or has ended, plus how to
word times ("in 2 h", "Tomorrow"). Not used from the calendar: descriptions, guests,
colors, reminders, attachments.

## 10. Repeating events (acceptance test 1.2.3)

Repeating events are made and changed in Google Calendar, which is what it is good at.
When the Pi reads the calendar it expands each repeating event into one event per
occurrence, honoring skipped and moved occurrences, and saves those. The screen never
computes repeats.

| Case | In Google Calendar | Actions |
|---|---|---|
| Set up the weekly meeting (Thursdays 6:30–8 PM through Dec 10) | Create the event, **Does not repeat** → **Custom**, weekly, ends Dec 10 | 1 |
| Cancel one week (Oct 22) | Open that week's event → **Delete** → **This event** | 1 |
| Time changes mid-semester (7 PM from Oct 29 on) | Open the Oct 29 event → change the time → **Save** → **This and following events** | 1 |
| Move one week only (Friday instead of Thursday) | Drag that week's event to Friday → **This event** | 1 |

Every case takes one action, against the three the plan allowed.

---

## 11. Decisions made while building (not spelled out in PLAN.md)

Listed in the order they were made, so they're easy to review or undo. By topic:

| Topic | Decisions |
|---|---|
| Accounts and security | 12, 13, 17, 19, 26, 33 |
| The Google Calendar | 31, 32, 34, 35, 36, 37 |
| What the screen shows | 1, 2, 3, 4, 6, 15, 23, 30, 38, 48 |
| Refreshing the screen's files | 5, 24, 37 |
| The Pi | 18, 20, 21, 22, 28, 29, 47 |
| Photos | 8, 9, 10, 27, 44, 45, 46 |
| Testing and the docs | 7, 11, 14, 16, 25 |

1. **The upcoming list excludes the hero event**, so the next event isn't shown twice.
2. **"+N more in the next 30 days"** instead of "+N more this month": N counts the list's
   30-day window, so "this month" would be wrong near the end of a month.
3. **Events without an end time disappear at their start time** (§2). Google Calendar
   events normally have an end, so this mostly applies to zero-length events.
4. **All-day `end_at` is the last day, inclusive, at 00:00**; a single-day all-day event is
   stored with `end_at` NULL. One time format everywhere keeps parsing and sorting simple.
5. **`display-publish` runs all the time and reads the calendar every 30 seconds**, not
   only at 00:05 (PLAN.md) and no longer as a timer. A daily publish would leave
   `generated_at` many hours old and the 60-minute stale badge always on; a 30-second cycle
   is what Owen asked for (2026-09-30) so that calendar changes reach the screen within
   about a minute with nobody touching the admin page. The files are rewritten when the
   events changed and every 5 minutes anyway (a heartbeat that also refreshes
   `time_synced` after NTP syncs and rolls the event window over after midnight). A
   service, not a timer, because starting Python every 30 s would cost a Pi 4 about a
   second of CPU each time. (It was a 15-minute timer, then a 5-minute one.)
6. **The list fits itself to the screen**: at most 7 rows, fewer if they would overflow at a
   large `font_scale` (acceptance test 2.2.13 requires no overflow at 1.3).
7. **`?now=` test hook** on the display page (§8), for the Date-mocking tests.
8. **HEIC photos are accepted** (via `pillow-heif`) and converted to JPEG like everything
   else. If `pillow-heif` is missing, a HEIC file is listed as one the screen can't use.
9. ~~admin.js uploads photos one file per request, with progress.~~ Gone with uploads
   (decision 44); nginx is back to its 1 MB request limit.
10. **Small thumbnails** (`web/photos/thumbs/`) are made when a photo is downloaded, so
    the admin Photos page loads fast on a phone.
11. **The status page includes the recent audit log**. Test 2.5.7 needs somewhere to read it.
12. **`/etc/display/env` is mode 640 root:displayapp** (the plan says 600 root:display).
    The group read lets a script run by hand as the service account (`sudo -u displayapp
    .venv/bin/python scripts/init_db.py`) see the same settings as the service, instead of
    quietly falling back to the defaults. It exposes nothing new: the service account can
    already read its own environment. systemd reads the file as root either way.
13. **Helper scripts that run as root are copied to `/usr/local/(s)bin` and owned by root**
    (`display-restart`, `display-netcheck`). A root job must never run a file that a
    non-root account can edit.
14. **Test images are generated when the tests run** instead of being committed
    (`img_30mb.jpg` alone would bloat the repo). They have the names from PLAN.md §4.
15. **All-day events that are already under way don't take the hero card** (§3), so a
    multi-day event doesn't bury the next meeting.
16. **Most of the display checks from PLAN.md Step 2.2 also run automatically** in
    `tests/test_display.py` (headless Chromium through Playwright; skipped if Playwright isn't
    installed). They stay in docs/TESTING.md as manual checks for the real TV.
17. **The web app, the data refresh and the backup run as a service account, `displayapp`,
    not as `display`** (PLAN.md §7 2.5 and §8 3.2 say `User=display`). Raspberry Pi OS gives
    the login user made in Imager passwordless sudo for everything, so a web-app bug running
    as `display` would be root, and the two-command sudo rule would protect nothing.
    `displayapp` has no password or login, owns only `data/`, `web/data/` and `web/photos/`,
    and is the account `pi/sudoers-display` names. `display` stays the desktop/kiosk user
    and the SSH account, as in the plan. Test 2.5.6 therefore checks `sudo -l -U displayapp`.
18. **The nightly reboot is a systemd timer** (`display-reboot.timer`), not a root cron job,
    like every other scheduled job here (and `cron` isn't guaranteed on a fresh image).
19. **The sudo rules forbid arguments** (`display-restart ""`, `/sbin/reboot ""`), so the
    web app can't run e.g. `reboot --halt`.
20. **Commands corrected by research at build time (2026-09-22):**
    - `tailscale serve --bg http://127.0.0.1:80`, because the plan's `tailscale serve --bg
      https / ...` is the old syntax and current Tailscale rejects it.
    - The browser command is `chromium` (Raspberry Pi OS trixie has no `chromium-browser`).
    - Kiosk mode adds `--password-store=basic`, so auto-login doesn't hit a
      keyring-unlock prompt.
    - Rotation goes through the desktop's Screens tool / kanshi, since `config.txt`
      rotation does nothing.
21. **`dtparam=watchdog=on` is added as the plan says**, although current Pi 4/5 firmware
    already has the watchdog on (harmless, and it covers other models).
22. **The read-only overlay decision is pre-answered "leave it off"** in SETUP.md step 11:
    with it on, Tailscale's login and certificate would be wiped at every nightly reboot.
    SETUP.md still lists what must stay writable if the team turns it on anyway.
23. **The display checks the data's shape itself, not with the JSON Schema.** PLAN.md says
    "both display and backend validate against" `events.schema.json`, but using the schema
    in the browser would need a library and a build step. `web/app.js` (`readEvent`,
    `readEvents`) checks what the page relies on: the time formats, title and `all_day`.
    It accepts `generated_at` and `synced_at` with or without seconds.
    The backend validates against the full schema before every publish.
24. **The three JSON files are replaced one after another.** Each file is always complete,
    and if writing any temporary file fails, none are replaced. But if the *second or third*
    rename failed, the first file would already be new. The log then says which files were
    replaced. The display treats each file independently, and the next publish (at most 5
    minutes later) makes them consistent again.
25. **The mockups assume a 1920×1080 TV**, because the real display's resolution is still a
    TODO in PLAN.md §2.2. Every size is relative to the screen, so re-shoot them once the TV
    is known.
26. **The login limit is counted per gunicorn worker**, in memory, as PLAN.md allows. With
    two workers, a client can get up to 10 wrong guesses in 10 minutes before every worker
    blocks it, rather than exactly 5. Anyone guessing must already be on the tailnet, so
    this was kept simple. Storing the counts in SQLite would make it exact.
27. **Photo size limits beyond the 20 MB file cap:** at most 100 megapixels for JPEGs (they
    decode at a reduced size) and 50 megapixels for PNG, WebP and HEIC (they decode at full
    size), so one photo can't exhaust a 2 GB Pi's memory. Only the JPEG, PNG, WebP and HEIF
    decoders are ever run on a photo.
28. **`kiosk.sh` waits for nginx for as long as it takes** (PLAN.md §8 3.3 says up to 60 s,
    then open the browser anyway). A kiosk-mode browser opened on an error page has no
    way back until something restarts it, whereas a script that keeps waiting opens the
    browser by itself the moment nginx answers. It logs "Still waiting" once a minute.
29. **Photo files are flushed to disk (fsync) before they are renamed into place**, like the
    JSON files, so pulling the power right after a download can't leave an empty photo
    file next to its database row.
30. **No "Updated N min ago" line on the screen** (PLAN.md's footer had one; Owen asked for
    it to go, 2026-09-29). The bottom row stays empty while all is well. The amber and red
    warnings still appear when the data is over 60 minutes old or can't be loaded. When
    the data was last written is on the admin **Status** page.
31. **Events come from the team's Google Calendar, not the admin pages** (Owen and the
    team leads, 2026-09-29). PLAN.md §5.1, §7 Step 2.3 and the recurrence rules in §6 are
    replaced by this. The admin site keeps Photos, Settings and Status. The `events` table
    is a copy that is rebuilt from the calendar; an old database has that table dropped
    and recreated at the next start.
32. **The Pi reads the calendar's secret iCal link**, the simplest access that keeps the
    calendar private: no Google Cloud project, no sign-in. If the university account has
    secret links turned off, only `calendar_feed.fetch` needs replacing.
33. **The link is a secret and lives only in `/etc/display/env`** (`CALENDAR_ICS_URL`). It
    can't be set from the admin pages, isn't stored in the database, and never appears in
    a log line or on a page; log lines name the host only. Plain `http://` links are
    refused, except to the Pi itself (which is how the tests serve a feed).
34. **One time-zone conversion, at the boundary** (§2). PLAN.md says "no UTC conversion
    anywhere"; Google sends UTC, so that rule now starts after `calendar_feed.parse`.
35. **Type badges and notes are gone from the screen.** Google Calendar has no event type,
    and its descriptions are mostly video-call links, some of them private.
36. **Two libraries were added**, `icalendar` and `recurring-ical-events`. Repeating events
    with skipped and moved weeks are where hand-written calendar code goes wrong. Both are
    pure Python, so they install on the Pi without a compiler (checked by a test).
37. **A read that fails changes nothing.** The saved events stay, the files are still
    rewritten every 5 minutes, and the screen shows the amber badge once the last good
    read is over an hour old. The events table and the screen's files are only rewritten
    when the calendar actually changed, to spare the SD card.
38. **The page polls every 30 seconds, but only redraws when a file changed.** With a
    30-second cycle on both sides a change is on screen within about a minute; without the
    "only when changed" rule the Pi would lay the page out again every 30 s for nothing.
39. **Text size stops at 1.5** (the form said 2.0). The stress test of 2026-09-30 showed
    the list empty from about 1.4 with a banner and the page overflowing from 1.8. At 1.5
    everything still fits at 1080p, 720p and portrait, and when no list row fits the list
    hides itself. Sizes above 1.5 already stored are shown as 1.5.
40. **A database the Pi can't open gets its own page, and the Status page works without
    one.** A completely full SD card stops SQLite from even opening (it needs a 32 KB
    `-shm` file), which used to turn every admin page into "Something went wrong",
    including the Status page that says the card is full. Now every page says the card is
    probably full, the Status page still shows the disk line, and "database is locked"
    (someone running `sqlite3` by hand) says so too.
41. **The calendar reader never exits and doesn't repeat itself.** On a full disk it used
    to die at start-up (systemd restarting it every 5 s) or log a traceback per 30-second
    round. Now a failed start is retried next round, errors are one line, and the same
    line is logged at most once every 10 minutes. It also re-reads the calendar link from
    the env file every round, so a changed link needs no restart.
42. **The reader is stricter with the feed:** events that repeat every second or minute
    are skipped (millions of occurrences before the 500 cap), one broken event is skipped
    instead of rejecting the whole calendar, the 20-second limit is for the whole download
    (not per packet), redirects are only followed to https, a download cut short by the
    server is refused, and control characters and text-direction tricks are dropped from
    titles.
43. **HEAD requests to the login page are not login attempts.** They used to count as
    wrong passwords, so five link previews or uptime checks locked the IP out for
    10 minutes.
44. **Photos come from a Google Drive folder; the admin Photos page is read-only** (Owen,
    2026-10-06: "quicker than having it done through the admin page"). The folder is
    shared as "Anyone with the link" and read with a Google Cloud API key (Owen chose this
    over a private folder shared with a service account: less setup, no key file on the
    Pi, but anyone with the link can see the photos). Uploading, captions, hide and delete
    are gone from the admin pages; captions are the files' descriptions in Drive. Photos
    uploaded before are dropped with the old `photos` table.
45. **The Pi downloads about 20 seconds' worth of new photos per 30-second round** (at
    least one), so a big first batch doesn't hold up the calendar read in the same loop.
    Only new or changed files (Drive's `md5Checksum`) are downloaded; a file that can't be
    used is remembered with the reason and not downloaded again until it changes. A photo
    whose copy went missing on the Pi is downloaded again.
46. **The API key travels in the `X-Goog-Api-Key` header, never in a URL**, so it can't
    leak into an error message or a log line; the admin pages never show it. They do
    show the folder link to logged-in admins (an "Open the folder" button), since the
    team leads need it to add photos anyway.
47. **`display-admin` gets 4 minutes to start** (`TimeoutStartSec=240`). Its start-up
    read now includes the photo folder, which on slow Wi-Fi with a big new batch can take
    over systemd's default 90 seconds, and a start that times out stops the admin app.
48. **Two competition countdowns** (Owen, 2026-10-13: "one race is for the EV car and one is
    for the gas car"). Settings has two name/date pairs instead of one; the screen shows a
    line for each, sooner first, and drops each once it has passed. Two fixed slots rather
    than a list keep the Settings form simple; the header only fits two lines anyway.
    `settings.json` now carries `competitions: [{name, date}]` instead of
    `competition_name` and `competition_date`.
