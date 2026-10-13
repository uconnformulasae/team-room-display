# Events from Google Calendar — design

Approved by Owen on 2026-09-29. This replaces the admin event pages described in
[PLAN.md](../../PLAN.md) §7 (Step 2.3). Everything else in the plan stands.

## Goal

The team leads keep the schedule in a Google Calendar. The team-room screen shows the
next events from that calendar. Nobody types events into the admin page any more.

## Decisions made with Owen

| Question | Decision |
|---|---|
| How the Pi reads the calendar | The calendar's **secret iCal link** (Google Calendar → Settings → the calendar → *Integrate calendar* → *Secret address in iCal format*) |
| What the admin page keeps | Photos, Settings, Status. The event pages are removed |
| When an event leaves the screen | When it **ends** (unchanged) |
| Type badges (Meeting, Due, ...) | Removed. Google Calendar has no event type |
| What the screen lists | Unchanged: the next event in the large card, then up to 7 more that start within 30 days |
| Event descriptions | Not shown. Google descriptions are mostly video-call links |
| Where it runs | A Raspberry Pi 4 or 5 (see "Built for the Pi" below) |

## How it works

```
Google Calendar ──secret iCal link──► scripts/publish.py --every 30 (always on, on the Pi)
                                          │ 1. read the link, expand repeats
                                          │ 2. save the events in SQLite
                                          ▼ 3. write web/data/*.json
Chromium kiosk ◄── static files in web/ ──┘
```

The screen still reads only files on the Pi. It never talks to Google, so the secret
link never reaches the browser, and the screen keeps working when the network is down.

Two designs were rejected:

- **The browser reads Google itself.** The secret link would be in the page, it would
  fail without a network, and Google refuses cross-site requests for it anyway.
- **The admin app fetches in the background.** The schedule would stop updating whenever
  the admin app is down. The plan's rule is that the screen never depends on Flask.

## Parts

### 1. `app/calendar_feed.py` — reading the link

Three functions, each usable and testable alone.

- `fetch(source)` returns the feed's bytes. `source` is the `CALENDAR_ICS_URL` setting: an
  `https://` address, or a path to a local `.ics` file (for the demo and the tests).
  Plain `http://` is refused, except to the Pi itself (`127.0.0.1`), which is how the
  tests serve a feed. It waits at most 20 seconds and reads at most 5 MB.
- `parse(data, window_start, window_end, timezone)` returns a `Feed`: the calendar's
  `name`, the `events`, and how many were `skipped`. Each event is a dict: `uid`, `title`,
  `start_at`, `end_at`, `all_day`, `location`.
- `sync(conn, source)` runs the two above, saves the result, and records what happened.
  It returns `(ok, message)` and never raises for a bad feed or a missing network: a
  failed read leaves the saved events exactly as they were.

Rules in `parse`:

| Feed | Stored |
|---|---|
| Repeating event | One event per occurrence in the window. Skipped dates and moved occurrences are honored (done by the `recurring-ical-events` library) |
| `STATUS:CANCELLED` | Left out |
| Timed event, any time zone or UTC | Converted **once** to the Pi's local wall-clock time, `2026-10-15T18:30` |
| Timed event with no end, or end equal to start | `end_at` NULL (it leaves the screen when it starts) |
| All-day event | `start_at` at 00:00; the feed's end date is the day *after* the last day, so `end_at` is that minus one day, or NULL for a single day |
| No title | `(No title)`, as Google shows it |
| Title or location over 200 characters | Cut to 200 |

The window is yesterday 00:00 to 60 days ahead. At most 500 events are kept.

**Time zones.** PLAN.md says "no UTC conversion anywhere". Google sends UTC and
zone-tagged times, so one conversion at this boundary is unavoidable. Everything after
it (the database, the JSON, the screen) is still local wall-clock time with no zone.
The target zone is the Pi's own (`America/New_York`, set in SETUP.md step 1).

**The link is a secret.** It lives only in `/etc/display/env`. It is never committed,
never stored in the database, never written to a log (log lines name the host only),
and cannot be changed from the admin page.

### 2. Database

`events` is rebuilt from the feed, so it becomes a copy, not the source of truth.

```sql
CREATE TABLE events (
  id        INTEGER PRIMARY KEY,
  uid       TEXT NOT NULL,     -- the calendar's id for the event (repeats share one)
  title     TEXT NOT NULL,
  start_at  TEXT NOT NULL,     -- 'YYYY-MM-DDTHH:MM' local wall clock
  end_at    TEXT,
  all_day   INTEGER NOT NULL DEFAULT 0,
  location  TEXT
);

CREATE TABLE calendar_sync (   -- always exactly one row
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  checked_at    TEXT,          -- last attempt
  synced_at     TEXT,          -- last attempt that worked
  ok            INTEGER,
  message       TEXT,
  calendar_name TEXT
);
```

An old database (with the `type` and `series_id` columns) has its `events` table dropped
and recreated when the app starts. Events typed into the old admin pages are lost; that
was agreed. Photos, settings and the audit log are untouched.

`sync` replaces the rows only when the feed's events differ from the saved ones, inside
one transaction, so a reader never sees a half-filled table.

### 3. Publishing

