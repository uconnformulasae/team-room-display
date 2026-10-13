"""db.transaction(): all or nothing, and the real error comes through."""
import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / 'test.db')
    connection.execute('CREATE TABLE t (x INTEGER)')
    yield connection
    connection.close()


def test_a_failed_block_saves_nothing(conn):
    with pytest.raises(ValueError):
        with db.transaction(conn):
            conn.execute('INSERT INTO t VALUES (1)')
            raise ValueError('boom')

    assert conn.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 0


def test_the_real_error_comes_through_when_sqlite_already_rolled_back(conn):
    # On "database or disk is full" SQLite rolls the transaction back by itself; our own
    # ROLLBACK must not replace that error with "no transaction is active".
    with pytest.raises(ValueError, match='the real problem'):
        with db.transaction(conn):
            conn.execute('ROLLBACK')
            raise ValueError('the real problem')


def test_an_events_table_from_before_google_calendar_is_replaced(tmp_path):
    # Until 2026-09-29 events were typed into the admin pages; the table had other columns.
    connection = db.connect(tmp_path / 'old.db')
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,"
                       " start_at TEXT NOT NULL, series_id TEXT)")
    connection.execute("INSERT INTO events (title, type, start_at) VALUES ('Old meeting', 'meeting', '2026-09-24T18:30')")
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO settings VALUES ('banner_text', 'keep me')")

    db.apply_schema(connection)
    db.apply_schema(connection)  # every start runs it again

    columns = [row['name'] for row in connection.execute('PRAGMA table_info(events)')]
    assert columns == ['id', 'uid', 'title', 'start_at', 'end_at', 'all_day', 'location']
    assert connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
    assert connection.execute("SELECT value FROM settings WHERE key = 'banner_text'").fetchone()[0] == 'keep me'
    connection.close()


def test_a_photos_table_from_before_google_drive_is_replaced(tmp_path):
    # Until 2026-10-06 photos were uploaded through the admin pages. They now come from a
    # Drive folder; the old rows are dropped (their files are removed by the first read).
    connection = db.connect(tmp_path / 'old.db')
    connection.execute('CREATE TABLE photos (id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE, caption TEXT,'
                       ' enabled INTEGER NOT NULL DEFAULT 1, uploaded_at TEXT NOT NULL)')
    connection.execute("INSERT INTO photos (filename, uploaded_at) VALUES ('0123456789abcdef.jpg', 'x')")

    db.apply_schema(connection)
    db.apply_schema(connection)

    columns = [row['name'] for row in connection.execute('PRAGMA table_info(photos)')]
    assert columns == ['id', 'drive_id', 'version', 'filename', 'name', 'caption', 'problem']
    assert connection.execute('SELECT COUNT(*) FROM photos').fetchone()[0] == 0
    connection.close()


def test_a_calendar_copy_survives_a_restart(tmp_path):
    connection = db.connect(tmp_path / 'new.db')
    db.apply_schema(connection)
    connection.execute("INSERT INTO events (uid, title, start_at) VALUES ('a@t', 'Weekly Meeting', '2026-09-24T18:30')")

    db.apply_schema(connection)

    assert connection.execute('SELECT title FROM events').fetchone()[0] == 'Weekly Meeting'
    connection.close()
