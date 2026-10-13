"""Reading the team's Google Calendar.

The team leads keep the schedule in Google Calendar. Its settings page has a "Secret
address in iCal format": a private link to the whole calendar as one file. This module
reads that file (fetch), turns it into the events the screen shows (parse), and saves
them (sync). scripts/publish.py runs sync every 30 seconds on the Pi.

The link is a secret (anyone who has it can read the calendar), so it is never written
to a log or shown on a page. Log lines name the host only.

Google sends times in UTC or tagged with a time zone. parse() converts each one, once,
to local wall-clock time; from there on the app never thinks about time zones
(docs/SPEC.md §2).
"""
import http.client
import logging
import urllib.error
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import icalendar
import recurring_ical_events

from . import clock, db, http_get, models

log = logging.getLogger(__name__)

TIMEOUT_S = 20     # for the whole download; campus Wi-Fi can hang, so give up and try next round
MAX_BYTES = 5 * 1024 * 1024  # a team calendar is a few hundred kB
DAYS_AHEAD = 60    # the screen lists the next 30 days; the rest is headroom
MAX_EVENTS = 500   # a Pi with 2 GB of memory should never be handed a runaway calendar
MAX_TEXT = 200     # characters kept of a title or a location
NO_TITLE = '(No title)'  # what Google Calendar itself shows
# A repeat this fast means millions of occurrences in the window before the 500 cap kicks
# in (minutes of work on a Pi). Google's own editor can't make one; an imported file can.
RUNAWAY_FREQUENCIES = {'SECONDLY', 'MINUTELY'}


class CalendarError(Exception):
    """The calendar could not be read. The message is shown on the admin Status page."""


class Feed(NamedTuple):
    name: str      # the calendar's name, e.g. 'Formula SAE'
    events: list   # dicts: uid, title, start_at, end_at, all_day, location
    skipped: int   # events that could not be understood and were left out


class SyncResult(NamedTuple):
    ok: bool       # was the calendar read?
    message: str   # '12 events', or what went wrong (shown on the admin Status page)
    changed: bool  # did the saved events change? (if not, the screen's files needn't be rewritten)


# --- fetch: the link → the feed's bytes -----------------------------------------------

def describe(source):
    """The source without its secret part, for log lines: the host, or the file's name."""
    parts = urlsplit(source)
    return parts.hostname if parts.scheme in ('http', 'https') else Path(source).name


def fetch(source):
    """The feed's bytes. source is the CALENDAR_ICS_URL setting: an https:// link, or the
    path of a .ics file on this computer (the demo uses one). Raises CalendarError."""
    over_network = '://' in source
    if over_network and not http_get.link_allowed(source):
        raise CalendarError('The calendar link must start with https://')
    try:
        if over_network:
            data = http_get.download(source, max_bytes=MAX_BYTES, timeout_s=TIMEOUT_S)
        else:
            with open(source, 'rb') as file:
                data = file.read(MAX_BYTES + 1)
    # "from None" below: the original errors can carry the link, so they are dropped.
    except http_get.DownloadError as error:
        raise CalendarError(str(error)) from None
    except urllib.error.HTTPError as error:
        raise CalendarError(f'The calendar link answered with error {error.code}. Copy the secret iCal '
                            'address from Google Calendar again.') from None
    except (OSError, http.client.HTTPException) as error:  # no network, timed out, no such file
        reason = getattr(error, 'reason', None) or getattr(error, 'strerror', None) or type(error).__name__
        raise CalendarError(f'Could not reach the calendar ({reason}).') from None
    if len(data) > MAX_BYTES:
        raise CalendarError('The calendar file is too big (over 5 MB).')
    return data


# --- parse: the feed's bytes → events ------------------------------------------------

