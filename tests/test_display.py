"""The display page (web/) in a real browser.

Automates the checks from PLAN.md §7 Step 2.2 that a headless browser can do. They are
also in docs/TESTING.md for a person to run on the real TV. The page's clock is
controlled by Playwright, so "an hour later" or "across midnight" take no real time.
The `screen` fixture (and the Screen/Site helpers) live in conftest.py; the wording
rules from SPEC.md §3 and §4 are checked in test_display_wording.py.

Needs Playwright:   pip install playwright && playwright install chromium
(or point PLAYWRIGHT_CHROMIUM at an existing Chromium/Chrome). Skipped without it.
"""
import json
import shutil
from datetime import datetime, timedelta

import pytest

from conftest import FIXED, NEW_YORK
from helpers import FIXTURES

pytest.importorskip('playwright.sync_api')


def more_count(screen):
    text = screen.text('agenda-more')
    return int(text.split()[0].lstrip('+')) if text.startswith('+') else 0


# --- The normal day ------------------------------------------------------------------

def test_normal_schedule_at_a_glance(screen):
    screen.open()

    assert screen.text('hero-kicker') == 'Happening now'
    assert screen.text('hero-title') == 'Machine shop open hours'
    assert screen.text('hero-relative') == 'Now'
    assert screen.text('hero-absolute') == 'Tue, Sep 22 · 1 – 5 PM'
    assert screen.text('countdown') == 'FSAE Michigan in 232 days'
    assert screen.text('clock-date') == 'Tuesday, September 22'
    assert screen.text('clock-time') == '2:10PM'
    rows = screen.rows()
    assert rows[0] == ['Today', '4 – 5 PM', 'Suspension design review · Team Room']
    assert rows[2] == ['Fri, Sep 25', '11:59 PM', 'Design report due']
    assert 'Brake bias test' not in screen.page.text_content('body')  # ended yesterday
    assert not screen.shows('stale-badge')
    assert screen.errors == []


def test_the_corner_stays_empty_while_all_is_well(screen):
    # No "Updated just now" line: the bottom corner only speaks up when something is wrong.
    screen.open()

    assert not screen.shows('status')
    assert 'Updated' not in screen.page.inner_text('body')
    assert screen.overflow_report() == []


def test_there_are_no_type_badges_or_notes(screen):
    # Google Calendar has no event types, and its descriptions are mostly video-call links.
    screen.open()

    assert screen.page.query_selector_all('.badge') == []
    assert screen.page.query_selector('#hero-notes') is None
    assert all(len(row) == 3 for row in screen.rows())
    assert screen.text('hero-where') == 'Machine shop'


def calendar_read(minutes_ago):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    read_at = FIXED.replace(tzinfo=None) - timedelta(minutes=minutes_ago or 0)
    document['synced_at'] = None if minutes_ago is None else read_at.strftime('%Y-%m-%dT%H:%M:%S')
    document['generated_at'] = '2026-09-22T14:11:00'  # the files themselves are fresh
    return document


@pytest.mark.parametrize('minutes_ago, warned', [(5, False), (55, False), (65, True), (None, True)])
def test_warns_when_the_calendar_has_not_been_read_for_an_hour(screen, minutes_ago, warned):
    # While Google can't be reached the Pi keeps rewriting the files from its saved events,
    # so fresh files alone don't prove a fresh schedule.
    screen.open()
    screen.site.write('events.json', calendar_read(minutes_ago))
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('countdown').textContent.includes('232')")  # still drawing

    assert screen.shows('stale-badge') is warned
    assert screen.page.get_attribute('#status-dot', 'data-state') == ('stale' if warned else 'ok')
    if warned:
        assert screen.text('stale-badge') == 'Schedule may be out of date'
    assert screen.text('hero-title') == 'Machine shop open hours'  # the saved events stay up


