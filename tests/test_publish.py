"""publish(): regenerating web/data/*.json from the database (PLAN.md §5.2, §5.3; 2.3.9)."""
import json
import logging
import os
import subprocess
from datetime import datetime

import jsonschema
import pytest

from app import clock, models, publish
from helpers import FIXTURES, calendar_event, published

SCHEMA = json.loads((FIXTURES / 'events.schema.json').read_text())


def add_event(db_conn, title, start, end=None, all_day=False, location='Team Room'):
    """Add one event to the saved copy of the calendar."""
    saved = [dict(row) for row in db_conn.execute('SELECT uid, title, start_at, end_at, all_day, location FROM events')]
    saved.append(calendar_event(title, clock.minute(start), clock.minute(end) if end else None, all_day, location))
    models.replace_events(db_conn, sorted(saved, key=lambda event: event['start_at']))


def test_events_json_matches_the_contract(db_conn, web_dir):
    add_event(db_conn, 'Weekly Meeting', datetime(2026, 9, 24, 18, 30), datetime(2026, 9, 24, 20, 0))
    add_event(db_conn, 'Career fair', datetime(2026, 9, 29), all_day=True, location='')
    models.record_calendar_check(db_conn, True, '2 events', 'Formula SAE')

    publish.publish(db_conn, web_dir, time_synced=True)

    events = published(web_dir, 'events.json')
    jsonschema.validate(events, SCHEMA)
    assert events['generated_at'] == '2026-09-22T14:10:00'
    assert events['time_synced'] is True
    assert events['synced_at'] == '2026-09-22T14:10:00'
    assert events['events'][0] == {
        'title': 'Weekly Meeting', 'start': '2026-09-24T18:30', 'end': '2026-09-24T20:00',
        'all_day': False, 'location': 'Team Room'}
    assert events['events'][1]['all_day'] is True


def test_events_window_starts_at_yesterday_midnight(db_conn, web_dir):
    add_event(db_conn, 'three days ago', datetime(2026, 9, 19, 18, 0), datetime(2026, 9, 19, 19, 0))
    add_event(db_conn, 'ended yesterday morning', datetime(2026, 9, 21, 8, 0), datetime(2026, 9, 21, 9, 0))
    add_event(db_conn, 'all day yesterday', datetime(2026, 9, 21), all_day=True)
    add_event(db_conn, 'started last week, ends Friday', datetime(2026, 9, 15, 9, 0), datetime(2026, 9, 25, 17, 0))
    add_event(db_conn, 'tomorrow', datetime(2026, 9, 23, 18, 0))

    publish.publish(db_conn, web_dir, time_synced=True)

    titles = [e['title'] for e in published(web_dir, 'events.json')['events']]
    assert titles == ['started last week, ends Friday', 'all day yesterday', 'ended yesterday morning', 'tomorrow']


def test_synced_at_is_null_until_the_calendar_has_been_read(db_conn, web_dir, fake_clock):
    publish.publish(db_conn, web_dir, time_synced=True)
    assert published(web_dir, 'events.json')['synced_at'] is None

    models.record_calendar_check(db_conn, True, '0 events', 'Formula SAE')
    fake_clock.advance(minutes=5)
    models.record_calendar_check(db_conn, False, 'Could not reach the calendar (timed out).')
    publish.publish(db_conn, web_dir, time_synced=True)

    events = published(web_dir, 'events.json')
    assert events['synced_at'] == '2026-09-22T14:10:00'  # the last read that worked
    assert events['generated_at'] == '2026-09-22T14:15:00'
    jsonschema.validate(events, SCHEMA)


def test_time_synced_false_is_published(db_conn, web_dir):
    publish.publish(db_conn, web_dir, time_synced=False)

    assert published(web_dir, 'events.json')['time_synced'] is False


def test_photos_json_lists_the_usable_photos_in_the_folders_order(db_conn, web_dir, fake_clock):
    models.replace_photos(db_conn, [
        {'drive_id': 'b', 'version': '1', 'filename': 'bbbbbbbbbbbbbbbb.jpg', 'name': 'IMG_2.jpg',
         'caption': 'Endurance run', 'problem': None},
        {'drive_id': 'x', 'version': '1', 'filename': None, 'name': 'poster.gif', 'caption': '',
         'problem': 'poster.gif is not a photo we can read.'},
        {'drive_id': 'a', 'version': '1', 'filename': 'aaaaaaaaaaaaaaaa.jpg', 'name': 'IMG_1.jpg',
         'caption': '', 'problem': None},
    ])
    fake_clock.advance(seconds=5)

    publish.publish(db_conn, web_dir, time_synced=True)

    assert published(web_dir, 'photos.json') == {
        'generated_at': '2026-09-22T14:10:05',
        'photos': [{'file': 'bbbbbbbbbbbbbbbb.jpg', 'caption': 'Endurance run'},
                   {'file': 'aaaaaaaaaaaaaaaa.jpg', 'caption': ''}],
    }


def test_settings_json_has_typed_values(db_conn, web_dir):
    models.save_settings(db_conn, {'competition_name': 'Gas car: FSAE Michigan', 'competition_date': '2027-05-12',
                                   'competition2_name': 'EV car: FSAE Michigan', 'competition2_date': '2027-05-13',
                                   'photo_interval_s': '20', 'font_scale': '1.25', 'banner_text': 'Shop closed Friday'})

    publish.publish(db_conn, web_dir, time_synced=True)

    assert published(web_dir, 'settings.json') == {
        'competitions': [{'name': 'Gas car: FSAE Michigan', 'date': '2027-05-12'},
                         {'name': 'EV car: FSAE Michigan', 'date': '2027-05-13'}],
        'photo_interval_s': 20, 'font_scale': 1.25, 'banner_text': 'Shop closed Friday'}


