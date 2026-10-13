"""2.1.2 — the app starts, creates its database, and serves the login page."""
import sqlite3


def test_app_creates_database_with_schema_in_wal_mode(app):
    connection = sqlite3.connect(app.config['DISPLAY_DB'])
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    journal_mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
    connection.close()

    assert {'events', 'photos', 'settings', 'audit_log', 'calendar_sync'} <= tables
    assert journal_mode == 'wal'


def test_default_settings_are_seeded(conn):
    settings = dict(conn.execute('SELECT key, value FROM settings').fetchall())

    assert settings == {
        'competition_name': '',
        'competition_date': '',
        'competition2_name': '',
        'competition2_date': '',
        'photo_interval_s': '15',
        'font_scale': '1.0',
        'banner_text': '',
    }


def test_login_page_renders(client):
    response = client.get('/admin/login')

    assert response.status_code == 200
    assert 'name="password"' in response.get_data(as_text=True)


def test_creating_the_app_twice_on_one_database_is_safe(app, tmp_path):
    """Two gunicorn workers start at the same time and both apply the schema."""
    from app import create_app

    second = create_app({'TESTING': True, 'SECRET_KEY': 'x', 'DISPLAY_DB': app.config['DISPLAY_DB'],
                         'DISPLAY_WEB': app.config['DISPLAY_WEB']})

    assert second.test_client().get('/admin/login').status_code == 200