def test_events_json_without_synced_at_is_refused(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    del document['synced_at']
    screen.open()

    screen.site.write('events.json', document)
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('status-dot').dataset.state === 'offline'")

    assert any('events.json' in error for error in screen.errors)


# 2.2.1
def test_empty_schedule_shows_a_friendly_empty_state(screen):
    screen.open(events='events_empty.json')

    assert screen.text('hero-title') == 'Nothing on the schedule'
    assert not screen.shows('upcoming')
    assert screen.errors == []


# 2.2.2
def test_forty_events_truncate_to_seven_rows_plus_more(screen):
    screen.open(events='events_40.json')

    rows = screen.rows()
    assert len(rows) == 7
    assert screen.text('agenda-more') == f'+{39 - 7} more in the next 30 days'
    assert screen.overflow_report() == []


# 2.2.3
def test_malformed_data_mid_session_keeps_the_screen_and_flags_it(screen):
    screen.open()
    before = screen.rows()

    shutil.copy(FIXTURES / 'events_malformed.json', screen.site.root / 'data' / 'events.json')
    screen.advance(seconds=61)
    screen.wait_until("!document.getElementById('stale-badge').hidden")

    assert screen.rows() == before
    assert screen.text('hero-title') == 'Machine shop open hours'
    assert any('events.json' in error for error in screen.errors)
    assert not any(error.startswith('uncaught') for error in screen.errors)


# 2.2.4
def test_event_disappears_a_minute_after_it_ends_without_a_reload(screen):
    screen.open(at=datetime(2026, 9, 22, 16, 59, tzinfo=NEW_YORK))
    assert screen.text('hero-title') == 'Machine shop open hours'
    assert screen.rows()[0][2].startswith('Suspension design review')

    screen.advance(minutes=2)

    assert screen.text('hero-title') == 'Weekly Meeting'
    assert screen.text('hero-kicker') == 'Next up'
    assert screen.text('hero-relative') == 'Thursday'
    assert all(not row[2].startswith('Suspension') for row in screen.rows())


# 2.2.5
def test_all_day_and_multi_day_events_are_single_rows_with_ranges(screen):
    screen.open()

    by_title = {row[2].split(' · ')[0]: row for row in screen.rows()}
    assert by_title['Career fair'][1] == 'All day'
    assert by_title['Testing weekend'][1] == 'Oct 2 – 4'
    assert [row[2].split(' · ')[0] for row in screen.rows()].count('Testing weekend') == 1


# 2.2.6
@pytest.mark.parametrize('size', [(1920, 1080), (1280, 720), (1080, 1920)])
@pytest.mark.parametrize('events', ['events_normal.json', 'events_40.json', 'events_empty.json'])
def test_no_overflow_at_the_target_screen_sizes(screen, size, events):
    screen.page.set_viewport_size({'width': size[0], 'height': size[1]})
    screen.open(events=events, settings={'banner_text': 'Shop closed Friday for the career fair'})

    assert screen.overflow_report() == []


def test_portrait_stacks_hero_then_list_then_photos(screen):
    screen.page.set_viewport_size({'width': 1080, 'height': 1920})
    screen.open()

    tops = screen.page.evaluate("['.hero', '.upcoming', '.photos'].map(s => document.querySelector(s).getBoundingClientRect().top)")
    assert tops == sorted(tops)


# 2.2.8
def test_offline_keeps_content_and_shows_the_stale_badge(screen):
    screen.open()
    before = screen.rows()

    screen.page.route('**/data/**', lambda route: route.abort())
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('status-dot').dataset.state === 'offline'")

    assert screen.rows() == before
    assert screen.text('stale-badge') == 'Can’t load new data. Showing the last copy.'


def test_data_older_than_an_hour_is_flagged_even_when_it_loads(screen):
    screen.open()
    screen.advance(minutes=59)
    assert not screen.shows('stale-badge')

    screen.advance(minutes=2)

    assert screen.text('stale-badge') == 'Schedule may be out of date'
    assert screen.shows('status') and screen.shows('status-dot')
    assert 'Updated' not in screen.page.inner_text('body')


# 2.2.9
def test_clock_date_and_countdown_roll_over_at_midnight(screen):
    screen.open(at=datetime(2026, 10, 31, 23, 59, 30, tzinfo=NEW_YORK))
    assert screen.text('clock-date') == 'Saturday, October 31'
    assert screen.text('countdown') == 'FSAE Michigan in 193 days'

    screen.advance(seconds=31)

    assert screen.text('clock-time') == '12:00AM'
    assert screen.text('clock-date') == 'Sunday, November 1'
    assert screen.text('countdown') == 'FSAE Michigan in 192 days'


def test_clock_follows_the_end_of_daylight_saving(screen):
    screen.open(at=datetime(2026, 11, 1, 1, 59, 30, tzinfo=NEW_YORK))  # EDT, the first 1:59 AM

    screen.advance(seconds=31)  # 2:00 AM EDT is 1:00 AM EST

    assert screen.text('clock-time') == '1:00AM'
    assert screen.text('clock-date') == 'Sunday, November 1'
    assert screen.text('countdown') == 'FSAE Michigan in 192 days'


def test_a_new_calendar_event_is_on_screen_within_30_seconds_of_being_published(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    document['events'].insert(3, {'title': 'Sponsor call', 'start': '2026-09-22T15:00', 'end': '2026-09-22T15:30',
                                  'all_day': False, 'location': 'Room 212'})
    screen.open()

    screen.site.write('events.json', document)
    screen.advance(seconds=31)  # (the short timeouts: the poll must come from the page's own 30 s timer)
    screen.wait_until("document.getElementById('agenda').textContent.includes('Sponsor call')", timeout=3000)

    document['events'] = [e for e in document['events'] if e['title'] != 'Sponsor call']  # deleted in the calendar
    screen.site.write('events.json', document)
    screen.advance(seconds=31)
    screen.wait_until("!document.getElementById('agenda').textContent.includes('Sponsor call')", timeout=3000)


# 2.2.7 (the part a test can check): nothing on the page shows seconds, so between two
# minute changes the page must not touch the DOM at all, and a poll that brings the same
# data must not redraw either. Redrawing anyway would make the Pi lay out and repaint
# the screen for nothing, all day.
def test_the_page_leaves_the_dom_alone_between_minute_changes(screen):
    screen.open(at=datetime(2026, 9, 22, 14, 10, 5, tzinfo=NEW_YORK), settings={'photo_interval_s': 600})
    screen.wait_until("document.querySelector('.photo.is-visible')")  # the first photo is up
    screen.page.evaluate('''() => {
      window.mutations = 0;
      new MutationObserver((records) => { window.mutations += records.length; })
        .observe(document.body, { childList: true, characterData: true, subtree: true, attributes: true });
    }''')

    screen.advance(seconds=40)  # 14:10:05 → 14:10:45: same minute, one poll of unchanged files, no photo change

    assert screen.page.evaluate('window.mutations') == 0
    assert sum(url.endswith('.json') or '.json?' in url for url in screen.requests) >= 6  # it did poll


# 2.2.10
def test_zero_requests_leave_the_pi(screen):
    screen.open()
    screen.advance(seconds=61)

    assert screen.requests
    assert all(url.startswith(screen.site.url) for url in screen.requests)


# 2.2.11
def test_countdown_hidden_when_the_clock_is_not_synced(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    screen.open()

    screen.site.write('events.json', dict(document, time_synced=False))
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('countdown').hidden")

    assert screen.text('hero-title') == 'Machine shop open hours'
    assert len(screen.rows()) > 0


def test_countdown_hidden_without_a_competition_date(screen):
    screen.open(settings={'competitions': []})

    assert not screen.shows('countdown')


# --- Two countdowns: one per car (Owen, 2026-10-13) --------------------------------------

GAS = {'name': 'Gas car: FSAE Michigan', 'date': '2027-05-12'}
EV = {'name': 'EV car: FSAE Lincoln', 'date': '2027-06-17'}
DAYS_FULLY_SHOWN = '''numbers => numbers.map(number => {
  const box = number.getBoundingClientRect(), line = number.closest('li').getBoundingClientRect();
  return [number.textContent, box.left >= line.left && box.right <= line.right + 0.5];
})'''


def countdown_lines(screen):
    return screen.page.eval_on_selector_all('#countdown > li', 'lines => lines.map(line => line.textContent)')


def test_two_countdowns_sooner_first(screen):
    screen.open(settings={'competitions': [EV, GAS]})  # typed in the other order

    assert countdown_lines(screen) == ['Gas car: FSAE Michigan in 232 days', 'EV car: FSAE Lincoln in 268 days']


def test_a_competition_that_has_passed_leaves_and_the_other_stays(screen):
    screen.open(settings={'competitions': [{'name': 'Gas car: Detroit', 'date': '2026-09-21'},
                                           {'name': 'EV car: Lincoln', 'date': '2026-09-23'}]})

    assert countdown_lines(screen) == ['EV car: Lincoln is tomorrow']


def test_two_countdowns_take_no_more_room_than_one(screen):
    screen.open()
    one = screen.page.eval_on_selector('.top', 'top => top.getBoundingClientRect().height')

    screen.open(settings={'competitions': [GAS, EV]})
    two = screen.page.eval_on_selector('.top', 'top => top.getBoundingClientRect().height')

    assert len(countdown_lines(screen)) == 2 and two <= one + 1


@pytest.mark.parametrize('size', [(1920, 1080), (1280, 720), (1080, 1920)])
def test_long_competition_names_are_cut_short_but_the_days_always_show(screen, size):
    screen.page.set_viewport_size({'width': size[0], 'height': size[1]})
    long_names = [{'name': 'Gas car: International Formula Student Championship', 'date': '2027-05-12'},
                  {'name': 'EV car: ' + 'W' * 52, 'date': '2027-06-17'}]
    screen.open(events='events_40.json', settings={'font_scale': 1.5, 'banner_text': 'B' * 140,
                                                  'competitions': long_names})

    assert screen.overflow_report() == []
    assert screen.page.eval_on_selector_all('#countdown strong', DAYS_FULLY_SHOWN) == [['232', True], ['268', True]]
    assert not screen.boxes_overlap('#countdown', '.clock')


# 2.2.12
def test_banner_appears_and_the_layout_reflows(screen):
    screen.open()
    rows_before = len(screen.rows())

    screen.site.use(settings={'banner_text': 'Shop closed Friday — career fair'})
    screen.advance(seconds=61)
    screen.wait_until("!document.getElementById('banner').hidden")

    assert screen.text('banner') == 'Shop closed Friday — career fair'
    assert not screen.boxes_overlap('#banner', '.upcoming')
    assert not screen.boxes_overlap('#banner', '.photos')
    assert screen.overflow_report() == []
    assert len(screen.rows()) <= rows_before


# 2.2.13
@pytest.mark.parametrize('scale', [0.8, 1.0, 1.3])
def test_font_scale_sizes_text_without_overflow(screen, scale):
    screen.open(events='events_40.json', settings={'font_scale': scale})

    clock_px = screen.page.evaluate("parseFloat(getComputedStyle(document.getElementById('clock-time')).fontSize)")
    assert clock_px == pytest.approx(1080 / 100 * 7.5 * scale, rel=0.02)
    assert screen.overflow_report() == []
    assert len(screen.rows()) + more_count(screen) == 39


# 2.2.14
def test_no_photos_shows_the_logo_and_the_rotation_keeps_running(screen):
    screen.open(photos='photos.json')
    screen.site.write('photos.json', {'generated_at': '2026-09-22T14:10:00', 'photos': []})
    screen.advance(seconds=61)
    screen.wait_until("!document.getElementById('photo-placeholder').hidden")
    screen.advance(minutes=5)

    assert screen.page.eval_on_selector_all('.photo.is-visible', 'nodes => nodes.length') == 0
    assert screen.errors == []


def test_photos_rotate_on_the_interval_with_captions(screen):
    screen.open(settings={'photo_interval_s': 15})
    screen.wait_until("document.querySelector('.photo.is-visible')")
    first = screen.page.get_attribute('.photo.is-visible', 'src')

    screen.advance(seconds=16)
    screen.wait_until(f"document.querySelector('.photo.is-visible').getAttribute('src') !== '{first}'")

    assert screen.page.eval_on_selector_all('.photo.is-visible', 'nodes => nodes.length') == 1
    assert not screen.shows('photo-placeholder')


def test_a_request_that_never_answers_does_not_stop_the_polling(screen):
    screen.open()
    stuck = []
    screen.page.route('**/data/events.json*', lambda route: stuck.append(route))  # never answer

    screen.advance(seconds=61)  # the next poll starts and hangs...
    screen.advance(seconds=16)  # ...until the page gives up on it
    screen.wait_until("document.getElementById('status-dot').dataset.state === 'offline'")

    screen.page.unroute('**/data/events.json*')
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('status-dot').dataset.state === 'ok'")


def test_a_shorter_photo_interval_takes_effect_within_a_minute(screen):
    # Not only when the old, possibly 10-minute, timer runs out (UPDATING.md promises
    # changes on screen within a minute).
    screen.open(settings={'photo_interval_s': 600})
    screen.wait_until("document.querySelector('.photo.is-visible')")
    first = screen.page.get_attribute('.photo.is-visible', 'src')

    screen.site.use(settings={'photo_interval_s': 5})
    screen.advance(seconds=61)  # the next poll reads the new interval; the photo has been up longer than that
    screen.advance(seconds=1)

    screen.wait_until(f"document.querySelector('.photo.is-visible').getAttribute('src') !== '{first}'")


def test_editing_a_caption_updates_it_without_reshuffling(screen):
    screen.open(settings={'photo_interval_s': 600})
    screen.wait_until("document.querySelector('.photo.is-visible')")
    showing = screen.page.get_attribute('.photo.is-visible', 'src')
    document = json.loads((FIXTURES / 'photos.json').read_text())
    for photo in document['photos']:
        photo['caption'] = 'Edited caption' if showing.endswith(photo['file']) else photo['caption']

    screen.site.write('photos.json', document)
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('photo-caption').textContent === 'Edited caption'")

    assert screen.page.get_attribute('.photo.is-visible', 'src') == showing


def test_a_broken_photo_is_skipped(screen):
    screen.open()
    screen.site.write('photos.json', {'generated_at': '2026-09-22T14:10:00', 'photos': [
        {'file': 'missing.jpg', 'caption': ''}, {'file': 'sample-1.jpg', 'caption': 'Endurance run'}]})
    screen.advance(seconds=61)
    screen.wait_until("document.querySelector('.photo.is-visible')?.getAttribute('src') === 'photos/sample-1.jpg'")

    assert screen.text('photo-caption') == 'Endurance run'
    assert not any(error.startswith('uncaught') for error in screen.errors)


def test_event_titles_are_shown_as_text_not_html(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    document['events'][1]['title'] = '<img src=x onerror="document.title=1">Shop hours'
    screen.open()

    screen.site.write('events.json', document)
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('hero-title').textContent.startsWith('<img')")

    assert screen.page.query_selector('#hero img') is None
    assert screen.page.title() == 'Team Room Display'


def test_ongoing_multi_day_event_is_listed_under_today(screen):
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    document['events'].insert(2, {'title': 'Sponsor week', 'start': '2026-09-20T00:00', 'end': '2026-09-26T00:00',
                                  'all_day': True, 'location': ''})
    screen.open()

    screen.site.write('events.json', document)
    screen.advance(seconds=61)
    screen.wait_until("document.getElementById('agenda').textContent.includes('Sponsor week')")

    row = next(r for r in screen.rows() if r[2] == 'Sponsor week')
    today_rows = [r for r in screen.rows() if r[0] == 'Today']
    assert row[1] == 'Sep 20 – 26'
    assert len(today_rows) == 1  # the day label is shown once, on the first row of the day


# --- Found by the stress test --------------------------------------------------------------

# H2: 1.5 is the new top of the Text size setting. With a banner and a full list it must
# still fit at every target size; when not even one row fits, the list hides instead of
# showing cut-off text.
@pytest.mark.parametrize('size', [(1920, 1080), (1280, 720), (1080, 1920)])
def test_the_largest_text_size_still_fits_with_a_banner_and_a_full_list(screen, size):
    screen.page.set_viewport_size({'width': size[0], 'height': size[1]})
    screen.open(events='events_40.json', settings={'font_scale': 1.5, 'banner_text': 'B' * 140, 'competitions': [
        {'name': 'International Formula Student Championship', 'date': '2027-05-12'}]})

    assert screen.overflow_report() == []
    assert screen.text('banner') == 'B' * 140


def test_a_text_size_above_the_cap_in_the_file_is_clamped(screen):  # H2
    screen.open(settings={'font_scale': 2.0})

    scale = screen.page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--font-scale').trim()")
    assert scale == '1.5'
    assert screen.overflow_report() == []


def test_photos_that_fail_to_load_are_not_requested_again_until_the_list_changes(screen):  # M7
    screen.open(settings={'photo_interval_s': 5})
    screen.site.write('photos.json', {'generated_at': '2026-09-22T14:10:00',
                                      'photos': [{'file': f'missing{i}.jpg', 'caption': ''} for i in range(50)]})
    screen.advance(seconds=31)
    screen.wait_until("!document.getElementById('photo-placeholder').hidden")
    before = sum('/photos/' in url for url in screen.requests)

    screen.advance(seconds=60)  # twelve more turns of the rotation

    assert sum('/photos/' in url for url in screen.requests) - before <= 5
    assert screen.shows('photo-placeholder')


def test_an_event_whose_end_equals_its_start_is_shown_without_a_range(screen):  # L4
    document = json.loads((FIXTURES / 'events_normal.json').read_text())
    document['events'] = [{'title': 'Zero length', 'start': '2026-09-22T15:00', 'end': '2026-09-22T15:00',
                           'all_day': False, 'location': ''}]
    screen.open()
    screen.site.write('events.json', document)
    screen.advance(seconds=31)
    screen.wait_until("document.getElementById('hero-title').textContent === 'Zero length'")

    assert screen.text('hero-absolute') == 'Tue, Sep 22 · 3 PM'


def test_an_impossible_competition_date_hides_the_countdown(screen):  # L5
    screen.open(settings={'competitions': [{'name': 'FSAE Michigan', 'date': '2027-02-30'}]})

    assert not screen.shows('countdown')