def one_line(value):
    """Text from the feed as one printable line of at most MAX_TEXT characters. Control
    characters and invisible text-direction marks are dropped: they can't be seen in
    Google Calendar but would garble the screen."""
    text = ' '.join(str(value or '').split())
    return ''.join(ch for ch in text if ch.isprintable())[:MAX_TEXT]


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


def repeats_too_fast(component):
    try:
        frequency = str(component.get('RRULE', {}).get('FREQ', [''])[0]).upper()
    except Exception:  # a rule the library couldn't read: expand() will deal with it
        return False
    return frequency in RUNAWAY_FREQUENCIES


def expand(timezones, components, window_start, window_end):
    """Every occurrence of `components` (VEVENTs) inside the window, repeats expanded."""
    calendar = icalendar.Calendar()
    for component in [*timezones, *components]:
        calendar.add_component(component)
    return recurring_ical_events.of(calendar).between(window_start, window_end)


def parse(data, window_start, window_end, timezone=None):
    """Turn the feed's bytes into a Feed holding every event between window_start and
    window_end (local wall-clock datetimes). Repeating events come out as one event per
    occurrence. timezone is where the screen is; None means this computer's own zone.
    Raises CalendarError if the bytes aren't a calendar at all; a single event the
    libraries can't handle is skipped and counted, the rest are kept."""
    try:
        calendar = icalendar.Calendar.from_ical(data)
    except Exception as error:  # the library raises many kinds of error for a bad file
        raise CalendarError('That link did not return a calendar. Copy the secret iCal address '
                            'from Google Calendar again.') from error
    window = (window_start.replace(tzinfo=timezone).astimezone(timezone),
              window_end.replace(tzinfo=timezone).astimezone(timezone))
    timezones = list(calendar.walk('VTIMEZONE'))
    skipped = 0
    components = []
    for component in calendar.walk('VEVENT'):
        if repeats_too_fast(component):
            skipped += 1
            log.warning('Skipped a calendar event that repeats every second or minute: %s', component.get('SUMMARY'))
        else:
            components.append(component)

    try:
        occurrences = expand(timezones, components, *window)
    except Exception:
        # Something in there the libraries can't handle. Expand each series (the events
        # sharing a UID: a repeat and its moved weeks) on its own, and drop only the bad one.
        occurrences = []
        series = {}
        for component in components:
            series.setdefault(str(component.get('UID', '')), []).append(component)
        for uid, members in series.items():
            try:
                occurrences.extend(expand(timezones, members, *window))
            except Exception as error:
                skipped += 1
                log.warning('Skipped a calendar event that could not be understood (%s): %s', uid, error)

    events = []
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


# --- sync: read the calendar and save it ------------------------------------------------

def sync(conn, source, timezone=None):
    """Read the calendar and save its events. Returns a SyncResult. A read that fails
    changes nothing but the status row, so the screen keeps the events it had."""
    if not source:
        return record_failure(conn, 'No calendar link is set. Add CALENDAR_ICS_URL to /etc/display/env '
                                    '(docs/SETUP.md).')
    window_start = datetime.combine(clock.now().date() - timedelta(days=1), time())  # yesterday 00:00
    window_end = window_start + timedelta(days=DAYS_AHEAD + 1)
    try:
        feed = parse(fetch(source), window_start, window_end, timezone)
    except CalendarError as error:
        log.warning('Could not read the calendar at %s: %s', describe(source), error)
        return record_failure(conn, str(error))

    message = f'{len(feed.events)} events'
    if feed.skipped:
        message += f' ({feed.skipped} could not be read and {"was" if feed.skipped == 1 else "were"} left out)'
    with db.transaction(conn):
        changed = models.replace_events(conn, feed.events)
        models.record_calendar_check(conn, True, message, feed.name)
    return SyncResult(True, message, changed)


def record_failure(conn, message):
    """Note a read that didn't work (the saved events are left alone) and report it."""
    with db.transaction(conn):
        models.record_calendar_check(conn, False, message)
    return SyncResult(False, message, False)
