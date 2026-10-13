"""Admin pages: what is left of them now that events come from Google Calendar, plus
settings, CSRF, escaping, the audit log and publishing (2.3.3 – 2.3.11)."""
import json
import logging
import os
import sqlite3
import threading

import jsonschema
import pytest

from helpers import FIXTURES, csrf_token, login, published

SCHEMA = json.loads((FIXTURES / 'events.schema.json').read_text())

SETTINGS = {'competition_name': 'FSAE Michigan', 'competition_date': '2027-05-12',
            'competition2_name': '', 'competition2_date': '', 'banner_text': '',
            'photo_interval_s': '15', 'font_scale': '1.0'}


def audit_rows(app):
    connection = sqlite3.connect(app.config['DISPLAY_DB'])
    result = connection.execute('SELECT who, action, target, detail FROM audit_log ORDER BY id').fetchall()
    connection.close()
    return result


def save(admin, **overrides):
    return admin.post('/admin/settings', data=dict(SETTINGS, **overrides))


# --- Events are no longer edited here --------------------------------------------------

def test_admin_home_goes_to_the_photos_page(admin):
    response = admin.get('/admin')

    assert response.status_code == 302 and response.headers['Location'] == '/admin/photos'


@pytest.mark.parametrize('page', ['/admin/events/new', '/admin/events/1/edit', '/admin/events/1/duplicate'])
def test_event_pages_are_gone(admin, page):
    assert admin.get(page).status_code == 404


@pytest.mark.parametrize('url', ['/admin/events/new', '/admin/events/1/delete', '/admin/series/abc/delete-future'])
def test_events_cannot_be_written_through_the_admin_site(admin, url):
    assert admin.post(url, data={'title': 'Sneaky', 'type': 'meeting', 'start_at': '2026-09-24T18:30'}).status_code == 404


def test_navigation_has_no_events_tab(admin):
    html = admin.get('/admin/photos').get_data(as_text=True)

    assert '>Events<' not in html and '>Photos<' in html and '>Status<' in html


# --- Settings ----------------------------------------------------------------------------

def test_settings_save_publish_and_audit(admin, app, web_dir):
    response = save(admin, banner_text='Shop closed Friday', photo_interval_s='20', font_scale='1.2')

    assert response.status_code == 302
    assert published(web_dir, 'settings.json') == {
        'competitions': [{'name': 'FSAE Michigan', 'date': '2027-05-12'}], 'photo_interval_s': 20,
        'font_scale': 1.2, 'banner_text': 'Shop closed Friday'}
    who, action, _, detail = audit_rows(app)[-1]
    assert action == 'settings.update' and 'competition_date' in detail


def test_settings_page_shows_current_values(admin):
    save(admin)

    html = admin.get('/admin/settings').get_data(as_text=True)

    assert 'value="FSAE Michigan"' in html


def test_two_competitions_one_for_each_car(admin, web_dir):
    response = save(admin, competition_name='Gas car: FSAE Michigan', competition_date='2027-05-12',
                    competition2_name='EV car: FSAE Michigan', competition2_date='2027-05-13')

    assert response.status_code == 302
    assert published(web_dir, 'settings.json')['competitions'] == [
        {'name': 'Gas car: FSAE Michigan', 'date': '2027-05-12'},
        {'name': 'EV car: FSAE Michigan', 'date': '2027-05-13'}]
    html = admin.get('/admin/settings').get_data(as_text=True)
    assert 'value="EV car: FSAE Michigan"' in html and 'value="2027-05-13"' in html


@pytest.mark.parametrize('field, value', [('font_scale', '5'), ('font_scale', 'big'), ('photo_interval_s', '1'),
                                          ('competition_date', '12/05/2027'), ('competition2_date', '2027-13-01'),
                                          ('competition2_name', 'x' * 61)])
def test_invalid_settings_are_rejected(admin, web_dir, field, value):
    data = dict(SETTINGS, competition_name='', competition_date='')
    data[field] = value

    response = admin.post('/admin/settings', data=data)

    assert response.status_code == 422
    assert not (web_dir / 'data' / 'settings.json').exists()


# 2.3.3
def test_post_without_csrf_token_is_rejected(admin, web_dir):
    response = admin.client.post('/admin/settings', data=dict(SETTINGS, banner_text='No token'))

    assert response.status_code == 400
    assert not (web_dir / 'data' / 'settings.json').exists()


# 2.3.7
@pytest.mark.parametrize('text', ['<script>alert(1)</script>', "'; DROP TABLE photos;--"])
def test_hostile_text_is_stored_verbatim_escaped_in_html_and_verbatim_in_json(admin, web_dir, text):
    save(admin, banner_text=text)

    html = admin.get('/admin/settings').get_data(as_text=True)
    assert text not in html
    assert text.replace('<', '&lt;').replace('>', '&gt;').replace("'", '&#39;') in html
    assert published(web_dir, 'settings.json')['banner_text'] == text


