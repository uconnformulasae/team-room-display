"""1.2.2 — the events.json fixtures match fixtures/events.schema.json (and the malformed
one does not), so the display and the backend agree on the contract."""
import json

import jsonschema
import pytest

from helpers import FIXTURES

SCHEMA = json.loads((FIXTURES / 'events.schema.json').read_text())
GOOD_FIXTURES = sorted(p.name for p in FIXTURES.glob('events_*.json') if p.name != 'events_malformed.json')


def test_schema_is_itself_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_there_are_fixtures_to_check():
    assert set(GOOD_FIXTURES) >= {'events_normal.json', 'events_empty.json', 'events_40.json'}


@pytest.mark.parametrize('name', GOOD_FIXTURES)
def test_fixture_matches_schema(name):
    jsonschema.validate(json.loads((FIXTURES / name).read_text()), SCHEMA)


def test_malformed_fixture_fails_schema():
    data = json.loads((FIXTURES / 'events_malformed.json').read_text())

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)


@pytest.mark.parametrize('bad_start', ['2026-09-24 18:30', '2026-09-24T18:30:00Z', '2026-09-24T18:30-04:00',
                                       'Sept 24 6:30pm', '2026-09-24'])
def test_schema_rejects_times_that_are_not_local_wall_clock_minutes(bad_start):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document(start=bad_start), SCHEMA)


def document(synced_at='2026-09-22T14:10:00', **event):
    event = dict({'title': 'x', 'start': '2026-09-24T18:30', 'end': None, 'all_day': False, 'location': ''}, **event)
    return {'generated_at': '2026-09-22T14:10:00', 'synced_at': synced_at, 'time_synced': True, 'events': [event]}


def test_synced_at_is_a_time_or_null():
    jsonschema.validate(document(), SCHEMA)
    jsonschema.validate(document(synced_at=None), SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document(synced_at='yesterday'), SCHEMA)


@pytest.mark.parametrize('extra', ['id', 'type', 'notes', 'description', 'uid'])
def test_events_carry_nothing_the_screen_does_not_show(extra):
    # e.g. a description, which in Google Calendar is often a private video-call link
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document(**{extra: 'x'}), SCHEMA)


def test_every_events_fixture_is_sorted_by_start():
    for name in GOOD_FIXTURES:
        starts = [e['start'] for e in json.loads((FIXTURES / name).read_text())['events']]
        assert starts == sorted(starts), name


def test_settings_and_photos_fixtures_have_the_published_shape():
    settings = json.loads((FIXTURES / 'settings.json').read_text())
    photos = json.loads((FIXTURES / 'photos.json').read_text())

    assert set(settings) == {'competitions', 'photo_interval_s', 'font_scale', 'banner_text'}
    assert all(set(c) == {'name', 'date'} for c in settings['competitions'])
    assert isinstance(settings['photo_interval_s'], int) and isinstance(settings['font_scale'], float)
    assert set(photos) == {'generated_at', 'photos'}
    assert all(set(p) == {'file', 'caption'} for p in photos['photos'])
