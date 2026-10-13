"""Reading the Google Calendar feed: parse (the rules), fetch (the network), sync (the database)."""
import threading
import time as real_time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from app import calendar_feed, models
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


# --- parse: the rules (docs/SPEC.md §2) -------------------------------------------------

def test_calendar_name_and_event_shape():
    feed = parse_sample()

    assert feed.name == 'Formula SAE'
    assert feed.skipped == 0
    assert by_title(feed)['Machine shop open hours'] == [{
        'uid': 'shop@t', 'title': 'Machine shop open hours', 'start_at': '2026-09-22T13:00',
        'end_at': '2026-09-22T17:00', 'all_day': False, 'location': 'Machine shop'}]


def test_events_are_sorted_by_start():
    starts = [event['start_at'] for event in parse_sample().events]

    assert starts == sorted(starts) and len(starts) == 17


def test_weekly_event_repeats_at_the_same_wall_clock_time_across_the_clock_change():
    weekly = by_title(parse_sample())['Weekly Meeting']
    days = [event['start_at'][:10] for event in weekly]

    assert days == ['2026-09-24', '2026-10-01', '2026-10-08', '2026-10-15', '2026-11-05', '2026-11-12', '2026-11-19']
    assert {event['start_at'][11:] for event in weekly} == {'18:30'}  # the clocks change on Nov 1
    assert {event['end_at'][11:] for event in weekly} == {'20:00'}


def test_a_skipped_week_is_missing_and_a_moved_week_is_moved():
    feed = by_title(parse_sample())
    every_start = [event['start_at'] for events in feed.values() for event in events]

    assert not any(start.startswith('2026-10-22') for start in every_start)  # skipped in the calendar
    assert not any(start.startswith('2026-10-29') for start in every_start)  # moved away from Thursday
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
    assert 'meet.example.com' not in str(parse_sample().events)


def calendar_of(*events):
    return b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:x\r\n' + b''.join(events) + b'END:VCALENDAR\r\n'


def test_long_text_is_cut_and_line_breaks_flattened():
    feed = calendar_feed.parse(calendar_of(
        b'BEGIN:VEVENT\r\nUID:long@t\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:20260925T160000Z\r\n'
        b'DTEND:20260925T170000Z\r\nSUMMARY:' + b'A' * 300 + b'\r\nLOCATION:Building 4\\nRoom 212\r\nEND:VEVENT\r\n'),
        *WINDOW, timezone=NEW_YORK)

    [event] = feed.events
    assert event['title'] == 'A' * 200
    assert event['location'] == 'Building 4 Room 212'


def test_at_most_500_events_the_soonest_ones():
    events = [b'BEGIN:VEVENT\r\nUID:%d@t\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:202609%02dT%02d%02d00Z\r\n'
              b'SUMMARY:Event %03d\r\nEND:VEVENT\r\n' % (n, 22 + n // 200, 12 + (n % 200) // 60, n % 60, n)
              for n in range(600)]

    feed = calendar_feed.parse(calendar_of(*events), *WINDOW, timezone=NEW_YORK)

    assert len(feed.events) == 500
    assert feed.events[0]['title'] == 'Event 000'


@pytest.mark.parametrize('data', [b'', b'<html><body>Sign in</body></html>', b'\xff\xfe\x00 not text'])
def test_something_that_is_not_a_calendar_is_an_error(data):
    with pytest.raises(calendar_feed.CalendarError, match='did not return a calendar'):
        calendar_feed.parse(data, *WINDOW, timezone=NEW_YORK)


def test_an_empty_calendar_is_fine():
    feed = calendar_feed.parse(calendar_of(), *WINDOW, timezone=NEW_YORK)

    assert (feed.name, feed.events, feed.skipped) == ('', [], 0)


def test_one_odd_event_does_not_hide_the_others(monkeypatch):
    real = calendar_feed.to_event

    def picky(component, timezone):
        if str(component.get('SUMMARY')) == 'Team BBQ':
            raise ValueError('odd event')
        return real(component, timezone)
    monkeypatch.setattr(calendar_feed, 'to_event', picky)

    feed = parse_sample()

    assert (len(feed.events), feed.skipped) == (16, 1)


# --- fetch: getting the bytes -----------------------------------------------------------

class FeedHandler(BaseHTTPRequestHandler):
    """Answers like Google would, or misbehaves, depending on the path."""

    def do_GET(self):
        if self.path.startswith('/missing'):
            self.send_error(404)
            return
        if self.path.startswith('/slow'):
            real_time.sleep(1.5)
        if self.path.startswith('/drip'):  # 200 bytes every 0.3 s: each read is quick, the whole is slow
            self.send_response(200); self.send_header('Content-Length', '6000'); self.end_headers()
            for _ in range(30):
                self.wfile.write(b'X' * 200); self.wfile.flush(); real_time.sleep(0.3)
            return
        if self.path.startswith('/to-external'):
            self.send_response(302); self.send_header('Location', 'http://example.com/basic.ics'); self.end_headers(); return
        if self.path.startswith('/to-self'):
            self.send_response(302); self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/ok/basic.ics'); self.end_headers(); return
        if self.path.startswith('/wrong-length'):  # promises 100000 bytes, sends 100, closes
            self.send_response(200); self.send_header('Content-Length', '100000'); self.end_headers(); self.wfile.write(b'Z' * 100); return
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
    assert error.value.__cause__ is None and error.value.__suppress_context__  # nor in a traceback
    assert calendar_feed.describe(secret) == '127.0.0.1'
    assert calendar_feed.describe('https://calendar.google.com/calendar/ical/x/private-abc/basic.ics') == 'calendar.google.com'
    assert calendar_feed.describe('/srv/display/data/demo-calendar.ics') == 'demo-calendar.ics'


# --- sync: fetch, parse, save -------------------------------------------------------------

@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / 'calendar.ics'
    path.write_bytes(SAMPLE)
    return str(path)


def test_sync_saves_the_events_in_the_window_and_the_result(db_conn, sample_file):
    ok, message, _ = calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)

    assert (ok, message) == (True, '17 events')
    titles = [row['title'] for row in db_conn.execute('SELECT title FROM events ORDER BY start_at, id')]
    assert titles[:3] == ['Brake bias test', 'Machine shop open hours', 'Suspension design review']
    status = models.calendar_status(db_conn)
    assert (status['ok'], status['synced_at'], status['calendar_name']) == (1, '2026-09-22T14:10:00', 'Formula SAE')


