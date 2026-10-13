"""Reading and writing the saved events, photos, settings and the audit log.

Functions take an open connection. Callers that write wrap them in db.transaction() so
the change and its audit-log row are saved together.

The events table is a copy of the team's Google Calendar (app/calendar_feed.py fills it),
and the photos table a copy of the team's Drive photo folder (app/drive_photos.py).
Event times are stored as local wall-clock strings, 'YYYY-MM-DDTHH:MM' (docs/SPEC.md §2).
All-day events store 00:00 as the time, and end_at is their LAST day (NULL for one day).
"""
import math

from . import clock

SETTING_DEFAULTS = {
    'competition_name': '',     # the first countdown (e.g. the gas car's competition)
    'competition_date': '',
    'competition2_name': '',    # the second (e.g. the EV car's); either can be left empty
    'competition2_date': '',
    'photo_interval_s': '15',
    'font_scale': '1.0',
    'banner_text': '',
}

EVENT_COLUMNS = ('uid', 'title', 'start_at', 'end_at', 'all_day', 'location')


# --- Events: a copy of the Google Calendar -----------------------------------------------

def replace_events(conn, events):
    """Make the events table hold exactly `events` (dicts from calendar_feed.parse, in
    order). Writes nothing if it already does, to spare the Pi's SD card. Returns whether
    anything changed. The caller wraps this in db.transaction()."""
    new = [(e['uid'], e['title'], e['start_at'], e['end_at'], int(e['all_day']), e['location']) for e in events]
    old = [tuple(row) for row in conn.execute(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events ORDER BY id")]
    if new == old:
        return False
    conn.execute('DELETE FROM events')
    conn.executemany(f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)", new)
    return True


def count_events(conn):
    return conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def events_since(conn, since):
    """Events whose end (or start, if they have no end) is on or after `since`, by start."""
    return conn.execute(
        'SELECT * FROM events WHERE COALESCE(end_at, start_at) >= ? ORDER BY start_at, id',
        (clock.minute(since),)).fetchall()


def calendar_status(conn):
    """How the last reads of the calendar went (the single calendar_sync row)."""
    return conn.execute('SELECT * FROM calendar_sync WHERE id = 1').fetchone()


def record_calendar_check(conn, ok, message, calendar_name=None):
    """Note a read of the calendar. A failed read keeps the time and the name of the last
    one that worked."""
    now = clock.stamp()
    if ok:
        conn.execute('UPDATE calendar_sync SET checked_at = ?, synced_at = ?, ok = 1, message = ?,'
                     ' calendar_name = ? WHERE id = 1', (now, now, message, calendar_name))
    else:
        conn.execute('UPDATE calendar_sync SET checked_at = ?, ok = 0, message = ? WHERE id = 1', (now, message))


# --- Settings ----------------------------------------------------------------------

def setting_number(text, convert, default):
    """A stored setting string as a number (convert = int, float, Decimal), or the default
    if what's stored isn't a usable number ('abc', but also 'inf' and 'nan', which JSON
    can't hold)."""
    try:
        value = convert(text)
    except (TypeError, ValueError, ArithmeticError):
        return convert(default)
    return value if math.isfinite(value) else convert(default)


def get_settings(conn):
    """All settings as strings, with defaults for any that are missing."""
    stored = dict(conn.execute('SELECT key, value FROM settings').fetchall())
    return {key: stored.get(key) if stored.get(key) is not None else default
            for key, default in SETTING_DEFAULTS.items()}


def save_settings(conn, values):
    """Save the known settings in `values`. Returns {key: (old, new)} for what changed."""
    current = get_settings(conn)
    changed = {}
    for key, value in values.items():
        if key in SETTING_DEFAULTS and current[key] != value:
            conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)'
                         ' ON CONFLICT (key) DO UPDATE SET value = excluded.value', (key, value))
            changed[key] = (current[key], value)
    return changed


# --- Photos: a copy of the Drive folder ------------------------------------------------

PHOTO_COLUMNS = ('drive_id', 'version', 'filename', 'name', 'caption', 'problem')


def replace_photos(conn, photos):
    """Make the photos table hold exactly `photos` (dicts with PHOTO_COLUMNS, in the order
    the screen shows them). Writes nothing if it already does. Returns whether anything
    changed. The caller wraps this in db.transaction()."""
    new = [tuple(p[column] for column in PHOTO_COLUMNS) for p in photos]
    old = [tuple(row) for row in conn.execute(f"SELECT {', '.join(PHOTO_COLUMNS)} FROM photos ORDER BY id")]
    if new == old:
        return False
    conn.execute('DELETE FROM photos')
    conn.executemany(f"INSERT INTO photos ({', '.join(PHOTO_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)", new)
    return True


def list_photos(conn):
    """Every file from the folder, usable or not, in the screen's order (for the admin page)."""
    return conn.execute('SELECT * FROM photos ORDER BY id').fetchall()


def photos_for_display(conn):
    """The photos the screen rotates through, in order."""
    return conn.execute('SELECT * FROM photos WHERE filename IS NOT NULL ORDER BY id').fetchall()


def drive_status(conn):
    """How the last reads of the Drive folder went (the single drive_sync row)."""
    return conn.execute('SELECT * FROM drive_sync WHERE id = 1').fetchone()


def record_drive_check(conn, ok, message, folder_name=None):
    """Note a read of the Drive folder. A failed read keeps the time and the name of the
    last one that worked."""
    now = clock.stamp()
    if ok:
        conn.execute('UPDATE drive_sync SET checked_at = ?, synced_at = ?, ok = 1, message = ?,'
                     ' folder_name = ? WHERE id = 1', (now, now, message, folder_name))
    else:
        conn.execute('UPDATE drive_sync SET checked_at = ?, ok = 0, message = ? WHERE id = 1', (now, message))


# --- Audit log ---------------------------------------------------------------------

WHO_MAX = 100     # longest Tailscale login kept in the audit log
DETAIL_MAX = 300  # longest target or detail kept (a settings change lists old and new values)


def audit(conn, who, action, target=None, detail=None):
    """Record who changed what. `who` is a Tailscale login or 'local'. Long values are cut,
    so a hostile header can't bloat the log or the Status page."""
    conn.execute('INSERT INTO audit_log (at, who, action, target, detail) VALUES (?, ?, ?, ?, ?)',
                 (clock.stamp(), who[:WHO_MAX], action, target and target[:DETAIL_MAX], detail and detail[:DETAIL_MAX]))


def recent_audit(conn, limit=50):
    return conn.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
