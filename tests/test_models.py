"""The saved copy of the calendar, settings and photos in the database."""
from datetime import datetime

import pytest

from app import models
from helpers import calendar_event

EVENTS = [
    {'uid': 'a@t', 'title': 'Weekly Meeting', 'start_at': '2026-09-24T18:30', 'end_at': '2026-09-24T20:00',
     'all_day': False, 'location': 'Team Room'},
    {'uid': 'b@t', 'title': 'Career fair', 'start_at': '2026-09-29T00:00', 'end_at': None,
     'all_day': True, 'location': ''},
]


def stored(db_conn):
    return [dict(row) for row in db_conn.execute(
        'SELECT uid, title, start_at, end_at, all_day, location FROM events ORDER BY id')]


# --- Events: a copy of the Google Calendar ----------------------------------------------

def test_replace_events_stores_the_calendar(db_conn):
    assert models.replace_events(db_conn, EVENTS) is True

    assert stored(db_conn) == [dict(EVENTS[0], all_day=0), dict(EVENTS[1], all_day=1)]
    assert models.count_events(db_conn) == 2


def test_replace_events_writes_nothing_when_nothing_changed(db_conn):
    models.replace_events(db_conn, EVENTS)
    ids_before = [row['id'] for row in db_conn.execute('SELECT id FROM events ORDER BY id')]
    changes_before = db_conn.total_changes

    assert models.replace_events(db_conn, EVENTS) is False

    assert db_conn.total_changes == changes_before  # kind to the Pi's SD card
    assert [row['id'] for row in db_conn.execute('SELECT id FROM events ORDER BY id')] == ids_before


def test_replace_events_drops_events_that_left_the_calendar(db_conn):
    models.replace_events(db_conn, EVENTS)

    assert models.replace_events(db_conn, EVENTS[1:]) is True

    assert [e['title'] for e in stored(db_conn)] == ['Career fair']


def test_events_since_keeps_anything_ending_on_or_after_the_cutoff_sorted_by_start(db_conn):
    models.replace_events(db_conn, [
        calendar_event('long gone', '2026-09-01T09:00', '2026-09-01T10:00'),
        calendar_event('multi-day, still going', '2026-09-18T09:00', '2026-09-25T09:00'),
        calendar_event('yesterday', '2026-09-21T18:00'),
        calendar_event('tomorrow', '2026-09-23T18:00'),
    ])

    kept = models.events_since(db_conn, datetime(2026, 9, 21, 0, 0))

    assert [e['title'] for e in kept] == ['multi-day, still going', 'yesterday', 'tomorrow']


def test_calendar_status_starts_empty_and_records_each_check(db_conn, fake_clock):
    assert models.calendar_status(db_conn)['synced_at'] is None

    models.record_calendar_check(db_conn, True, '12 events', 'Formula SAE')
    fake_clock.advance(minutes=5)
    models.record_calendar_check(db_conn, False, 'Could not reach the calendar (timed out).')

    status = models.calendar_status(db_conn)
    assert (status['synced_at'], status['checked_at']) == ('2026-09-22T14:10:00', '2026-09-22T14:15:00')
    assert (status['ok'], status['message']) == (0, 'Could not reach the calendar (timed out).')
    assert status['calendar_name'] == 'Formula SAE'  # kept from the last read that worked


# --- Settings and photos ------------------------------------------------------------------

def test_settings_default_and_save_reports_only_changes(db_conn):
    assert models.get_settings(db_conn)['photo_interval_s'] == '15'

    changed = models.save_settings(db_conn, {'competition_name': 'FSAE Michigan', 'photo_interval_s': '15',
                                          'font_scale': '1.2'})

    assert changed == {'competition_name': ('', 'FSAE Michigan'), 'font_scale': ('1.0', '1.2')}
    assert models.get_settings(db_conn)['font_scale'] == '1.2'


def test_save_settings_ignores_unknown_keys(db_conn):
    models.save_settings(db_conn, {'is_admin': 'yes'})

    assert 'is_admin' not in models.get_settings(db_conn)


PHOTOS = [  # as drive_photos.sync saves them: the folder's newest file first
    {'drive_id': 'f1', 'version': 'aaa', 'filename': '1111111111111111.jpg', 'name': 'IMG_1.jpg',
     'caption': 'Car reveal night', 'problem': None},
    {'drive_id': 'f2', 'version': 'bbb', 'filename': None, 'name': 'poster.gif', 'caption': '',
     'problem': 'poster.gif is not a photo we can read.'},
    {'drive_id': 'f3', 'version': 'ccc', 'filename': '3333333333333333.jpg', 'name': 'IMG_3.jpg',
     'caption': '', 'problem': None},
]


def test_replace_photos_stores_the_folder_in_order_and_writes_nothing_when_unchanged(db_conn):
    assert models.replace_photos(db_conn, PHOTOS) is True
    changes_before = db_conn.total_changes

    assert models.replace_photos(db_conn, PHOTOS) is False

    assert db_conn.total_changes == changes_before  # kind to the Pi's SD card
    assert [dict(row) for row in models.list_photos(db_conn)] == [dict(p, id=i + 1) for i, p in enumerate(PHOTOS)]


def test_replace_photos_drops_photos_that_left_the_folder(db_conn):
    models.replace_photos(db_conn, PHOTOS)

    assert models.replace_photos(db_conn, PHOTOS[2:]) is True

    assert [p['drive_id'] for p in models.list_photos(db_conn)] == ['f3']


def test_photos_for_display_skips_files_that_could_not_be_used(db_conn):
    models.replace_photos(db_conn, PHOTOS)

    assert [p['filename'] for p in models.photos_for_display(db_conn)] == ['1111111111111111.jpg',
                                                                          '3333333333333333.jpg']


def test_drive_status_starts_empty_and_records_each_check(db_conn, fake_clock):
    assert models.drive_status(db_conn)['synced_at'] is None

    models.record_drive_check(db_conn, True, '12 photos', 'Team Photos')
    fake_clock.advance(minutes=5)
    models.record_drive_check(db_conn, False, 'Could not reach Google Drive (timed out).')

    status = models.drive_status(db_conn)
    assert (status['synced_at'], status['checked_at']) == ('2026-09-22T14:10:00', '2026-09-22T14:15:00')
    assert (status['ok'], status['message']) == (0, 'Could not reach Google Drive (timed out).')
    assert status['folder_name'] == 'Team Photos'  # kept from the last read that worked