def test_sync_without_a_zone_uses_this_computers(db_conn, sample_file):
    ok, _, _ = calendar_feed.sync(db_conn, sample_file)

    assert ok and models.count_events(db_conn) == 17


def test_a_second_read_of_an_unchanged_calendar_leaves_the_events_alone(db_conn, sample_file, fake_clock):
    calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)
    ids = [row['id'] for row in db_conn.execute('SELECT id FROM events ORDER BY id')]
    fake_clock.advance(minutes=5)

    calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)

    assert [row['id'] for row in db_conn.execute('SELECT id FROM events ORDER BY id')] == ids
    assert models.calendar_status(db_conn)['synced_at'] == '2026-09-22T14:15:00'


def test_a_failed_read_keeps_the_saved_events_and_says_why(db_conn, sample_file, tmp_path, fake_clock, caplog):
    calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)
    fake_clock.advance(minutes=5)

    ok, message, _ = calendar_feed.sync(db_conn, str(tmp_path / 'no-such-calendar.ics'), timezone=NEW_YORK)

    assert ok is False and 'Could not reach the calendar' in message
    assert models.count_events(db_conn) == 17
    status = models.calendar_status(db_conn)
    assert (status['ok'], status['synced_at'], status['checked_at']) == (0, '2026-09-22T14:10:00', '2026-09-22T14:15:00')
    assert 'no-such-calendar.ics' in caplog.text  # a file's name is not a secret...


def test_the_log_never_holds_the_secret_link(db_conn, caplog):
    calendar_feed.sync(db_conn, 'http://127.0.0.1:9/calendar/ical/x/private-abc123/basic.ics')

    assert 'private-abc123' not in caplog.text  # ...but the link is
    assert '127.0.0.1' in caplog.text


def test_a_link_that_returns_a_web_page_keeps_the_saved_events(db_conn, sample_file, tmp_path):
    calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)
    (tmp_path / 'login.ics').write_text('<html><body>Sign in to continue</body></html>')

    ok, message, _ = calendar_feed.sync(db_conn, str(tmp_path / 'login.ics'), timezone=NEW_YORK)

    assert ok is False and 'did not return a calendar' in message
    assert models.count_events(db_conn) == 17


@pytest.mark.parametrize('source', [None, ''])
def test_no_link_set_is_reported_not_raised(db_conn, source):
    ok, message, _ = calendar_feed.sync(db_conn, source)

    assert ok is False and 'No calendar link is set' in message
    assert models.calendar_status(db_conn)['message'] == message


def test_skipped_events_are_counted_in_the_message(db_conn, sample_file, monkeypatch):
    real = calendar_feed.to_event

    def picky(component, timezone):
        if str(component.get('SUMMARY')) == 'Team BBQ':
            raise ValueError('odd event')
        return real(component, timezone)
    monkeypatch.setattr(calendar_feed, 'to_event', picky)

    ok, message, _ = calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK)

    assert (ok, message) == (True, '16 events (1 could not be read and was left out)')


