# Events from Google Calendar Implementation Plan

> A step-by-step plan, written before the work and carried out on 2026-09-29. Kept as the record of how the change was made; the design it follows is in `docs/decisions/`.

**Goal:** The team-room screen shows events read from the team's Google Calendar (secret iCal link) instead of events typed into the admin pages.

**Architecture:** A job on the Raspberry Pi (`scripts/publish.py`, every 5 minutes) reads the iCal link, expands repeating events, saves them in SQLite, and rewrites `web/data/*.json`. The screen still reads only those static files. The admin site keeps Photos, Settings and Status.

**Tech Stack:** Python 3.11+, Flask, stdlib `sqlite3` and `urllib`, `icalendar` + `recurring-ical-events` (both pure Python, so they install on the Pi's ARM processor with no compiler), vanilla JS/CSS, systemd timers, pytest + Playwright.

**Design:** [docs/decisions/2026-09-29-google-calendar-design.md](../decisions/2026-09-29-google-calendar-design.md)

**Status: carried out on 2026-09-29.** Tasks 4, 5 and 7 had to land together (the app could not be imported until the event form was gone). Task 10, step 10 is the local demo.

**Conventions for every task**

- Test first, watch it fail, then write the code (TDD).
- Run tests with:
  `PLAYWRIGHT_CHROMIUM=~/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell .venv/bin/python -m pytest -q -p no:cacheprovider <files>`
- **No commits.** Owen has not asked for any; the repo has none. Each task ends with a test run instead.
- Code style: boring and readable; comments say *why*. The next maintainer is a new student.
- The calendar link is a secret: never in git, the database, a log line or an error page.

## File map

| File | Change | Responsibility |
|---|---|---|
| `requirements.txt` | modify | add the two libraries |
| `fixtures/calendar_sample.ics` | create | a Google-style feed dated around 2026-09-22 |
| `app/calendar_feed.py` | create | `fetch`, `parse`, `sync`, `CalendarError` |
| `app/schema.sql`, `app/db.py` | modify | new `events` shape, `calendar_sync`, drop the old table |
| `app/models.py` | modify | remove event editing; add `replace_events`, `calendar_status`, `record_calendar_check`, `count_events` |
| `app/publish.py`, `fixtures/events.schema.json`, `fixtures/events_*.json` | modify | new JSON shape with `synced_at` |
| `app/__init__.py` | modify | `CALENDAR_ICS_URL` setting |
| `scripts/publish.py`, `scripts/init_db.py`, `scripts/seed_demo.py` | modify | read the calendar, then publish |
| `app/admin/routes.py`, `app/forms.py`, `app/auth.py`, templates, `admin.js`, `admin.css` | modify | remove event pages; Status calendar row and button |
| `app/admin/filters.py`, `events.html`, `event_form.html` | delete | only served the event pages |
| `web/index.html`, `web/app.js`, `web/style.css` | modify | no badges, no notes, calendar-stale warning |
| `pi/systemd/display-publish.*`, `pi/env.example` | modify | every 5 minutes, after the network; the new setting |
| `tests/*` | modify/create | see each task |
| `docs/*`, `README.md`, `fixtures/README.md` | modify | see Task 10 |

---

### Task 1: Libraries and the sample feed

**Files:** modify `requirements.txt`; create `fixtures/calendar_sample.ics`

- [x] **Step 1:** In `requirements.txt`, after the `jsonschema` line, add:

```
icalendar>=6.1,<8              # reads the Google Calendar feed (pure Python: fine on the Pi)
recurring-ical-events>=3,<4    # expands repeating events, skipped and moved weeks included
```

- [x] **Step 2:** `.venv/bin/pip install -r requirements.txt` (already installed during design; confirms the pins resolve).

- [x] **Step 3:** Create `fixtures/calendar_sample.ics`: a `VCALENDAR` with `X-WR-CALNAME:Formula SAE`, a `VTIMEZONE` for America/New_York, and these events. "Now" in the tests is Tue 2026-09-22 14:10, so the window is 2026-09-21 to 2026-11-21.

| UID | What | Feed values | Expected local result |
|---|---|---|---|
| `brake@t` | Brake bias test, Test lot | `TZID=America/New_York` 2026-09-21 18:00–20:00 | yesterday, kept |
| `shop@t` | Machine shop open hours, Machine shop | NY 2026-09-22 13:00–17:00 | happening now |
| `review@t` | Suspension design review, Team Room | NY 2026-09-22 16:00–17:00 | today |
| `weekly@t` | Weekly Meeting, Team Room | NY 2026-09-24 18:30–20:00, `RRULE:FREQ=WEEKLY;UNTIL=20261211T045959Z;BYDAY=TH`, `EXDATE` 2026-10-22, DESCRIPTION with HTML | every Thursday 18:30, also after the Nov 1 clock change; Oct 22 missing |
| `weekly@t` + `RECURRENCE-ID` 2026-10-29 | Weekly Meeting (moved to Friday), Room 212 | NY 2026-10-30 19:00–20:30 | replaces Oct 29 |
| `report@t` | Design report due | `DTSTART:20260926T035900Z`, no `DTEND` | 2026-09-25T23:59, end NULL |
| `fair@t` | Career fair, Student Union | `VALUE=DATE` 2026-09-29 to 2026-09-30 | all day, end NULL |
| `testing@t` | Testing weekend, Test track | `VALUE=DATE` 2026-10-02 to 2026-10-05 | all day, end 2026-10-04T00:00 |
| `bbq@t` | Team BBQ, Picnic pavilion | `20261003T160000Z`–`20261003T190000Z` | 12:00–15:00 |
| `sponsor@t` | Sponsor call | `TZID=America/Los_Angeles` 2026-10-08 15:00–16:00 | 18:00–19:00 |
| `untitled@t` | (no SUMMARY) | NY 2026-10-12 10:00–11:00 | title `(No title)` |
| `cancelled@t` | Cancelled social, `STATUS:CANCELLED` | `20261009T210000Z`–`220000Z` | left out |
| `old@t` | Season kickoff | NY 2026-09-10 18:00–19:00 | outside the window |
| `far@t` | Winter testing | `VALUE=DATE` 2027-01-15 to 2027-01-16 | outside the window |

- [x] **Step 4:** Check it parses: `.venv/bin/python -c "import icalendar; print(len(list(icalendar.Calendar.from_ical(open('fixtures/calendar_sample.ics','rb').read()).walk('VEVENT'))))"` → `14`.

---

### Task 2: `parse` — feed bytes to event rows

**Files:** create `app/calendar_feed.py`, `tests/test_calendar_feed.py`

- [x] **Step 1: Write the failing tests** (`tests/test_calendar_feed.py`)

```python
"""Reading the Google Calendar feed: parse (rules), fetch (network), sync (database)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import calendar_feed
from helpers import FIXTURES

NEW_YORK = ZoneInfo('America/New_York')
SAMPLE = (FIXTURES / 'calendar_sample.ics').read_bytes()
WINDOW = (datetime(2026, 9, 21), datetime(2026, 11, 21))


def parse_sample():
    return calendar_feed.parse(SAMPLE, *WINDOW, timezone=NEW_YORK)


def by_title(feed):
    found = {}
    for event in feed.events:
        found.setdefault(event['title'], []).append(event)
    return found


def test_calendar_name_and_event_shape():
    feed = parse_sample()

    assert feed.name == 'Formula SAE'
    assert feed.skipped == 0
    assert by_title(feed)['Machine shop open hours'] == [{
        'uid': 'shop@t', 'title': 'Machine shop open hours', 'start_at': '2026-09-22T13:00',
        'end_at': '2026-09-22T17:00', 'all_day': False, 'location': 'Machine shop'}]


def test_events_are_sorted_by_start():
    starts = [event['start_at'] for event in parse_sample().events]

    assert starts == sorted(starts)


def test_weekly_event_repeats_at_the_same_wall_clock_time_across_the_clock_change():
    weekly = by_title(parse_sample())['Weekly Meeting']
    days = [event['start_at'][:10] for event in weekly]

    assert days == ['2026-09-24', '2026-10-01', '2026-10-08', '2026-10-15', '2026-11-05', '2026-11-12', '2026-11-19']
    assert {event['start_at'][11:] for event in weekly} == {'18:30'}  # Nov 1 is when the clocks change
    assert {event['end_at'][11:] for event in weekly} == {'20:00'}


def test_a_skipped_week_is_missing_and_a_moved_week_is_moved():
    feed = by_title(parse_sample())
    every_start = [event['start_at'] for events in feed.values() for event in events]

    assert not any(start.startswith('2026-10-22') for start in every_start)  # EXDATE
    assert not any(start.startswith('2026-10-29') for start in every_start)  # moved away
    assert feed['Weekly Meeting (moved to Friday)'][0]['start_at'] == '2026-10-30T19:00'
    assert feed['Weekly Meeting (moved to Friday)'][0]['location'] == 'Room 212'


@pytest.mark.parametrize('title, start, end', [
    ('Team BBQ', '2026-10-03T12:00', '2026-10-03T15:00'),      # sent in UTC
    ('Sponsor call', '2026-10-08T18:00', '2026-10-08T19:00'),  # sent in Pacific time
])
def test_times_from_other_zones_become_local_wall_clock(title, start, end):
    [event] = by_title(parse_sample())[title]

    assert (event['start_at'], event['end_at']) == (start, end)


def test_event_with_no_end_has_no_end():
    [event] = by_title(parse_sample())['Design report due']

    assert (event['start_at'], event['end_at'], event['all_day']) == ('2026-09-25T23:59', None, False)


def test_all_day_events_end_on_their_last_day_inclusive():
    feed = by_title(parse_sample())

    assert feed['Career fair'] == [{'uid': 'fair@t', 'title': 'Career fair', 'start_at': '2026-09-29T00:00',
                                    'end_at': None, 'all_day': True, 'location': 'Student Union'}]
    [weekend] = feed['Testing weekend']
    assert (weekend['start_at'], weekend['end_at'], weekend['all_day']) == ('2026-10-02T00:00', '2026-10-04T00:00', True)


def test_cancelled_and_out_of_window_events_are_left_out():
    titles = set(by_title(parse_sample()))

    assert 'Brake bias test' in titles  # yesterday: still inside the window
    assert not titles & {'Cancelled social', 'Season kickoff', 'Winter testing'}


def test_event_without_a_title_is_shown_like_google_shows_it():
    assert by_title(parse_sample())['(No title)'][0]['start_at'] == '2026-10-12T10:00'


def test_descriptions_are_never_kept():
    assert all(set(event) == {'uid', 'title', 'start_at', 'end_at', 'all_day', 'location'}
               for event in parse_sample().events)


def test_long_text_is_cut_and_line_breaks_flattened():
    feed = calendar_feed.parse(
        b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nBEGIN:VEVENT\r\nUID:long@t\r\n'
        b'DTSTAMP:20260901T000000Z\r\nDTSTART:20260925T160000Z\r\nDTEND:20260925T170000Z\r\n'
        b'SUMMARY:' + b'A' * 300 + b'\r\nLOCATION:Building 4\\nRoom 212\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n',
        *WINDOW, timezone=NEW_YORK)

    [event] = feed.events
    assert event['title'] == 'A' * 200
    assert event['location'] == 'Building 4 Room 212'


def test_at_most_500_events_the_soonest_ones():
    events = b''.join(
        b'BEGIN:VEVENT\r\nUID:%d@t\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:202609%02dT%02d%02d00Z\r\n'
        b'SUMMARY:Event %d\r\nEND:VEVENT\r\n' % (n, 22 + n // 200, 12 + (n % 200) // 60, n % 60, n)
        for n in range(600))
    feed = calendar_feed.parse(b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n' + events + b'END:VCALENDAR\r\n',
                               *WINDOW, timezone=NEW_YORK)

    assert len(feed.events) == 500
    assert feed.events[0]['title'] == 'Event 0'


@pytest.mark.parametrize('data', [b'', b'<html><body>Sign in</body></html>', b'\xff\xfe\x00 not text'])
def test_something_that_is_not_a_calendar_is_an_error(data):
    with pytest.raises(calendar_feed.CalendarError, match='did not return a calendar'):
        calendar_feed.parse(data, *WINDOW, timezone=NEW_YORK)


def test_an_empty_calendar_is_fine():
    feed = calendar_feed.parse(b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\nEND:VCALENDAR\r\n',
                               *WINDOW, timezone=NEW_YORK)

    assert (feed.name, feed.events, feed.skipped) == ('', [], 0)
```

- [x] **Step 2:** Run `tests/test_calendar_feed.py`. Expected: collection error, `cannot import name 'calendar_feed'`.

- [x] **Step 3: Write `app/calendar_feed.py`** (the `fetch` and `sync` functions come in Tasks 3 and 5)

```python
"""Reading the team's Google Calendar.

The team leads keep the schedule in Google Calendar. Its settings page has a "Secret
address in iCal format": a private link to the whole calendar as one file. This module
reads that file (fetch), turns it into the events the screen shows (parse), and saves
them (sync). scripts/publish.py runs sync every 5 minutes on the Pi.

The link is a secret (anyone who has it can read the calendar), so it is never written
to a log or shown on a page. Log lines name the host only.

Google sends times in UTC or tagged with a time zone. parse() converts each one, once,
to local wall-clock time; from there on the app never thinks about time zones
(docs/SPEC.md §2).
"""
import logging
from datetime import datetime, time, timedelta
from typing import NamedTuple

import icalendar
import recurring_ical_events

from . import clock

log = logging.getLogger(__name__)

MAX_EVENTS = 500   # a Pi with 2 GB of memory should never be handed a runaway calendar
MAX_TEXT = 200     # characters kept of a title or a location
NO_TITLE = '(No title)'  # what Google Calendar itself shows


class CalendarError(Exception):
    """The calendar could not be read. The message is shown on the admin Status page."""


class Feed(NamedTuple):
    name: str      # the calendar's name, e.g. 'Formula SAE'
    events: list   # dicts: uid, title, start_at, end_at, all_day, location
    skipped: int   # events that could not be understood and were left out


def one_line(value):
    """Text from the feed as one line of at most MAX_TEXT characters."""
    return ' '.join(str(value or '').split())[:MAX_TEXT]


def local_wall_clock(moment, timezone):
    """A time from the feed as local wall-clock time with no zone attached. A time that
    came without a zone ("floating") already is one."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone).replace(tzinfo=None)


def to_event(component, timezone):
    """One occurrence from the feed as a row for the events table (docs/SPEC.md §2)."""
    start = component['DTSTART'].dt
    end = component['DTEND'].dt if 'DTEND' in component else None
    all_day = not isinstance(start, datetime)  # all-day events carry dates, not times
    if all_day:
        # The feed's end date is the day AFTER the last day; we store the last day itself.
        last_day = end - timedelta(days=1) if end else start
        start_at = datetime.combine(start, time())
        end_at = datetime.combine(last_day, time()) if last_day > start else None
    else:
        start_at = local_wall_clock(start, timezone)
        end_at = local_wall_clock(end, timezone) if end else None
        if end_at is not None and end_at <= start_at:
            end_at = None  # no real end: the event leaves the screen when it starts
    return {
        'uid': str(component.get('UID', '')),
        'title': one_line(component.get('SUMMARY')) or NO_TITLE,
        'start_at': clock.minute(start_at),
        'end_at': clock.minute(end_at) if end_at else None,
        'all_day': all_day,
        'location': one_line(component.get('LOCATION')),
    }


def parse(data, window_start, window_end, timezone=None):
    """Turn the feed's bytes into a Feed holding every event between window_start and
    window_end (local wall-clock datetimes). Repeating events come out as one event per
    occurrence. timezone is where the screen is; None means this computer's own zone.
    Raises CalendarError if the bytes aren't a calendar."""
    try:
        calendar = icalendar.Calendar.from_ical(data)
        occurrences = recurring_ical_events.of(calendar).between(
            window_start.replace(tzinfo=timezone).astimezone(timezone),
            window_end.replace(tzinfo=timezone).astimezone(timezone))
    except Exception as error:  # the libraries raise many kinds of error for a bad file
        raise CalendarError('That link did not return a calendar. Copy the secret iCal address '
                            'from Google Calendar again.') from error

    events, skipped = [], 0
    for component in occurrences:
        if str(component.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        try:
            events.append(to_event(component, timezone))
        except Exception as error:  # one odd event must not hide all the others
            skipped += 1
            log.warning('Skipped a calendar event that could not be understood: %s', error)
    events.sort(key=lambda event: (event['start_at'], event['title'], event['uid']))
    return Feed(one_line(calendar.get('X-WR-CALNAME')), events[:MAX_EVENTS], skipped)
```

- [x] **Step 4:** Run `tests/test_calendar_feed.py`. Expected: all pass. If `window_start.replace(tzinfo=None).astimezone(None)` misbehaves for `timezone=None`, the test in Task 5 (`sync` with no zone) will show it.

---

### Task 3: `fetch` — getting the bytes

**Files:** modify `app/calendar_feed.py`, `tests/test_calendar_feed.py`

- [x] **Step 1: Add the failing tests**

```python
import functools
import threading
import time as real_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FeedHandler(BaseHTTPRequestHandler):
    """Answers like Google would, or misbehaves, depending on the path."""

    def do_GET(self):
        if self.path.startswith('/missing'):
            self.send_error(404)
            return
        if self.path.startswith('/slow'):
            real_time.sleep(1.5)
        body = b'x' * (calendar_feed.MAX_BYTES + 10) if self.path.startswith('/huge') else SAMPLE
        self.send_response(200)
        self.send_header('Content-Type', 'text/calendar')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass  # the client gave up (the timeout and too-big tests)

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def feed_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FeedHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def test_fetch_reads_the_feed_over_the_network(feed_server):
    assert calendar_feed.fetch(feed_server + '/private-abc123/basic.ics') == SAMPLE


def test_fetch_reads_a_local_file(tmp_path):
    (tmp_path / 'demo.ics').write_bytes(SAMPLE)

    assert calendar_feed.fetch(str(tmp_path / 'demo.ics')) == SAMPLE


@pytest.mark.parametrize('path, message', [
    ('/missing/basic.ics', 'error 404'),
    ('/huge/basic.ics', 'too big'),
    ('/slow/basic.ics', 'Could not reach the calendar'),
])
def test_fetch_failures_are_calendar_errors(feed_server, monkeypatch, path, message):
    monkeypatch.setattr(calendar_feed, 'TIMEOUT_S', 0.5)

    with pytest.raises(calendar_feed.CalendarError, match=message):
        calendar_feed.fetch(feed_server + path)


def test_fetch_failure_when_nothing_is_listening():
    with pytest.raises(calendar_feed.CalendarError, match='Could not reach the calendar'):
        calendar_feed.fetch('http://127.0.0.1:9/private-abc123/basic.ics')


def test_fetch_failure_for_a_missing_file(tmp_path):
    with pytest.raises(calendar_feed.CalendarError, match='Could not reach the calendar'):
        calendar_feed.fetch(str(tmp_path / 'nope.ics'))


@pytest.mark.parametrize('link', ['http://calendar.google.com/calendar/ical/x/private-abc123/basic.ics',
                                  'ftp://example.com/basic.ics'])
def test_fetch_refuses_links_that_are_not_https(link):
    with pytest.raises(calendar_feed.CalendarError, match='https://'):
        calendar_feed.fetch(link)


def test_error_messages_and_log_names_never_contain_the_secret_link(feed_server):
    secret = feed_server + '/missing/private-abc123/basic.ics'

    with pytest.raises(calendar_feed.CalendarError) as error:
        calendar_feed.fetch(secret)

    assert 'private-abc123' not in str(error.value)
    assert calendar_feed.describe(secret) == '127.0.0.1'
    assert calendar_feed.describe('https://calendar.google.com/calendar/ical/x/private-abc/basic.ics') == 'calendar.google.com'
    assert calendar_feed.describe('/srv/display/data/demo-calendar.ics') == 'demo-calendar.ics'
```

- [x] **Step 2:** Run. Expected: FAIL, `module 'app.calendar_feed' has no attribute 'fetch'`.

- [x] **Step 3: Add to `app/calendar_feed.py`** (imports at the top: `http.client`, `urllib.error`, `urllib.request`, `from pathlib import Path`, `from urllib.parse import urlsplit`)

```python
TIMEOUT_S = 20                  # campus Wi-Fi can hang; give up and try again in 5 minutes
MAX_BYTES = 5 * 1024 * 1024     # a team calendar is a few hundred kB
THIS_COMPUTER = ('127.0.0.1', 'localhost', '::1')


def describe(source):
    """The source without its secret part, for log lines: the host, or the file's name."""
    parts = urlsplit(source)
    return parts.hostname if parts.scheme in ('http', 'https') else Path(source).name


def fetch(source):
    """The feed's bytes. source is the CALENDAR_ICS_URL setting: an https:// link, or the
    path of a .ics file on this computer (the demo uses one). Raises CalendarError."""
    parts = urlsplit(source)
    over_network = '://' in source
    # Plain http would send the secret link unencrypted. (This computer itself is
    # allowed, which is how the tests serve a feed.)
    if over_network and not (parts.scheme == 'https' or
                             (parts.scheme == 'http' and parts.hostname in THIS_COMPUTER)):
        raise CalendarError('The calendar link must start with https://')
    try:
        if over_network:
            request = urllib.request.Request(source, headers={'User-Agent': 'team-display'})
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                data = response.read(MAX_BYTES + 1)
        else:
            with open(source, 'rb') as file:
                data = file.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise CalendarError(f'The calendar link answered with error {error.code}. Copy the secret iCal '
                            'address from Google Calendar again.') from None
    except (OSError, http.client.HTTPException) as error:  # no network, timeout, no such file
        reason = getattr(error, 'reason', None) or error.strerror or type(error).__name__
        raise CalendarError(f'Could not reach the calendar ({reason}).') from None
    if len(data) > MAX_BYTES:
        raise CalendarError('The calendar file is too big (over 5 MB).')
    return data
```

`from None` keeps the original exception (whose text can contain the link) out of tracebacks.

- [x] **Step 4:** Run `tests/test_calendar_feed.py`. Expected: all pass.

---

### Task 4: Database shape

**Files:** modify `app/schema.sql`, `app/db.py`, `app/models.py`, `tests/test_models.py`, `tests/test_smoke.py`, `tests/test_db.py`

- [x] **Step 1: Write the failing tests.** In `tests/test_models.py` delete every test that creates, updates or deletes events (`test_create_single_event...` through `test_events_since...`, and `event_fields`), keep the settings and photo tests, and add:

```python
EVENTS = [
    {'uid': 'a@t', 'title': 'Weekly Meeting', 'start_at': '2026-09-24T18:30', 'end_at': '2026-09-24T20:00',
     'all_day': False, 'location': 'Team Room'},
    {'uid': 'b@t', 'title': 'Career fair', 'start_at': '2026-09-29T00:00', 'end_at': None,
     'all_day': True, 'location': ''},
]


def stored(conn):
    return [dict(row) for row in conn.execute(
        'SELECT uid, title, start_at, end_at, all_day, location FROM events ORDER BY id')]


def test_replace_events_stores_the_calendar(conn):
    assert models.replace_events(conn, EVENTS) is True

    assert stored(conn) == [dict(EVENTS[0], all_day=0), dict(EVENTS[1], all_day=1)]
    assert models.count_events(conn) == 2


def test_replace_events_writes_nothing_when_nothing_changed(conn):
    models.replace_events(conn, EVENTS)
    ids_before = [row['id'] for row in conn.execute('SELECT id FROM events ORDER BY id')]
    changes_before = conn.total_changes

    assert models.replace_events(conn, EVENTS) is False

    assert conn.total_changes == changes_before  # kind to the SD card
    assert [row['id'] for row in conn.execute('SELECT id FROM events ORDER BY id')] == ids_before


def test_replace_events_drops_events_that_left_the_calendar(conn):
    models.replace_events(conn, EVENTS)

    assert models.replace_events(conn, EVENTS[1:]) is True

    assert [event['title'] for event in stored(conn)] == ['Career fair']


def test_events_since_keeps_anything_ending_on_or_after_the_cutoff_sorted_by_start(conn):
    models.replace_events(conn, [
        {'uid': '1', 'title': 'long gone', 'start_at': '2026-09-01T09:00', 'end_at': '2026-09-01T10:00', 'all_day': False, 'location': ''},
        {'uid': '2', 'title': 'multi-day, still going', 'start_at': '2026-09-18T09:00', 'end_at': '2026-09-25T09:00', 'all_day': False, 'location': ''},
        {'uid': '3', 'title': 'yesterday', 'start_at': '2026-09-21T18:00', 'end_at': None, 'all_day': False, 'location': ''},
        {'uid': '4', 'title': 'tomorrow', 'start_at': '2026-09-23T18:00', 'end_at': None, 'all_day': False, 'location': ''},
    ])

    kept = models.events_since(conn, datetime(2026, 9, 21, 0, 0))

    assert [e['title'] for e in kept] == ['multi-day, still going', 'yesterday', 'tomorrow']


def test_calendar_status_starts_empty_and_records_each_check(conn, fake_clock):
    assert models.calendar_status(conn)['synced_at'] is None

    models.record_calendar_check(conn, True, '12 events', 'Formula SAE')
    fake_clock.advance(minutes=5)
    models.record_calendar_check(conn, False, 'Could not reach the calendar (timed out).')

    status = models.calendar_status(conn)
    assert (status['synced_at'], status['checked_at']) == ('2026-09-22T14:10:00', '2026-09-22T14:15:00')
    assert (status['ok'], status['message']) == (0, 'Could not reach the calendar (timed out).')
    assert status['calendar_name'] == 'Formula SAE'  # kept from the last read that worked
```

In `tests/test_smoke.py` change the table check to `{'events', 'photos', 'settings', 'audit_log', 'calendar_sync'} <= tables`. In `tests/test_db.py` add:

```python
def test_an_events_table_from_before_google_calendar_is_replaced(tmp_path):
    # Until 2026-09-29 events were typed into the admin pages; the table had other columns.
    connection = db.connect(tmp_path / 'old.db')
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,"
                       " start_at TEXT NOT NULL, series_id TEXT)")
    connection.execute("INSERT INTO events (title, type, start_at) VALUES ('Old meeting', 'meeting', '2026-09-24T18:30')")
    connection.execute('CREATE TABLE photos (id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE, caption TEXT,'
                       ' enabled INTEGER NOT NULL DEFAULT 1, uploaded_at TEXT NOT NULL)')
    connection.execute("INSERT INTO photos (filename, uploaded_at) VALUES ('keep.jpg', 'x')")

    db.apply_schema(connection)
    db.apply_schema(connection)  # every start runs it again

    columns = [row['name'] for row in connection.execute('PRAGMA table_info(events)')]
    assert columns == ['id', 'uid', 'title', 'start_at', 'end_at', 'all_day', 'location']
    assert connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
    assert connection.execute('SELECT filename FROM photos').fetchone()[0] == 'keep.jpg'
    connection.close()
```

- [x] **Step 2:** Run the three files. Expected: FAIL (`no attribute 'replace_events'`, missing table, old columns).

- [x] **Step 3: `app/schema.sql`** — replace the `events` block and add `calendar_sync`:

```sql
-- A copy of the team's Google Calendar, rebuilt by app/calendar_feed.py. The calendar is
-- the source of truth; nothing here is edited by hand.
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  uid         TEXT NOT NULL,          -- the calendar's id for the event (repeats share one)
  title       TEXT NOT NULL,
  start_at    TEXT NOT NULL,          -- 'YYYY-MM-DDTHH:MM' local wall clock
  end_at      TEXT,                   -- nullable; for all_day: the last day, 'YYYY-MM-DDT00:00'
  all_day     INTEGER NOT NULL DEFAULT 0,
  location    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);

-- How the last reads of the calendar went. Always exactly one row.
CREATE TABLE IF NOT EXISTS calendar_sync (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  checked_at    TEXT,                 -- the last attempt
  synced_at     TEXT,                 -- the last attempt that worked
  ok            INTEGER,
  message       TEXT,                 -- '12 events', or what went wrong
  calendar_name TEXT
);
INSERT OR IGNORE INTO calendar_sync (id) VALUES (1);
```

Remove the `idx_events_series` line.

- [x] **Step 4: `app/db.py`**

```python
def apply_schema(conn):
    """Create any missing tables and default settings. Safe to run on every start."""
    drop_events_table_from_before_google_calendar(conn)
    conn.executescript(SCHEMA.read_text())


def drop_events_table_from_before_google_calendar(conn):
    """Until 2026-09-29 events were typed into the admin pages and the table had other
    columns. Events now come from Google Calendar, so the old rows are not carried over."""
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(events)')}
    if columns and 'uid' not in columns:
        conn.execute('DROP TABLE IF EXISTS events')
```

- [x] **Step 5: `app/models.py`** — delete `EVENT_TYPES`, `_stored_times`, `create_event`, `update_event`, `get_event`, `delete_event`, `delete_series_from`, `series_count`, `event_ends_at`, `split_upcoming_past`, and the `uuid` import. Update the module docstring's last paragraph to say events are a copy of the calendar. Keep `events_since`. Add:

```python
EVENT_COLUMNS = ('uid', 'title', 'start_at', 'end_at', 'all_day', 'location')


def replace_events(conn, events):
    """Make the events table hold exactly `events` (dicts from calendar_feed.parse, in
    order). Writes nothing if it already does, to spare the Pi's SD card. Returns whether
    anything changed. The caller wraps this in db.transaction()."""
    new = [(e['uid'], e['title'], e['start_at'], e['end_at'], int(e['all_day']), e['location']) for e in events]
    old = [tuple(row) for row in conn.execute(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events ORDER BY id")]
    if new == old:
        return False
    conn.execute('DELETE FROM events')
    conn.executemany(f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)", new)
    return True


def count_events(conn):
    return conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def calendar_status(conn):
    """How the last reads of the calendar went (the single calendar_sync row)."""
    return conn.execute('SELECT * FROM calendar_sync WHERE id = 1').fetchone()


def record_calendar_check(conn, ok, message, calendar_name=None):
    """Note a read of the calendar. A failed read keeps the time and the name of the last
    one that worked."""
    now = clock.stamp()
    if ok:
        conn.execute('UPDATE calendar_sync SET checked_at = ?, synced_at = ?, ok = 1, message = ?,'
                     ' calendar_name = ? WHERE id = 1', (now, now, message, calendar_name))
    else:
        conn.execute('UPDATE calendar_sync SET checked_at = ?, ok = 0, message = ? WHERE id = 1', (now, message))
```

- [x] **Step 6:** Run `tests/test_models.py tests/test_smoke.py tests/test_db.py`. Expected: pass. (Other test files still fail until Tasks 5–8.)

---

### Task 5: `sync` — fetch, parse, save

**Files:** modify `app/calendar_feed.py`, `tests/test_calendar_feed.py`

- [x] **Step 1: Add the failing tests**

```python
from app import db, models


@pytest.fixture
def conn(app):
    connection = db.connect(app.config['DISPLAY_DB'])
    yield connection
    connection.close()


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / 'calendar.ics'
    path.write_bytes(SAMPLE)
    return str(path)


def test_sync_saves_the_events_in_the_window_and_the_result(conn, sample_file):
    ok, message = calendar_feed.sync(conn, sample_file, timezone=NEW_YORK)

    assert (ok, message) == (True, '17 events')
    titles = [row['title'] for row in conn.execute('SELECT title FROM events ORDER BY start_at, id')]
    assert titles[:3] == ['Brake bias test', 'Machine shop open hours', 'Suspension design review']
    status = models.calendar_status(conn)
    assert (status['ok'], status['synced_at'], status['calendar_name']) == (1, '2026-09-22T14:10:00', 'Formula SAE')


def test_sync_without_a_zone_uses_this_computers(conn, sample_file):
    ok, _ = calendar_feed.sync(conn, sample_file)

    assert ok and models.count_events(conn) == 17


def test_a_failed_read_keeps_the_saved_events_and_says_why(conn, sample_file, tmp_path, fake_clock, caplog):
    calendar_feed.sync(conn, sample_file, timezone=NEW_YORK)
    fake_clock.advance(minutes=5)

    ok, message = calendar_feed.sync(conn, str(tmp_path / 'private-abc123.ics'), timezone=NEW_YORK)

    assert ok is False and 'Could not reach the calendar' in message
    assert models.count_events(conn) == 17
    status = models.calendar_status(conn)
    assert (status['ok'], status['synced_at'], status['checked_at']) == (0, '2026-09-22T14:10:00', '2026-09-22T14:15:00')
    assert 'private-abc123.ics' in caplog.text  # a file name is not a secret...


def test_the_log_never_holds_the_secret_link(conn, caplog):
    calendar_feed.sync(conn, 'http://127.0.0.1:9/calendar/ical/x/private-abc123/basic.ics')

    assert 'private-abc123' not in caplog.text  # ...but the link is
    assert '127.0.0.1' in caplog.text


def test_no_link_set_is_reported_not_raised(conn):
    ok, message = calendar_feed.sync(conn, None)

    assert ok is False and 'No calendar link is set' in message
    assert models.calendar_status(conn)['message'] == message


def test_skipped_events_are_counted_in_the_message(conn, tmp_path, monkeypatch):
    real = calendar_feed.to_event

    def picky(component, timezone):
        if str(component.get('SUMMARY')) == 'Team BBQ':
            raise ValueError('odd event')
        return real(component, timezone)
    monkeypatch.setattr(calendar_feed, 'to_event', picky)
    (tmp_path / 'c.ics').write_bytes(SAMPLE)

    ok, message = calendar_feed.sync(conn, str(tmp_path / 'c.ics'), timezone=NEW_YORK)

    assert (ok, message) == (True, '16 events (1 could not be read and was left out)')
```

(17 = brake, shop, review, 7 weekly, 1 moved, report, fair, testing, BBQ, sponsor, untitled.)

- [x] **Step 2:** Run. Expected: FAIL, no attribute `sync`.

- [x] **Step 3: Add to `app/calendar_feed.py`** (import `db` and `models` from the package)

```python
DAYS_AHEAD = 60    # the screen lists 30 days; the rest is headroom


def sync(conn, source, timezone=None):
    """Read the calendar and save its events. Returns (ok, message); the message is for
    the admin Status page. A read that fails changes nothing but the status row, so the
    screen keeps the events it had."""
    if not source:
        return _record(conn, False, 'No calendar link is set. Add CALENDAR_ICS_URL to /etc/display/env '
                                    '(docs/SETUP.md).')
    window_start = datetime.combine(clock.now().date() - timedelta(days=1), time())  # yesterday 00:00
    window_end = window_start + timedelta(days=DAYS_AHEAD + 1)
    try:
        feed = parse(fetch(source), window_start, window_end, timezone)
    except CalendarError as error:
        log.warning('Could not read the calendar at %s: %s', describe(source), error)
        return _record(conn, False, str(error))

    message = f'{len(feed.events)} events'
    if feed.skipped:
        message += f' ({feed.skipped} could not be read and {"was" if feed.skipped == 1 else "were"} left out)'
    with db.transaction(conn):
        models.replace_events(conn, feed.events)
        models.record_calendar_check(conn, True, message, feed.name)
    return True, message


def _record(conn, ok, message):
    with db.transaction(conn):
        models.record_calendar_check(conn, ok, message)
    return ok, message
```

- [x] **Step 4:** Run `tests/test_calendar_feed.py`. Expected: pass.

---

### Task 6: Publishing and the JSON contract

**Files:** modify `app/publish.py`, `fixtures/events.schema.json`, `fixtures/events_normal.json`, `fixtures/events_40.json`, `fixtures/events_empty.json`, `fixtures/events_malformed.json`, `app/__init__.py`, `scripts/publish.py`, `scripts/init_db.py`, `tests/test_publish.py`, `tests/test_schema.py`, `tests/test_scripts.py`, `tests/test_config.py`

- [x] **Step 1: Tests.** In `tests/test_publish.py` replace `add_event` with a helper that goes through `models.replace_events` (accumulating a list), update `test_events_json_matches_the_contract` to expect `{'title', 'start', 'end', 'all_day', 'location'}` events and `synced_at`, and add:

```python
def test_synced_at_is_null_until_the_calendar_has_been_read(conn, web_dir):
    publish.publish(conn, web_dir, time_synced=True)
    assert read(web_dir, 'events.json')['synced_at'] is None

    models.record_calendar_check(conn, True, '0 events', 'Formula SAE')
    publish.publish(conn, web_dir, time_synced=True)
    assert read(web_dir, 'events.json')['synced_at'] == '2026-09-22T14:10:00'
```

In `tests/test_schema.py` update the hand-built event in `test_schema_rejects_times...` to the new shape, and add a test that an event with a `type` key is rejected (`additionalProperties`). In `tests/test_scripts.py` add:

```python
def test_publish_script_reads_the_calendar_first(paths, tmp_path):
    result = run('publish.py', env=dict(paths, CALENDAR_ICS_URL=str(FIXTURES / 'calendar_sample.ics')))

    assert result.returncode == 0, result.stderr
    events = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    assert events['synced_at'] is not None
    assert len(events['events']) > 0  # the sample is dated 2026; this holds until late 2026


def test_publish_script_still_publishes_when_the_calendar_cannot_be_read(paths, tmp_path):
    result = run('publish.py', env=dict(paths, CALENDAR_ICS_URL=str(tmp_path / 'private-abc123.ics')))

    assert result.returncode == 0
    assert (tmp_path / 'web' / 'data' / 'events.json').exists()
    assert 'Could not read the calendar' in result.stderr
```

(The first of these depends on today's date; write it with a generated `.ics` dated relative to today instead of the fixture.) In `tests/test_config.py` add `CALENDAR_ICS_URL` to the env-file test and the defaults test (`None` when unset).

- [x] **Step 2:** Run. Expected: FAIL.

- [x] **Step 3: `fixtures/events.schema.json`** — top level `required: ["generated_at", "synced_at", "time_synced", "events"]`; add

```json
"synced_at": {
  "description": "When the Google Calendar was last read successfully, or null if never. The display warns when this is over 60 minutes old.",
  "oneOf": [{ "$ref": "#/$defs/localSecond" }, { "type": "null" }]
}
```

event: `required: ["title", "start", "end", "all_day", "location"]`, remove the `id`, `type`, `notes` properties.

- [x] **Step 4: Fixtures.** Convert the three good fixtures with a one-off script: drop `id`, `type`, `notes` from each event; add `"synced_at"` equal to `generated_at`. In `events_malformed.json` drop nothing (it must stay wrong) but keep it wrong under the new schema too (it has bad `generated_at` and `start`, so it is).

- [x] **Step 5: `app/publish.py`** — `build_events` becomes:

```python
def build_events(conn, now, time_synced):
    since = datetime.combine(now.date() - timedelta(days=1), time())  # yesterday 00:00
    return {
        'generated_at': clock.stamp(now),
        'synced_at': models.calendar_status(conn)['synced_at'],
        'time_synced': bool(time_synced),
        'events': [{
            'title': e['title'],
            'start': e['start_at'],
            'end': e['end_at'],
            'all_day': bool(e['all_day']),
            'location': e['location'] or '',
        } for e in models.events_since(conn, since)],
    }
```

Update the module docstring: publish runs after every change in the admin pages, when the admin service starts, and every 5 minutes after the calendar is read.

- [x] **Step 6: `app/__init__.py`** — `CONFIG_KEYS = ('SECRET_KEY', 'ADMIN_PASSWORD_HASH', 'DISPLAY_DB', 'DISPLAY_WEB', 'CALENDAR_ICS_URL')`.

- [x] **Step 7: `scripts/publish.py`**

```python
"""Read the team's Google Calendar, then regenerate web/data/events.json, photos.json and
settings.json.

    python scripts/publish.py

Runs when the admin service starts and every 5 minutes (display-publish.timer). If the
calendar can't be read (no network, say), the files are still rewritten from the events
saved last time, and the reason is logged and shown on the admin Status page. Exits with
status 1 only if the files could not be written, so that failure shows up in
`systemctl status display-publish` and `journalctl -u display-publish`.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app` works

from app import calendar_feed, create_app, db, publish  # noqa: E402

log = logging.getLogger('publish')


def main():
    app = create_app()
    with app.app_context():
        conn = db.get_db()
        try:
            calendar_feed.sync(conn, app.config['CALENDAR_ICS_URL'])
        except Exception:  # e.g. the disk is full; still try to publish what we have
            log.exception('Could not save the calendar')
        try:
            publish.publish(conn, app.config['DISPLAY_WEB'], time_synced=app.config['TIME_SYNCED'])
        except publish.PublishError:
            return 1  # the details were already logged
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

`scripts/init_db.py`: after creating the app, call `calendar_feed.sync` the same way and print `Calendar: <message>` before the two existing lines.

- [x] **Step 8:** Run `tests/test_publish.py tests/test_schema.py tests/test_scripts.py tests/test_config.py`. Expected: pass except the `seed_demo` tests (Task 9).

---

### Task 7: Admin pages

**Files:** modify `app/admin/routes.py`, `app/forms.py`, `app/auth.py`, `app/admin/templates/base.html`, `message.html`, `status.html`, `app/admin/static/admin.js`, `app/admin/static/admin.css`; delete `app/admin/filters.py`, `app/admin/templates/events.html`, `app/admin/templates/event_form.html`; modify `tests/test_admin.py`, `tests/test_status.py`, `tests/test_auth.py`, `tests/test_admin_browser.py`, `tests/helpers.py`

- [x] **Step 1: Tests.**
  - `tests/test_admin.py`: delete every event and series test. Keep CSRF (rewrite against `/admin/settings`), settings, headers. The publish-failure test posts settings instead of an event. The concurrency test posts two different banner texts to `/admin/settings` and asserts both requests return 302 and the database passes `integrity_check`. The hostile-text test posts `<script>alert(1)</script>` as `banner_text` and checks it is escaped on `/admin/settings` and verbatim in `settings.json`. Add:

```python
def test_admin_home_goes_to_the_photos_page(admin):
    response = admin.get('/admin')

    assert response.status_code == 302 and response.headers['Location'] == '/admin/photos'


@pytest.mark.parametrize('page', ['/admin/events/new', '/admin/events/1/edit', '/admin/events/1/duplicate'])
def test_event_pages_are_gone(admin, page):
    assert admin.get(page).status_code == 404


def test_navigation_has_no_events_tab(admin):
    html = admin.get('/admin/photos').get_data(as_text=True)

    assert '>Events<' not in html and '>Photos<' in html and '>Status<' in html
```

  - `tests/test_status.py`: add

```python
def test_status_shows_the_calendar(admin, commands, conn, app):
    app.config['CALENDAR_ICS_URL'] = str(FIXTURES / 'calendar_sample.ics')
    admin.post('/admin/status/read-calendar')

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Formula SAE' in html and '17 events' in html and '2026-09-22 14:10' in html
    assert conn.execute('SELECT action FROM audit_log ORDER BY id DESC').fetchone()[0] == 'calendar.read'


def test_read_calendar_now_republishes(admin, commands, app, web_dir):
    app.config['CALENDAR_ICS_URL'] = str(FIXTURES / 'calendar_sample.ics')

    response = admin.post('/admin/status/read-calendar')

    assert response.status_code == 302
    published = json.loads((web_dir / 'data' / 'events.json').read_text())
    assert published['synced_at'] == '2026-09-22T14:10:00' and len(published['events']) == 17


def test_a_calendar_that_cannot_be_read_is_obvious(admin, commands, app, tmp_path):
    app.config['CALENDAR_ICS_URL'] = str(tmp_path / 'private-abc123.ics')
    admin.post('/admin/status/read-calendar')

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Could not reach the calendar' in html
    assert 'private-abc123' not in html  # the link is a secret, even from admins


def test_status_without_a_calendar_link(admin, commands):
    assert 'No calendar link is set' in admin.get('/admin/status').get_data(as_text=True)
```

  - `tests/test_auth.py`: `is_logged_in` becomes `client.get('/admin/photos').status_code == 200`; `ADMIN_PAGES` drops `/admin/events/new`.
  - `tests/test_admin_browser.py`: delete the two all-day tests; `page.wait_for_url(live_app + '/admin/photos')`; add one browser test that the Status page's **Read calendar now** button asks nothing and shows the result flash.

- [x] **Step 2:** Run. Expected: FAIL.

- [x] **Step 3: `app/admin/routes.py`.** Delete the Events section, `describe`, `form_status` stays (settings uses it), the `filters` import and registration, `EventForm`, `clock`, `timedelta`, `groupby`. Add:

```python
from .. import calendar_feed, db, models, publish, wants_json


@bp.route('', strict_slashes=False)
def home():
    """Events come from Google Calendar, so photos are what people come here to change."""
    return redirect(url_for('admin.photos'))


@bp.route('/status')
def status():
    conn = db.get_db()
    return render_template('status.html', info=pi_status.gather(current_app.config),
                           calendar=models.calendar_status(conn), event_count=models.count_events(conn),
                           audit=models.recent_audit(conn, limit=50))


@bp.post('/status/read-calendar')
def read_calendar():
    """Read the Google Calendar now instead of waiting up to 5 minutes for the timer."""
    conn = db.get_db()
    ok, message = calendar_feed.sync(conn, current_app.config['CALENDAR_ICS_URL'])
    with db.transaction(conn):
        models.audit(conn, who(), 'calendar.read', detail=message[:200])
    publish_now()
    if ok:
        flash(f'Calendar read: {message}. The screen picks them up within a minute.', 'success')
    else:
        flash(f'The calendar could not be read. {message}', 'error')
    return redirect(url_for('admin.status'))
```

Update the module docstring (three-step write pattern still holds; events are not written here).

- [x] **Step 4:** `app/forms.py`: delete `EventForm`, `DATETIME_FORMATS`, the `EVENT_TYPES` import and the unused field imports. `app/auth.py` and `message.html`: `url_for('admin.events')` → `url_for('admin.home')`. `base.html`: remove the Events link; Photos is current for `section in ('photos', 'home')`.

- [x] **Step 5: `status.html`** — first row of the list:

```html
  <dt>Calendar</dt>
  <dd>
    {% if calendar.ok %}
      {{ state_pill(true, 'OK') }} {{ calendar.calendar_name or 'Google Calendar' }}: {{ event_count }} events,
      last read {{ calendar.synced_at[:16] | replace('T', ' ') }}
    {% else %}
      {{ state_pill(false, 'Not read') }} {{ calendar.message or 'The calendar has not been read yet.' }}
      {% if calendar.synced_at %}
        Showing the {{ event_count }} events from {{ calendar.synced_at[:16] | replace('T', ' ') }}.
      {% endif %}
    {% endif %}
  </dd>
```

and a first button in `.actions`:

```html
  <form method="post" action="{{ url_for('admin.read_calendar') }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button type="submit" class="button primary">Read calendar now</button>
  </form>
```

- [x] **Step 6:** `admin.js`: delete the "All day" block. `admin.css`: delete `.chips`, `.chip*`, `.day*`, `.event-*`, `.series`, `.badge*`, `.past*`, `.field-row`, the five type color variables. Delete the three files listed above.

- [x] **Step 7:** Run `tests/test_admin.py tests/test_status.py tests/test_auth.py tests/test_admin_browser.py tests/test_photos.py`. Expected: pass.

---

### Task 8: The screen

**Files:** modify `web/index.html`, `web/app.js`, `web/style.css`, `tests/test_display.py`

- [x] **Step 1: Tests** (`tests/test_display.py`). Rows now have three cells. Update `test_normal_schedule_at_a_glance` (`rows[0] == ['Today', '4 – 5 PM', 'Suspension design review · Team Room']`, drop the `Due` line), `by_title` users, the `event()` helper (no `id`, `type`, `notes`), the XSS test. Add:

```python
def test_there_are_no_type_badges(screen):
    screen.open()

    assert screen.page.query_selector_all('.badge') == []
    assert all(len(row) == 3 for row in screen.rows())


def calendar_read(minutes_ago):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    document['synced_at'] = None if minutes_ago is None else \
        (FIXED.replace(tzinfo=None) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S')
    return document


@pytest.mark.parametrize('minutes_ago, warned', [(5, False), (59, False), (61, True), (None, True)])
def test_warns_when_the_calendar_has_not_been_read_for_an_hour(screen, minutes_ago, warned):
    # The Pi keeps rewriting the files from its saved events while Google is unreachable,
    # so fresh files alone don't prove a fresh schedule.
    screen.open()
    screen.site.write('events.json', calendar_read(minutes_ago))
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('hero-title').textContent !== 'Loading the schedule…'")

    assert screen.shows('stale-badge') is warned
    if warned:
        assert screen.text('stale-badge') == 'Schedule may be out of date'
        assert screen.page.get_attribute('#status-dot', 'data-state') == 'stale'


def test_events_json_without_synced_at_is_refused(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    del document['synced_at']
    screen.open()

    screen.site.write('events.json', document)
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('status-dot').dataset.state === 'offline'")
```

(`screen.advance(seconds=61)` moves the page clock one minute, so use 58 and 61 around the limit if 59 proves flaky.)

- [x] **Step 2:** Run. Expected: FAIL.

- [x] **Step 3: `web/index.html`** — remove `<span class="badge" id="hero-badge" hidden></span>` and `<p class="hero-notes" id="hero-notes" hidden></p>`.

- [x] **Step 4: `web/app.js`.**
  - Delete `TYPE_LABELS`, `setBadge`, every `hero-badge` and `hero-notes` line, and the badge in `agendaRow` (the row appends day, time, title).
  - `state` gains `syncedAt: null, // Date the Pi last read the Google Calendar, or null`.
  - `readEvent`: drop the `type` check and `notes`; `ev = { title, start, end, allDay, location }`.
  - `readEvents`:

```js
function readEvents(data) {
  const generatedAt = isObject(data) ? parseLocal(data.generated_at) : null;
  const syncedAt = generatedAt && data.synced_at !== null ? parseLocal(data.synced_at) : null;
  if (!generatedAt || (data.synced_at !== null && !syncedAt) || typeof data.time_synced !== 'boolean'
      || !Array.isArray(data.events)) {
    throw new Error('events.json is not in the expected format');
  }
  const events = data.events.map(readEvent).sort((a, b) => a.start - b.start);
  return () => {
    Object.assign(state, { events, generatedAt, syncedAt, timeSynced: data.time_synced, loaded: true });
  };
}
```

  - `renderStatus`:

```js
  // Old data: the Pi has stopped writing the files, or it keeps writing them but hasn't
  // been able to read the Google Calendar (SPEC §7).
  const minutesSince = (moment) => (t - moment) / 60000;
  const old = state.loaded && (minutesSince(state.generatedAt) > STALE_AFTER_MINUTES
    || !state.syncedAt || minutesSince(state.syncedAt) > STALE_AFTER_MINUTES);
```

  - Update the file's header comment: the JSON files are written by the Pi from the team's Google Calendar and the admin pages.

- [x] **Step 5: `web/style.css`** — delete the five `--meeting … --other` variables and the whole "Type badges" section; `.agenda` and `.agenda-row` use `grid-template-columns: max-content max-content minmax(0, 1fr)`; `.hero-where` keeps its rule alone (remove `.hero-notes` from the selector).

- [x] **Step 6:** Run `tests/test_display.py`. Expected: pass, including the overflow tests at all three sizes.

---

### Task 9: The Pi, the demo, the repo guard

**Files:** modify `pi/systemd/display-publish.timer`, `pi/systemd/display-publish.service`, `pi/env.example`, `scripts/seed_demo.py`, `tests/test_pi_files.py`, `tests/test_scripts.py`, `tests/test_repo.py`

- [x] **Step 1: Tests.**

```python
# tests/test_pi_files.py (replaces test_publish_runs_often_enough_for_the_stale_badge)
def test_the_calendar_is_read_every_five_minutes_once_the_network_is_up():
    timer, service = unit('display-publish.timer'), unit('display-publish.service')

    assert timer['Timer']['OnCalendar'] == '*:0/5'
    assert service['Unit']['After'] == 'network-online.target'
    assert service['Unit']['Wants'] == 'network-online.target'
    assert service['Service']['TimeoutStartSec'] == '120'  # a hung read must not block the next one


def test_env_example_shows_where_the_calendar_link_goes_without_holding_one():
    text = (PI / 'env.example').read_text()

    assert '# CALENDAR_ICS_URL=' in text
    assert 'private-' not in text.replace('private-XXXX', '')
```

```python
# tests/test_repo.py: add to SECRET_VALUE_PATTERNS
    (re.compile(r'calendar\.google\.com/calendar/ical/[^/\s]+/private-[0-9a-f]{16,}'), 'secret calendar link'),
```

  `tests/test_scripts.py`: replace the two `seed_demo` tests:

```python
def test_seed_demo_makes_a_demo_calendar_and_photos(paths, tmp_path):
    result = run('seed_demo.py', env=paths)

    assert result.returncode == 0, result.stderr
    published = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    photos = json.loads((tmp_path / 'web' / 'data' / 'photos.json').read_text())['photos']
    assert len(published['events']) >= 8 and published['synced_at']
    assert len(photos) == 4
    assert (tmp_path / 'data' / 'demo-calendar.ics').exists()
    assert 'CALENDAR_ICS_URL=' in result.stdout  # tells you how to keep using it


def test_seed_demo_can_run_again_without_adding_more_photos(paths, tmp_path):
    run('seed_demo.py', env=paths)

    assert run('seed_demo.py', env=paths).returncode == 0
    assert len(json.loads((tmp_path / 'web' / 'data' / 'photos.json').read_text())['photos']) == 4


def test_seed_demo_refuses_when_a_real_calendar_is_set(paths):
    result = run('seed_demo.py', env=dict(paths, CALENDAR_ICS_URL='https://calendar.google.com/calendar/ical/x/basic.ics'))

    assert result.returncode != 0
    assert 'already reads a calendar' in result.stderr
```

- [x] **Step 2:** Run. Expected: FAIL.

- [x] **Step 3: `pi/systemd/display-publish.timer`**

```ini
# Every 5 minutes: read the team's Google Calendar and rewrite the screen's files, even
# when nothing changed. The file's timestamp is how the screen knows its data is fresh
# (it warns after 60 minutes), it picks up "clock synced" soon after boot, and old events
# roll off after midnight. See docs/SPEC.md §11.
[Unit]
Description=Team display: read the calendar and rewrite the screen's data files every 5 minutes

[Timer]
OnBootSec=1min
OnCalendar=*:0/5

[Install]
WantedBy=timers.target
```

`display-publish.service`: `[Unit]` gains `After=network-online.target` and `Wants=network-online.target`; `[Service]` gains `TimeoutStartSec=120`. Comment: without a network the read fails within 20 s and the files are written from the saved events.

- [x] **Step 4: `pi/env.example`** — add after `DISPLAY_WEB`:

```
# The team calendar. In Google Calendar: Settings → the team's calendar → Integrate
# calendar → "Secret address in iCal format". Anyone with this link can read the
# calendar, so it goes here and nowhere else. Remove the # and paste the real link:
# CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/XXXX/private-XXXX/basic.ics
```

- [x] **Step 5: `scripts/seed_demo.py`** — rewrite: build a calendar with `icalendar` (events relative to today: a weekly Thursday meeting with `RRULE` for 8 weeks, a Monday subteam meeting for 4 weeks, shop hours today 13:00–17:00, a deadline Friday 23:59 with no end, an all-day career fair in 7 days, a 3-day all-day testing weekend, a BBQ, a 2-day mock competition in 25 days), write it to `<data dir>/demo-calendar.ics`, `calendar_feed.sync` from it, add the sample photos only if the photos table is empty, set the competition name and date, publish, and print:

```
Demo data added to <db>
To keep reading the demo calendar, add this line to .env:
CALENDAR_ICS_URL=<path to demo-calendar.ics>
```

Refuse (exit with `This display already reads a calendar (CALENDAR_ICS_URL is set). The demo is for a laptop without one.`) when `CALENDAR_ICS_URL` is set to anything other than that demo file.

- [x] **Step 6:** Run `tests/test_pi_files.py tests/test_scripts.py tests/test_repo.py`. Expected: pass.

---

### Task 10: Docs, pictures, the running demo

**Files:** modify `docs/SPEC.md`, `docs/SETUP.md`, `docs/UPDATING.md`, `docs/TROUBLESHOOTING.md`, `docs/TESTING.md`, `README.md`, `fixtures/README.md`, `docs/decisions/2026-09-29-google-calendar-design.md` (only if the build deviated); replace `docs/mockups/*.png`, `docs/screenshots/*.png`

- [x] **Step 1: SPEC.md.** §1 Next up: "what, when, where" (no badge). §3: remove the notes sentence. §4: row is `day · time · title (location)`; delete the Badges bullet. §7: polling paragraph gains the calendar rule ("every 5 minutes the Pi reads the Google Calendar…"; stale badge also when `synced_at` is null or over 60 minutes old). §9: sources become the Google Calendar fields (`SUMMARY`, `DTSTART`/`DTEND`, `LOCATION`). §10: replace the recurrence walkthrough with "Repeating events are made in Google Calendar; the Pi expands them". §11: mark decisions 3 (form hint), 5 (15 minutes → 5) as updated and add 31–34: events come from Google Calendar; one time-zone conversion at the boundary; the link is a secret in the env file only; `http://` is refused except to the Pi itself.
- [x] **Step 2: SETUP.md.** Step 5 gains the calendar link (`sudoedit /etc/display/env`, paste the `CALENDAR_ICS_URL=` line, how to find the link, what to do if the university account has no secret address). Step 6 shows the expected `Calendar: N events` line. Step 8 says the timer reads the calendar every 5 minutes. "Updating the code later" adds `pip install -r requirements.txt` as required for this change.
- [x] **Step 3: UPDATING.md.** Rewrite the events sections: events are added in Google Calendar by anyone the team leads share it with; they reach the screen within about 5 minutes; what the screen uses (title, time, location) and ignores (description, guests, colors); repeating events work; to hide something, delete or cancel it in the calendar. Photos, Settings and "Reading the bottom of the screen" stay; the amber badge text gains "or the Pi can't reach Google". Remove the three event screenshots.
- [x] **Step 4: TROUBLESHOOTING.md.** "Where things are" gains the calendar link. New section "Events are missing, wrong, or out of date": Status → Calendar row; **Read calendar now**; `journalctl -u display-publish -n 20`; link reset or changed → new link into `/etc/display/env`, then `sudo systemctl restart display-admin`; an event on the wrong day or hour → `timedatectl` time zone. The amber-badge section points here.
- [x] **Step 5: TESTING.md.** Automated table: replace the 2.3.x rows' wording (events are read from the calendar: `tests/test_calendar_feed.py`). Manual checklist: replace 2.3.12 with "Add an event in Google Calendar → on screen within 6 minutes"; add "Unplug the network for 70 minutes → events stay, amber badge; plug back in → badge clears within 5 minutes"; add "Reset the secret link in Google Calendar → Status shows the error; new link fixes it".
- [x] **Step 6: README.md** (diagram and "Try it on your laptop"), `fixtures/README.md` (the `.ics` sample).
- [x] **Step 7:** Re-shoot `docs/mockups/*.png` with the scratch script from 2026-09-29 (same seven states) and the admin screenshots (login, photos, settings, status) at 375 px wide.
- [x] **Step 8:** Run the **whole** suite. Expected: all pass.
- [x] **Step 9:** End-to-end under gunicorn with two workers and a local `.ics`: log in, **Read calendar now**, upload a photo, change a setting, open the display. No console errors, no tracebacks in the gunicorn log.
- [x] **Step 10:** Refresh the local demo: run `scripts/seed_demo.py`, add the printed `CALENDAR_ICS_URL=` line to `.env`, restart `flask run`.

## Self-review

- **Spec coverage:** reading the link (Tasks 2, 3, 5), database (4), publishing (6), screen (8), admin (7), built for the Pi (1: pure Python; 9: timer, network-online, timeout; 4: no needless writes; 3: size and time caps), docs (10), every row of the spec's error table (3, 5, 7), testing (each task).
- **Names used across tasks:** `calendar_feed.fetch/parse/sync/describe/to_event/CalendarError/Feed`, `TIMEOUT_S`, `MAX_BYTES`, `MAX_EVENTS`; `models.replace_events/count_events/calendar_status/record_calendar_check/events_since`; config key `CALENDAR_ICS_URL`; endpoints `admin.home`, `admin.read_calendar`; JSON keys `synced_at`, `title`, `start`, `end`, `all_day`, `location`; page state `syncedAt`.
- **The sample feed yields 17 events** inside the test window; Tasks 5 and 7 rely on that number.