# 2.3.8
def test_every_write_regenerates_valid_json_with_a_newer_timestamp(admin, web_dir, fake_clock):
    save(admin)
    first = published(web_dir, 'events.json')
    fake_clock.advance(minutes=3)

    save(admin, banner_text='Pizza at the meeting')

    second = published(web_dir, 'events.json')
    jsonschema.validate(second, SCHEMA)
    assert second['generated_at'] > first['generated_at']
    assert published(web_dir, 'settings.json')['banner_text'] == 'Pizza at the meeting'


# 2.3.9
def test_publish_failure_keeps_the_db_change_and_old_json_and_returns_500(admin, app, web_dir, monkeypatch, caplog):
    save(admin)
    old_json = (web_dir / 'data' / 'settings.json').read_text()

    def broken_replace(src, dst):
        raise OSError('No space left on device')
    monkeypatch.setattr(os, 'replace', broken_replace)
    with caplog.at_level(logging.ERROR):
        response = save(admin, banner_text='Shop closed Friday')

    assert response.status_code == 500
    assert 'saved' in response.get_data(as_text=True)
    assert 'Shop closed Friday' in admin.get('/admin/settings').get_data(as_text=True)
    assert (web_dir / 'data' / 'settings.json').read_text() == old_json
    assert 'No space left on device' in caplog.text


# 2.3.10
def test_audit_log_records_the_tailscale_user(admin, app):
    admin.post('/admin/settings', data=dict(SETTINGS, banner_text='Hi'),
               headers={'Tailscale-User-Login': 'alice@example.com'})

    assert audit_rows(app)[-1][:2] == ('alice@example.com', 'settings.update')


def test_audit_log_says_local_without_the_header(admin, app):
    save(admin, banner_text='Hi')

    assert audit_rows(app)[-1][:2] == ('local', 'settings.update')


# 2.3.11
def test_two_concurrent_writes_are_both_saved(app, conn):
    posts = []
    for i in range(2):
        client = app.test_client()
        login(client)
        # Fetch the CSRF token up front so the two POSTs really do overlap.
        field = ('banner_text', 'Banner') if i == 0 else ('competition_name', 'FSAE Michigan')
        posts.append((client, dict({'competition_date': '', 'photo_interval_s': '15', 'font_scale': '1.0'},
                                   csrf_token=csrf_token(client, '/admin/settings'), **{field[0]: field[1]})))
    barrier = threading.Barrier(2)
    statuses = []

    def post(client, form):
        barrier.wait()
        statuses.append(client.post('/admin/settings', data=form).status_code)

    threads = [threading.Thread(target=post, args=pair) for pair in posts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [302, 302]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'settings.update'").fetchone()[0] == 2
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


# --- Headers -------------------------------------------------------------------------------

def test_admin_pages_send_security_headers(admin):
    headers = admin.get('/admin/photos').headers

    assert "default-src 'self'" in headers['Content-Security-Policy']
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['X-Frame-Options'] == 'DENY'
    assert headers['Cache-Control'] == 'no-store'


def test_hsts_only_over_https(admin):
    assert 'Strict-Transport-Security' not in admin.get('/admin/photos').headers
    https = admin.get('/admin/photos', headers={'X-Forwarded-Proto': 'https'})
    assert https.headers['Strict-Transport-Security'].startswith('max-age=')


# --- Found by the stress test --------------------------------------------------------------

def test_a_database_that_cannot_be_opened_gets_a_page_that_says_so(admin, monkeypatch):  # H3
    # On a completely full SD card SQLite can't even open (it needs a 32 KB -shm file).
    import sqlite3
    from app import db

    def cannot_open(path):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(db, 'connect', cannot_open)

    response = admin.get('/admin/photos')

    assert response.status_code == 503
    body = response.get_data(as_text=True)
    assert 'SD card' in body and 'full' in body and 'screen' in body.lower()
    assert 'Traceback' not in body


def test_a_locked_database_gets_a_page_that_says_so(admin, monkeypatch):  # M5
    import sqlite3
    from app import db

    def locked(conn):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(db, 'transaction', locked)

    response = save(admin, banner_text='Hi')

    assert response.status_code == 503
    assert 'busy' in response.get_data(as_text=True)


def test_audit_rows_cap_the_who_and_detail_columns(admin, app):  # M9
    admin.post('/admin/settings', data=dict(SETTINGS, banner_text='x' * 140),
               headers={'Tailscale-User-Login': 'a' * 10000})

    who, action, target, detail = audit_rows(app)[-1]
    assert len(who) <= 100 and len(detail) <= 300


def test_too_much_text_gets_a_message_about_text_not_photos(admin):  # M10
    response = admin.post('/admin/settings', data=dict(SETTINGS, banner_text='x' * 1_000_000))

    assert response.status_code == 413
    message = response.get_data(as_text=True).split('<main')[1]
    assert 'photo' not in message.lower() and 'too much text' in message.lower()


def test_text_size_stops_at_one_and_a_half(admin, web_dir):  # H2
    assert save(admin, font_scale='1.5').status_code == 302
    assert save(admin, font_scale='1.51').status_code == 422
    assert published(web_dir, 'settings.json')['font_scale'] == 1.5