def test_sync_says_whether_the_saved_events_changed(db_conn, sample_file, tmp_path):
    # The 30-second loop only rewrites the screen's files when they did (kind to the SD card).
    assert calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK).changed is True
    assert calendar_feed.sync(db_conn, sample_file, timezone=NEW_YORK).changed is False
    assert calendar_feed.sync(db_conn, str(tmp_path / 'gone.ics'), timezone=NEW_YORK).changed is False


# --- Found by the stress test (docs/decisions/2026-09-30-stress-test-notes.md) --------------

def calendar_with(*events):
    return calendar_of(*(f'BEGIN:VEVENT\r\nUID:{n}@t\r\nDTSTAMP:20260901T000000Z\r\n{body}END:VEVENT\r\n'.encode()
                         for n, body in enumerate(events)))


def test_an_every_second_or_every_minute_repeat_is_left_out_instead_of_taking_minutes():  # M1
    feed = calendar_feed.parse(calendar_with('DTSTART:20260101T000000Z\r\nRRULE:FREQ=SECONDLY\r\nSUMMARY:Tick\r\n',
                                             'DTSTART:20260101T000000Z\r\nRRULE:FREQ=MINUTELY\r\nSUMMARY:Ping\r\n',
                                             'DTSTART:20261001T180000Z\r\nRRULE:FREQ=HOURLY;COUNT=3\r\nSUMMARY:Hourly\r\n'),
                               *WINDOW, timezone=NEW_YORK)

    assert [e['title'] for e in feed.events] == ['Hourly'] * 3
    assert feed.skipped == 2


@pytest.mark.parametrize('bad', [
    'DTSTART:20261501T180000Z\r\nSUMMARY:Bad date\r\n',
    'DTSTART:20261001T180000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=XX;UNTIL=garbage\r\nSUMMARY:Bad rule\r\n',
    'SUMMARY:No start\r\n',
])
def test_one_broken_event_is_skipped_and_the_rest_are_kept(bad):  # M2
    feed = calendar_feed.parse(calendar_with('DTSTART:20261001T180000Z\r\nSUMMARY:Good\r\n', bad,
                                             'DTSTART:20261002T180000Z\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\nSUMMARY:Weekly\r\n'),
                               *WINDOW, timezone=NEW_YORK)

    assert [e['title'] for e in feed.events] == ['Good', 'Weekly', 'Weekly']
    assert feed.skipped == 1


def test_a_moved_week_still_belongs_to_its_series_when_another_event_is_broken():  # M2, series kept together
    feed = calendar_feed.parse(calendar_of(
        b'BEGIN:VEVENT\r\nUID:w@t\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:20261001T180000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\n'
        b'BEGIN:VEVENT\r\nUID:w@t\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:20261009T180000Z\r\nRECURRENCE-ID:20261008T180000Z\r\nSUMMARY:Weekly (moved)\r\nEND:VEVENT\r\n'
        b'BEGIN:VEVENT\r\nUID:b@t\r\nDTSTAMP:20260901T000000Z\r\nSUMMARY:No start\r\nEND:VEVENT\r\n'),
        *WINDOW, timezone=NEW_YORK)

    assert [(e['title'], e['start_at'][:10]) for e in feed.events] == [
        ('Weekly', '2026-10-01'), ('Weekly (moved)', '2026-10-09'), ('Weekly', '2026-10-15')]
    assert feed.skipped == 1


def test_control_characters_and_text_direction_tricks_are_dropped_from_titles():  # L1
    feed = calendar_feed.parse(calendar_with('DTSTART:20261001T180000Z\r\nSUMMARY:Team\x00 ‮meeting‬ \x07now\r\nLOCATION:Room\t1\r\n'),
                               *WINDOW, timezone=NEW_YORK)

    assert feed.events[0]['title'] == 'Team meeting now'
    assert feed.events[0]['location'] == 'Room 1'


def test_fetch_gives_up_on_a_server_that_drips_the_body_slowly(feed_server, monkeypatch):  # M3
    monkeypatch.setattr(calendar_feed, 'TIMEOUT_S', 1)

    started = real_time.monotonic()
    with pytest.raises(calendar_feed.CalendarError, match='took too long'):
        calendar_feed.fetch(feed_server + '/drip/basic.ics')
    assert real_time.monotonic() - started < 4


def test_fetch_refuses_a_redirect_to_plain_http_elsewhere(feed_server):  # M4
    with pytest.raises(calendar_feed.CalendarError, match='redirect'):
        calendar_feed.fetch(feed_server + '/to-external/basic.ics')


def test_fetch_follows_a_redirect_to_https_or_to_this_computer(feed_server):  # M4
    assert calendar_feed.fetch(feed_server + '/to-self/basic.ics') == SAMPLE


def test_fetch_refuses_a_body_cut_short_by_the_server(feed_server):  # L3
    with pytest.raises(calendar_feed.CalendarError, match='cut short'):
        calendar_feed.fetch(feed_server + '/wrong-length/basic.ics')