@pytest.mark.parametrize('first, second, expected', [
    (('', ''), ('', ''), []),
    (('Gas car', '2027-05-12'), ('EV car', ''), [{'name': 'Gas car', 'date': '2027-05-12'}]),
    (('', ''), ('EV car', '2027-06-17'), [{'name': 'EV car', 'date': '2027-06-17'}]),
    (('', '2027-05-12'), ('', ''), [{'name': '', 'date': '2027-05-12'}]),  # the screen says "Competition"
])
def test_only_competitions_with_a_date_are_published(db_conn, web_dir, first, second, expected):
    models.save_settings(db_conn, {'competition_name': first[0], 'competition_date': first[1],
                                   'competition2_name': second[0], 'competition2_date': second[1]})

    publish.publish(db_conn, web_dir, time_synced=True)

    assert published(web_dir, 'settings.json')['competitions'] == expected


def test_garbage_settings_fall_back_to_defaults(db_conn, web_dir):
    models.save_settings(db_conn, {'photo_interval_s': 'abc', 'font_scale': 'huge'})

    publish.publish(db_conn, web_dir, time_synced=True)

    settings = published(web_dir, 'settings.json')
    assert (settings['photo_interval_s'], settings['font_scale']) == (15, 1.0)


@pytest.mark.parametrize('value', ['inf', 'nan', '1e999'])
def test_non_finite_settings_fall_back_so_the_json_stays_valid(db_conn, web_dir, value):
    # Python's json module happily writes Infinity and NaN; the browser's JSON.parse
    # rejects them, and the screen would then refuse the whole settings file.
    models.save_settings(db_conn, {'font_scale': value})

    publish.publish(db_conn, web_dir, time_synced=True)

    assert published(web_dir, 'settings.json')['font_scale'] == 1.0


def test_write_files_refuses_values_json_cannot_express(web_dir):
    with pytest.raises(ValueError):
        publish.write_files(web_dir / 'data', {'settings.json': {'font_scale': float('nan')}})

    assert not (web_dir / 'data' / 'settings.json').exists()


def test_publish_leaves_no_temp_files(db_conn, web_dir):
    publish.publish(db_conn, web_dir, time_synced=True)

    assert sorted(p.name for p in (web_dir / 'data').iterdir()) == ['events.json', 'photos.json', 'settings.json']


# 2.3.9 (publish half; the route half is in test_admin.py)
def test_failed_replace_keeps_old_files_and_raises(db_conn, web_dir, monkeypatch, caplog):
    publish.publish(db_conn, web_dir, time_synced=True)
    before = {p.name: p.read_text() for p in (web_dir / 'data').iterdir()}
    add_event(db_conn, 'new event', datetime(2026, 9, 30, 18, 0))

    def broken_replace(src, dst):
        raise OSError('disk on fire')
    monkeypatch.setattr(os, 'replace', broken_replace)

    with caplog.at_level(logging.ERROR), pytest.raises(publish.PublishError):
        publish.publish(db_conn, web_dir, time_synced=True)

    after = {p.name: p.read_text() for p in (web_dir / 'data').iterdir()}
    assert after == before  # old files intact, no .tmp files left behind
    assert 'disk on fire' in caplog.text


def test_failure_partway_through_the_renames_is_logged_precisely(db_conn, web_dir, monkeypatch, caplog):
    publish.publish(db_conn, web_dir, time_synced=True)
    old_photos = (web_dir / 'data' / 'photos.json').read_text()
    add_event(db_conn, 'new event', datetime(2026, 9, 30, 18, 0))
    real_replace, renames = os.replace, []

    def second_rename_fails(src, dst):
        renames.append(dst)
        if len(renames) == 2:
            raise OSError('disk on fire')
        real_replace(src, dst)
    monkeypatch.setattr(os, 'replace', second_rename_fails)

    with caplog.at_level(logging.ERROR), pytest.raises(publish.PublishError):
        publish.publish(db_conn, web_dir, time_synced=True)

    data = web_dir / 'data'
    assert sorted(p.name for p in data.iterdir()) == ['events.json', 'photos.json', 'settings.json']
    assert 'new event' in (data / 'events.json').read_text()  # this one was already swapped in
    assert (data / 'photos.json').read_text() == old_photos
    assert 'Already replaced: events.json' in caplog.text
    assert 'Still the old copy: photos.json, settings.json' in caplog.text


def test_invalid_data_is_never_published(db_conn, web_dir):
    publish.publish(db_conn, web_dir, time_synced=True)
    before = (web_dir / 'data' / 'events.json').read_text()
    db_conn.execute("INSERT INTO events (uid, title, start_at) VALUES ('x', '', '2026-09-30T18:00')")  # no title

    with pytest.raises(publish.PublishError):
        publish.publish(db_conn, web_dir, time_synced=True)

    assert (web_dir / 'data' / 'events.json').read_text() == before


@pytest.mark.parametrize('stdout, expected', [('yes\n', True), ('no\n', False)])
def test_ntp_synced_reads_timedatectl(monkeypatch, stdout, expected):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout))

    assert publish.ntp_synced() is expected


def test_ntp_synced_is_true_where_timedatectl_does_not_exist(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('timedatectl')
    monkeypatch.setattr(subprocess, 'run', missing)

    assert publish.ntp_synced() is True
