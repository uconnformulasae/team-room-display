"""SQLite access: one connection per request, WAL mode, explicit write transactions."""
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name('schema.sql')


def connect(path) -> sqlite3.Connection:
    """Open the database. The file is in WAL mode already (schema.sql sets that once, and
    it sticks to the file); synchronous is per connection, so it is set here."""
    # isolation_level=None: Python won't start transactions behind our back; the
    # transaction() helper below does it explicitly. timeout: wait up to 10 s for another
    # writer (e.g. the other gunicorn worker) instead of failing with "database is locked".
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA synchronous=FULL')  # a commit is on disk before we say "saved"
    return conn


@contextmanager
def transaction(conn):
    """Run a block of writes as one transaction: either all of it is saved or none of it.

    BEGIN IMMEDIATE takes the write lock up front, so two writers at the same moment
    simply queue up instead of one of them failing halfway through.
    """
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
    except BaseException:
        # SQLite may already have rolled back by itself (e.g. "database or disk is full");
        # rolling back again would hide that error behind "no transaction is active".
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise
    conn.execute('COMMIT')


def apply_schema(conn):
    """Create any missing tables and default settings. Safe to run on every start."""
    drop_events_table_from_before_google_calendar(conn)
    drop_photos_table_from_before_google_drive(conn)
    conn.executescript(SCHEMA.read_text())


def drop_events_table_from_before_google_calendar(conn):
    """Until 2026-09-29 events were typed into the admin pages and the table had other
    columns. Events now come from Google Calendar, so the old rows are not carried over."""
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(events)')}
    if columns and 'uid' not in columns:
        conn.execute('DROP TABLE IF EXISTS events')


def drop_photos_table_from_before_google_drive(conn):
    """Until 2026-10-06 photos were uploaded through the admin pages. They now come from a
    Drive folder, so the old rows are not carried over; drive_photos.sync removes their
    files (photos.remove_unused_files) the first time it reads the folder."""
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(photos)')}
    if columns and 'drive_id' not in columns:
        conn.execute('DROP TABLE IF EXISTS photos')


def get_db() -> sqlite3.Connection:
    """The database connection for the current request (opened on first use)."""
    if 'db' not in g:
        g.db = connect(current_app.config['DISPLAY_DB'])
    return g.db


def close_db(_error=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def init_app(app):
    """Once per process: make sure the folder and the tables exist (and WAL is on)."""
    Path(app.config['DISPLAY_DB']).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(app.config['DISPLAY_DB'])) as conn:
        apply_schema(conn)
    app.teardown_appcontext(close_db)
