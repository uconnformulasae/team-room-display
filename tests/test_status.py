"""The Status page: facts about the Pi, the audit log, and the restart/reboot buttons (2.5.4)."""
import json
import threading

import pytest

from app import status as pi_status
from helpers import FIXTURES

CALENDAR = str(FIXTURES / 'calendar_sample.ics')


class FakeCommands:
    """Stands in for status.run(): records commands and answers like a healthy Pi."""

    def __init__(self):
        self.calls = []
        self.called = threading.Event()
        self.answers = {
            'vcgencmd measure_temp': (0, "temp=48.3'C"),
            'vcgencmd get_throttled': (0, 'throttled=0x0'),
            'systemctl is-active nginx': (0, 'active'),
            'systemctl is-active display-admin': (0, 'active'),
            'systemctl is-active tailscaled': (3, 'inactive'),
            'pgrep -x chromium': (0, '1234'),
            'sudo -n /usr/local/bin/display-restart': (0, ''),
            'sudo -n /sbin/reboot': (0, ''),
        }

    def __call__(self, command, timeout=5):
        self.calls.append(' '.join(command))
        self.called.set()
        return self.answers.get(' '.join(command), (None, 'not available here'))


@pytest.fixture
def commands(monkeypatch):
    fake = FakeCommands()
    monkeypatch.setattr(pi_status, 'run', fake)
    monkeypatch.setattr(pi_status, 'read_uptime_seconds', lambda: 3 * 86400 + 4 * 3600 + 120)
    return fake


def test_status_page_shows_the_pi_at_a_glance(admin, commands, web_dir, tmp_path):
    (web_dir / 'data').mkdir()
    (web_dir / 'data' / 'events.json').write_text(json.dumps({'generated_at': '2026-09-22T14:05:00'}))
    (tmp_path / 'data' / 'last_backup.json').write_text(json.dumps(
        {'at': '2026-09-22T03:30:12', 'ok': True, 'message': 'display-2026-09-22.db and photos-2026-09-22.tar'}))

    html = admin.get('/admin/status').get_data(as_text=True)

    assert '48.3 °C' in html
    assert '3 days, 4 h' in html
    assert 'GB free' in html
    assert '2026-09-22 14:05' in html and '5 min ago' in html
    assert 'photos-2026-09-22.tar' in html
    assert 'Chromium is running' in html
    assert 'tailscaled' in html and 'inactive' in html
    assert 'No power problems' in html


def test_failed_backup_is_obvious(admin, commands, tmp_path):
    (tmp_path / 'data' / 'last_backup.json').write_text(json.dumps(
        {'at': '2026-09-22T03:30:12', 'ok': False, 'message': 'USB stick not mounted at /media/backup'}))

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Backup failed' in html and 'USB stick not mounted' in html


def test_status_page_copes_with_a_laptop(admin, monkeypatch):
    monkeypatch.setattr(pi_status, 'run', lambda command, timeout=5: (None, 'not available'))
    monkeypatch.setattr(pi_status, 'read_uptime_seconds', lambda: None)

    response = admin.get('/admin/status')

    assert response.status_code == 200
    assert 'No backup has run yet' in response.get_data(as_text=True)


@pytest.mark.parametrize('code, message', [('0x50005', 'Under-voltage'), ('0x50000', 'since boot')])
def test_power_problems_are_explained(code, message):
    assert message in pi_status.describe_throttled(code)


def test_audit_log_is_on_the_status_page(admin, commands):
    admin.post('/admin/status/read-calendar', headers={'Tailscale-User-Login': 'alice@example.com'})

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'alice@example.com' in html and 'calendar.read' in html


# --- The calendar row and its button ----------------------------------------------------

def test_status_shows_the_calendar(admin, commands, conn, app):
    app.config['CALENDAR_ICS_URL'] = CALENDAR
    admin.post('/admin/status/read-calendar')

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Formula SAE' in html and '17 events' in html and '2026-09-22 14:10' in html
    assert conn.execute('SELECT action FROM audit_log ORDER BY id DESC').fetchone()[0] == 'calendar.read'