`events.json` changes shape (`fixtures/events.schema.json` is updated to match):

```json
{
  "generated_at": "2026-09-29T18:35:00",
  "synced_at": "2026-09-29T18:35:00",
  "time_synced": true,
  "events": [
    {"title": "Weekly Meeting", "start": "2026-10-01T18:30", "end": "2026-10-01T20:00",
     "all_day": false, "location": "Team Room"}
  ]
}
```

`synced_at` is when the calendar was last read successfully, or `null` if never. `id`,
`type` and `notes` are gone.

`scripts/publish.py` reads the calendar, then publishes. It exits with status 1 only if
the screen's files could not be written. A failed calendar read is logged as a warning
and shown on the Status page; the files are still rewritten from the saved events.

### 4. The screen

- Badges are removed from the large card and from the list (three columns: day, time,
  title).
- Notes are removed from the large card.
- The amber "Schedule may be out of date" badge appears when **either** the files are
  over 60 minutes old (as before) **or** the calendar has not been read successfully for
  over 60 minutes (new), including when it has never been read.
- Nothing else changes: what counts as ended, the wording of times, the photo panel,
  the banner, the countdown, the empty bottom corner.

### 5. Admin pages

- Removed: the Events list, the event form, weekly series, Duplicate.
- `/admin` goes to Photos.
- **Status** gains a Calendar row (name, when it was last read, how many events, or the
  error) and a **Read calendar now** button, which reads the calendar and republishes.
- Photos and Settings are unchanged.

### 6. Built for the Pi

| Concern | How it is handled |
|---|---|
| ARM processor | The two new libraries (`icalendar`, `recurring-ical-events`) and everything they pull in are pure Python: no compiler, no system packages |
| Runs unattended | `display-publish.service`, an always-on loop reading every 30 seconds, as the `displayapp` service account, started after the network is up |
| Campus Wi-Fi drops | 20-second timeout; a failed read changes nothing; the next run tries again; the screen shows the amber badge after an hour |
| Boot without a network | The saved events are published at once, so the screen has a schedule before the first read succeeds |
| SD card wear | The events table is rewritten only when the calendar changed |
| 2 GB of memory | The feed is capped at 5 MB and 500 events |
| Power cuts | Same atomic writes as before: one SQLite transaction, then write-fsync-rename for the JSON |

### 7. Docs

SETUP.md gains a step for getting the secret link into `/etc/display/env`. UPDATING.md
becomes "add events in Google Calendar; they reach the screen within about a minute".
TROUBLESHOOTING.md gains "events are missing or out of date". SPEC.md is updated
throughout. PLAN.md is left as written, as the record of the original plan.

## Errors

| What goes wrong | What happens |
|---|---|
| `CALENDAR_ICS_URL` not set | Status: "No calendar link is set". Screen: no events, amber badge |
| No network, timeout, HTTP error | Saved events stay. Warning in the log. Status shows the error |
| The link returns a web page (wrong or reset link) | Same, with "That link did not return a calendar" |
| Feed over 5 MB | Same, with "The calendar file is too big" |
| One event the parser cannot handle | That event is skipped and counted in the message; the rest are used |
| Database or disk full | As today: the old files stay, the error is logged |

## Testing

- `tests/test_calendar_feed.py`: `parse` against small `.ics` fixtures (weekly with a
  skipped and a moved week, UTC, another time zone, across the daylight-saving change,
  all-day, multi-day, cancelled, no end, no title); `fetch` against a local HTTP server
  (ok, 404, timeout, too big, not a calendar, `http://` refused); `sync` (first read,
  unchanged read writes nothing, failed read keeps the events, status row).
- `tests/test_publish.py`, `tests/test_schema.py`, the fixtures: the new JSON shape.
- `tests/test_display.py`: no badges, the calendar-stale badge, everything else as before.
- `tests/test_admin.py`, `tests/test_status.py`: event pages gone, Status calendar row,
  the button.
- `tests/test_pi_files.py`: the service reads every 30 seconds and never gives up.
- Checklist additions in TESTING.md for the real Pi and the real calendar.

## Not included

- Choosing between several calendars, or merging them.
- Editing calendar events from the admin page.
- Google sign-in or a service account. If the university account has secret links turned
  off, a second reader can replace `fetch`; nothing else would change.

## As built (2026-09-29)

Built as designed, with these additions found while building:

- `scripts/init_db.py` also reads the calendar and prints how it went, so setting up a Pi
  shows at once whether the link works.
- `scripts/seed_demo.py` writes a demo calendar file and reads it the same way as a real
  link. It refuses to run where a real link is set.
- `display-publish.service` has `TimeoutStartSec=120`, so a read that hangs can't hold up
  the next one.
- `tests/test_repo.py` refuses any file that contains something shaped like a real secret
  calendar link.
- 2026-09-30, after the first test with a real calendar: Owen asked for a 30-second to
  one-minute cycle with no admin involvement. The 5-minute timer became an always-on
  service (`publish.py --every 30`) that rewrites the files only when the events changed,
  and the page polls every 30 s but redraws only when a file changed.
- Not yet tried on a real Pi: TESTING.md 2.3.12 and 2.3.14 – 2.3.17.
