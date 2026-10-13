"""scripts/init_db.py, scripts/publish.py and scripts/set_password.py, run the way systemd
and people run them: as separate processes, configured through the environment."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest
from werkzeug.security import check_password_hash

from app import read_env_file
from helpers import FIXTURES, REPO_ROOT


@pytest.fixture
def paths(tmp_path):
    return {'DISPLAY_DB': str(tmp_path / 'data' / 'display.db'), 'DISPLAY_WEB': str(tmp_path / 'web'),
            'DISPLAY_ENV_FILE': str(tmp_path / 'no-env-file'), 'SECRET_KEY': 'test', 'CALENDAR_ICS_URL': '',
            'DRIVE_FOLDER_URL': '', 'DRIVE_API_KEY': ''}


def run(script, *args, env=None, stdin=''):
    return subprocess.run([sys.executable, str(REPO_ROOT / 'scripts' / script), *args],
                          capture_output=True, text=True, input=stdin, timeout=60,
                          env=dict(os.environ, **(env or {})))


def test_init_db_creates_the_database_and_publishes(paths, tmp_path):
    result = run('init_db.py', env=paths)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'data' / 'display.db').exists()
    settings = json.loads((tmp_path / 'web' / 'data' / 'settings.json').read_text())
    assert settings['photo_interval_s'] == 15


def test_init_db_can_run_twice(paths):
    assert run('init_db.py', env=paths).returncode == 0
    assert run('init_db.py', env=paths).returncode == 0


def test_publish_script_writes_the_json(paths, tmp_path):
    result = run('publish.py', env=paths)

    assert result.returncode == 0, result.stderr
    events = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    assert events['events'] == []


def calendar_file(folder, days_from_now=3):
    """A one-event calendar dated relative to today (these scripts use the real clock)."""
    start = datetime.now() + timedelta(days=days_from_now)
    path = folder / 'team-calendar.ics'
    path.write_text('\r\n'.join([
        'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:test', 'X-WR-CALNAME:Formula SAE', 'BEGIN:VEVENT', 'UID:1@t',
        'DTSTAMP:20260901T000000Z', f'DTSTART:{start:%Y%m%dT%H%M%S}', f'DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%S}',
        'SUMMARY:Weekly Meeting', 'LOCATION:Team Room', 'END:VEVENT', 'END:VCALENDAR', '']))
    return str(path)


def test_publish_script_reads_the_calendar_first(paths, tmp_path):
    result = run('publish.py', env=dict(paths, CALENDAR_ICS_URL=calendar_file(tmp_path)))

    assert result.returncode == 0, result.stderr
    events = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    assert events['synced_at'] is not None
    assert [(e['title'], e['location']) for e in events['events']] == [('Weekly Meeting', 'Team Room')]


def test_publish_script_still_publishes_when_the_calendar_cannot_be_read(paths, tmp_path):
    assert run('publish.py', env=dict(paths, CALENDAR_ICS_URL=calendar_file(tmp_path))).returncode == 0

    result = run('publish.py', env=dict(paths, CALENDAR_ICS_URL='http://127.0.0.1:9/ical/private-abc123/basic.ics'))

    assert result.returncode == 0
    events = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    assert [e['title'] for e in events['events']] == ['Weekly Meeting']  # from the last good read
    assert 'Could not read the calendar at 127.0.0.1' in result.stderr
    assert 'private-abc123' not in result.stderr + result.stdout


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def test_publish_every_keeps_the_screen_matching_the_calendar(paths, tmp_path):
    """`publish.py --every 1` runs forever: an event added to the calendar appears, and one
    taken away disappears, without anyone pressing anything. (This and the next test start
    a real loop and watch files, so they take a few seconds each; that is the point.)"""
    calendar = tmp_path / 'team-calendar.ics'
    events_file = tmp_path / 'web' / 'data' / 'events.json'
    start = datetime.now() + timedelta(days=2)

    def write_calendar(*titles):
        body = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:test', 'X-WR-CALNAME:Formula SAE']
        for n, title in enumerate(titles):
            body += ['BEGIN:VEVENT', f'UID:{n}@t', 'DTSTAMP:20260901T000000Z',
                     f'DTSTART:{start + timedelta(hours=n):%Y%m%dT%H%M%S}', f'SUMMARY:{title}', 'END:VEVENT']
        calendar.write_text('\r\n'.join(body + ['END:VCALENDAR', '']))

    def titles_on_screen():
        try:
            return [e['title'] for e in json.loads(events_file.read_text())['events']]
        except (OSError, ValueError):
            return None

    write_calendar('Weekly Meeting')
    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '1'],
                            env={**os.environ, **paths, 'CALENDAR_ICS_URL': str(calendar)},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_for(lambda: titles_on_screen() == ['Weekly Meeting']), titles_on_screen()

        write_calendar('Weekly Meeting', 'Design review')  # added in Google Calendar
        assert wait_for(lambda: titles_on_screen() == ['Weekly Meeting', 'Design review']), titles_on_screen()

        write_calendar('Design review')  # the meeting was deleted from the calendar
        assert wait_for(lambda: titles_on_screen() == ['Design review']), titles_on_screen()
        assert loop.poll() is None  # still running
    finally:
        loop.terminate()
        output = loop.communicate(timeout=10)[0]
    assert 'every 1 s' in output
    assert 'Traceback' not in output


def test_publish_every_keeps_the_screen_matching_the_photo_folder(paths, tmp_path):
    folder = tmp_path / 'team photos'
    folder.mkdir()
    photos_file = tmp_path / 'web' / 'data' / 'photos.json'
    samples = sorted((FIXTURES / 'sample_photos').glob('*.jpg'))
    (folder / 'first.jpg').write_bytes(samples[0].read_bytes())

    def photos_on_screen():
        try:
            return len(json.loads(photos_file.read_text())['photos'])
        except (OSError, ValueError):
            return None

    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '1'],
                            env={**os.environ, **paths, 'DRIVE_FOLDER_URL': str(folder)},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_for(lambda: photos_on_screen() == 1), photos_on_screen()
        (folder / 'second.jpg').write_bytes(samples[1].read_bytes())  # added to the Drive folder
        assert wait_for(lambda: photos_on_screen() == 2), photos_on_screen()
        (folder / 'first.jpg').unlink()  # taken out of it
        assert wait_for(lambda: photos_on_screen() == 1), photos_on_screen()
        assert loop.poll() is None
    finally:
        loop.terminate()
        output = loop.communicate(timeout=10)[0]
    assert 'Traceback' not in output


def calendar_message(tmp_path):
    connection = sqlite3.connect(tmp_path / 'data' / 'display.db')
    try:
        return connection.execute('SELECT message FROM calendar_sync').fetchone()[0] or ''
    finally:
        connection.close()


def test_publish_every_survives_the_calendar_going_away(paths, tmp_path):
    calendar = tmp_path / 'team-calendar.ics'
    calendar.write_bytes((FIXTURES / 'calendar_sample.ics').read_bytes())
    events_file = tmp_path / 'web' / 'data' / 'events.json'
    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '1'],
                            env={**os.environ, **paths, 'CALENDAR_ICS_URL': str(calendar)},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_for(lambda: events_file.exists())
        calendar.unlink()  # the link stops working
        assert wait_for(lambda: 'Could not reach the calendar' in calendar_message(tmp_path))
        time.sleep(2.5)
        assert loop.poll() is None, loop.communicate()[0]  # a failed read must not end the loop
    finally:
        loop.terminate()
        loop.communicate(timeout=10)


def test_refresh_reads_the_calendar_and_the_photo_folder_then_publishes(app, db_conn, web_dir):
    """publish.refresh() is the one 'read, then publish' both scripts use."""
    from app import publish
    app.config['CALENDAR_ICS_URL'] = str(FIXTURES / 'calendar_sample.ics')
    app.config['DRIVE_FOLDER_URL'] = str(FIXTURES / 'sample_photos')

    calendar, photos = publish.refresh(db_conn, app.config)

    assert (calendar.ok, calendar.message) == (True, '17 events')
    assert (photos.ok, photos.message) == (True, '4 photos')
    assert len(json.loads((web_dir / 'data' / 'events.json').read_text())['events']) == 17
    assert len(json.loads((web_dir / 'data' / 'photos.json').read_text())['photos']) == 4


@pytest.mark.parametrize('module, message', [('calendar_feed', 'Could not save the calendar'),
                                             ('drive_photos', 'Could not save the photos')])
def test_refresh_publishes_even_when_saving_a_read_blows_up(app, db_conn, web_dir, monkeypatch, caplog, module, message):
    import importlib
    from app import publish

    def broken(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(importlib.import_module(f'app.{module}'), 'sync', broken)

    results = publish.refresh(db_conn, app.config)

    assert any(result.ok is False and message in result.message for result in results)
    assert (web_dir / 'data' / 'events.json').exists()
    assert 'disk full' in caplog.text


def test_init_db_reads_the_calendar_and_says_how_it_went(paths, tmp_path):
    result = run('init_db.py', env=dict(paths, CALENDAR_ICS_URL=calendar_file(tmp_path)))

    assert result.returncode == 0, result.stderr
    assert 'Calendar:            1 events' in result.stdout


def test_init_db_without_a_calendar_link_still_works(paths):
    result = run('init_db.py', env=paths)

    assert result.returncode == 0, result.stderr
    assert 'No calendar link is set' in result.stdout


def test_init_db_reads_the_photo_folder_and_says_how_it_went(paths, tmp_path):
    result = run('init_db.py', env=dict(paths, DRIVE_FOLDER_URL=str(FIXTURES / 'sample_photos')))

    assert result.returncode == 0, result.stderr
    assert 'Photos:              4 photos' in result.stdout


def test_init_db_without_a_photo_folder_still_works(paths):
    result = run('init_db.py', env=paths)

    assert result.returncode == 0, result.stderr
    assert 'No Drive folder is set' in result.stdout


def test_publish_script_exits_nonzero_when_it_cannot_write(paths, tmp_path):
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web' / 'data').write_text('a file where the folder should be')

    result = run('publish.py', env=paths)

    assert result.returncode == 1
    assert 'Could not update the display files' in result.stderr


def test_seed_demo_makes_a_demo_calendar_and_photos(paths, tmp_path):
    result = run('seed_demo.py', env=paths)

    assert result.returncode == 0, result.stderr
    published = json.loads((tmp_path / 'web' / 'data' / 'events.json').read_text())
    photos = json.loads((tmp_path / 'web' / 'data' / 'photos.json').read_text())['photos']
    assert len(published['events']) >= 12 and published['synced_at']
    assert {'Weekly Meeting', 'Design report due', 'Testing weekend'} <= {e['title'] for e in published['events']}
    assert len(photos) == 4
    assert (tmp_path / 'data' / 'demo-calendar.ics').exists()
    # how to keep using them
    assert f"CALENDAR_ICS_URL={tmp_path / 'data' / 'demo-calendar.ics'}" in result.stdout
    assert f"DRIVE_FOLDER_URL={FIXTURES / 'sample_photos'}" in result.stdout


def test_seed_demo_can_run_again(paths, tmp_path):
    assert run('seed_demo.py', env=paths).returncode == 0

    again = run('seed_demo.py', env=dict(paths, CALENDAR_ICS_URL=str(tmp_path / 'data' / 'demo-calendar.ics'),
                                         DRIVE_FOLDER_URL=str(FIXTURES / 'sample_photos')))

    assert again.returncode == 0, again.stderr
    assert len(json.loads((tmp_path / 'web' / 'data' / 'photos.json').read_text())['photos']) == 4


@pytest.mark.parametrize('setting, value, message', [
    ('CALENDAR_ICS_URL', 'https://calendar.google.com/calendar/ical/x/basic.ics', 'already reads a calendar'),
    ('DRIVE_FOLDER_URL', 'https://drive.google.com/drive/folders/XXXXXXXXXXXX', 'already reads a photo folder'),
])
def test_seed_demo_refuses_when_a_real_calendar_or_folder_is_set(paths, tmp_path, setting, value, message):
    result = run('seed_demo.py', env=dict(paths, **{setting: value}))

    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / 'web' / 'data' / 'events.json').exists()


def test_set_password_prints_a_line_for_the_env_file():
    result = run('set_password.py', stdin='a long team password\n')

    assert result.returncode == 0, result.stderr
    key, value = result.stdout.strip().split('=', 1)
    assert key == 'ADMIN_PASSWORD_HASH'
    assert check_password_hash(value.strip("'"), 'a long team password')


def test_set_password_rejects_short_passwords():
    result = run('set_password.py', stdin='short\n')

    assert result.returncode != 0
    assert 'at least 12 characters' in result.stderr


def test_set_password_write_updates_the_file_and_adds_a_secret_key(tmp_path):
    env_file = tmp_path / 'env'
    env_file.write_text("# display settings\nDISPLAY_DB=/srv/display/data/display.db\nADMIN_PASSWORD_HASH='old'\n")

    result = run('set_password.py', '--write', str(env_file), stdin='a long team password\n')

    assert result.returncode == 0, result.stderr
    values = read_env_file(env_file)
    assert values['DISPLAY_DB'] == '/srv/display/data/display.db'
    assert check_password_hash(values['ADMIN_PASSWORD_HASH'], 'a long team password')
    assert len(values['SECRET_KEY']) == 64
    assert env_file.read_text().startswith('# display settings\n')
    assert env_file.read_text().count('ADMIN_PASSWORD_HASH=') == 1


def test_set_password_write_keeps_an_existing_secret_key(tmp_path):
    env_file = tmp_path / 'env'
    env_file.write_text('SECRET_KEY=keep-me\n')

    run('set_password.py', '--write', str(env_file), stdin='a long team password\n')

    assert read_env_file(env_file)['SECRET_KEY'] == 'keep-me'


def test_set_password_write_creates_a_private_file(tmp_path):
    env_file = tmp_path / '.env'

    run('set_password.py', '--write', str(env_file), stdin='a long team password\n')

    assert oct(env_file.stat().st_mode & 0o777) == '0o640'
    assert set(read_env_file(env_file)) == {'ADMIN_PASSWORD_HASH', 'SECRET_KEY'}


# --- Found by the stress test --------------------------------------------------------------

def test_publish_every_keeps_going_and_stays_quiet_when_it_cannot_start_or_write(paths, tmp_path):  # H4
    """A full SD card: the loop must neither die (systemd would restart it every 5 s) nor
    log a traceback per cycle. Simulated with a web folder that is a file."""
    (tmp_path / 'web').write_text('a file where the folder should be')
    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '0.2'],
                            env={**os.environ, **paths, 'CALENDAR_ICS_URL': str(FIXTURES / 'calendar_sample.ics')},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(3)
        assert loop.poll() is None, loop.communicate()[0]
    finally:
        loop.terminate()
        output = loop.communicate(timeout=10)[0]
    assert 'Traceback' not in output
    assert output.count('Could not update the display files') <= 2  # not one per 0.2 s cycle


def test_publish_every_survives_a_database_it_cannot_open_at_start(paths, tmp_path):  # H4
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'display.db').mkdir()  # SQLite can't open a folder; like a full disk at start-up
    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '0.2'],
                            env={**os.environ, **paths}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(2)
        assert loop.poll() is None, loop.communicate()[0]
    finally:
        loop.terminate()
        output = loop.communicate(timeout=10)[0]
    assert 'Traceback' not in output and 'Could not open the database' in output


def test_publish_every_picks_up_a_changed_link_from_the_env_file(paths, tmp_path):  # L8
    env_file = tmp_path / 'env'
    env_file.write_text(f"DISPLAY_DB={paths['DISPLAY_DB']}\nDISPLAY_WEB={paths['DISPLAY_WEB']}\nCALENDAR_ICS_URL={calendar_file(tmp_path)}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith('DISPLAY_') and k != 'CALENDAR_ICS_URL'}
    env['DISPLAY_ENV_FILE'] = str(env_file)
    events_file = tmp_path / 'web' / 'data' / 'events.json'
    loop = subprocess.Popen([sys.executable, str(REPO_ROOT / 'scripts' / 'publish.py'), '--every', '1'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert wait_for(lambda: events_file.exists() and json.loads(events_file.read_text())['events'])
        env_file.write_text(env_file.read_text().replace('CALENDAR_ICS_URL=', 'CALENDAR_ICS_URL=/nowhere/'))
        assert wait_for(lambda: 'Could not reach' in calendar_message(tmp_path))  # the new link is used, no restart
    finally:
        loop.terminate()
        loop.communicate(timeout=10)


@pytest.mark.parametrize('value', ['nan', 'inf', '100000', '0.01'])
def test_publish_every_rejects_numbers_that_are_not_a_sensible_interval(paths, value):  # M11
    result = run('publish.py', '--every', value, env=paths)

    assert result.returncode == 2
    assert 'seconds' in result.stderr and 'Traceback' not in result.stderr


def test_scripts_explain_a_read_only_or_missing_folder_instead_of_crashing(paths, tmp_path):  # M12
    missing = run('set_password.py', '--write', str(tmp_path / 'no' / 'such' / 'env'), stdin='twelve chars ok\n')
    assert missing.returncode == 1 and 'Traceback' not in missing.stderr and 'no/such' in missing.stderr

    read_only = tmp_path / 'ro'
    read_only.mkdir()
    read_only.chmod(0o555)
    try:
        result = run('init_db.py', env=dict(paths, DISPLAY_DB=str(read_only / 'data' / 'display.db'), DISPLAY_WEB=str(read_only / 'web')))
    finally:
        read_only.chmod(0o755)
    assert result.returncode == 1 and 'Traceback' not in result.stderr and 'Could not open the database' in result.stderr


def test_scripts_explain_a_corrupt_database_instead_of_crashing(paths, tmp_path):  # M12
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'display.db').write_bytes(b'this is not a database' * 100)

    result = run('init_db.py', env=paths)

    assert result.returncode == 1 and 'Traceback' not in result.stderr
    assert 'not a database' in result.stderr and 'TROUBLESHOOTING' in result.stderr


def test_set_password_ignores_a_windows_line_ending_and_refuses_blank_passwords():  # M13
    result = run('set_password.py', stdin='twelve chars ok\r\n')
    assert result.returncode == 0, result.stderr
    assert check_password_hash(result.stdout.strip().split('=', 1)[1].strip("'"), 'twelve chars ok')

    blank = run('set_password.py', stdin=' ' * 20 + '\n')
    assert blank.returncode != 0 and 'blank' in blank.stderr.lower()