def test_read_calendar_now_republishes(admin, commands, app, web_dir):
    app.config['CALENDAR_ICS_URL'] = CALENDAR

    response = admin.post('/admin/status/read-calendar')

    assert response.status_code == 302 and response.headers['Location'] == '/admin/status'
    published = json.loads((web_dir / 'data' / 'events.json').read_text())
    assert published['synced_at'] == '2026-09-22T14:10:00' and len(published['events']) == 17


def test_a_calendar_that_cannot_be_read_is_obvious(admin, commands, app, tmp_path):
    app.config['CALENDAR_ICS_URL'] = CALENDAR
    admin.post('/admin/status/read-calendar')
    app.config['CALENDAR_ICS_URL'] = 'http://127.0.0.1:9/calendar/ical/x/private-abc123/basic.ics'

    admin.post('/admin/status/read-calendar')
    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Could not reach the calendar' in html
    assert 'Showing the 17 events from 2026-09-22 14:10' in html
    assert 'private-abc123' not in html  # the link is a secret, even from admins


def test_status_before_any_calendar_link_is_set(admin, commands):
    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'has not been read yet' in html

    admin.post('/admin/status/read-calendar')
    assert 'No calendar link is set' in admin.get('/admin/status').get_data(as_text=True)


def test_read_calendar_needs_post_and_a_login(admin, client, commands, conn):
    assert admin.get('/admin/status/read-calendar').status_code == 405

    client.post('/admin/logout', data={'csrf_token': admin.token})
    logged_out = client.post('/admin/status/read-calendar', data={'csrf_token': admin.token})

    # Refused (the CSRF check says 400 before the login check would say 302), and nothing happened.
    assert logged_out.status_code in (400, 302)
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'calendar.read'").fetchone()[0] == 0
    assert conn.execute('SELECT checked_at FROM calendar_sync').fetchone()[0] is None


def test_restart_display_runs_the_whitelisted_command(admin, commands, conn):
    response = admin.post('/admin/status/restart-display')

    assert response.status_code == 302
    assert 'sudo -n /usr/local/bin/display-restart' in commands.calls
    assert conn.execute('SELECT action FROM audit_log ORDER BY id DESC').fetchone()[0] == 'system.restart_display'


def test_restart_display_failure_is_reported(admin, commands):
    commands.answers['sudo -n /usr/local/bin/display-restart'] = (1, 'sudo: a password is required')

    admin.post('/admin/status/restart-display')

    assert 'password is required' in admin.get('/admin/status').get_data(as_text=True)


def test_reboot_answers_first_then_reboots(app, admin, commands, conn):
    app.config['REBOOT_DELAY_S'] = 0

    response = admin.post('/admin/status/reboot')

    assert response.status_code == 202
    assert 'about 2 minutes' in response.get_data(as_text=True)
    assert commands.called.wait(5)
    assert 'sudo -n /sbin/reboot' in commands.calls
    assert conn.execute('SELECT action FROM audit_log ORDER BY id DESC').fetchone()[0] == 'system.reboot'


def test_buttons_need_post(admin, commands):
    assert admin.get('/admin/status/reboot').status_code == 405
    assert admin.get('/admin/status/restart-display').status_code == 405
    assert commands.calls == [] or 'sudo -n /sbin/reboot' not in commands.calls


def test_temporary_secret_key_warning(app, admin, commands):
    app.config['SECRET_KEY_IS_TEMPORARY'] = True

    assert 'SECRET_KEY' in admin.get('/admin/status').get_data(as_text=True)


# --- Local development: Flask can serve web/ too (nginx does this on the Pi) ---------

def test_flask_serves_the_display_files_for_local_development(client, web_dir):
    (web_dir / 'index.html').write_text('<h1>display</h1>')
    (web_dir / 'photos').mkdir()
    (web_dir / 'photos' / 'a1b2.jpg').write_bytes(b'not really a jpeg')

    assert client.get('/').get_data(as_text=True) == '<h1>display</h1>'
    assert client.get('/photos/a1b2.jpg').data == b'not really a jpeg'
    assert client.get('/../app/__init__.py').status_code == 404
    assert client.get('/photos/../../secret').status_code == 404


def test_status_page_still_shows_disk_space_when_the_database_cannot_be_opened(admin, commands, monkeypatch):  # H3
    import sqlite3
    from app import db

    def cannot_open(path):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(db, 'connect', cannot_open)

    response = admin.get('/admin/status')

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'GB free' in body and 'cannot open its database' in body
