"""Regenerate web/data/events.json, photos.json and settings.json from the database
(the events in it are a copy of the team's Google Calendar, see calendar_feed.py, and
the photos a copy of the team's Drive folder, see drive_photos.py).

The display page only ever reads these files, so it keeps working even when Flask is
down. Each file is first written to <name>.json.tmp and then renamed over the old one.
The rename is atomic, so the display never sees a half-written file, even if the power
is pulled halfway through.

publish() runs after every change made in the admin pages, when the admin service starts,
and from display-publish (scripts/publish.py --every 30) whenever a read of the calendar
or the photo folder brought a change, and every 5 minutes anyway (see docs/SPEC.md §11).
"""
import json
import logging
import os
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

import jsonschema

from . import calendar_feed, clock, db, drive_photos, models

log = logging.getLogger(__name__)

EVENTS_SCHEMA = json.loads((Path(__file__).resolve().parent.parent / 'fixtures' / 'events.schema.json').read_text())


class PublishError(Exception):
    """The database change was saved, but the display's JSON files could not be updated."""


def ntp_synced():
    """Whether the Pi's clock has been set from the internet (NTP). Where timedatectl
    doesn't exist (a laptop, the tests) we assume the clock is fine."""
    try:
        result = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    return result.stdout.strip() != 'no'


def build_events(conn, now, time_synced):
    since = datetime.combine(now.date() - timedelta(days=1), time())  # yesterday 00:00
    return {
        'generated_at': clock.stamp(now),
        'synced_at': models.calendar_status(conn)['synced_at'],  # None until the calendar has been read
        'time_synced': bool(time_synced),
        'events': [{
            'title': e['title'],
            'start': e['start_at'],
            'end': e['end_at'],
            'all_day': bool(e['all_day']),
            'location': e['location'] or '',
        } for e in models.events_since(conn, since)],
    }


def build_photos(conn, now):
    return {
        'generated_at': clock.stamp(now),
        'photos': [{'file': p['filename'], 'caption': p['caption'] or ''} for p in models.photos_for_display(conn)],
    }


def build_settings(conn):
    settings = models.get_settings(conn)
    defaults = models.SETTING_DEFAULTS
    competitions = [(settings['competition_name'], settings['competition_date']),
                    (settings['competition2_name'], settings['competition2_date'])]
    return {
        # Only the countdowns that have a date; the screen hides each one once it has passed.
        'competitions': [{'name': name, 'date': day} for name, day in competitions if day],
        'photo_interval_s': models.setting_number(settings['photo_interval_s'], int, defaults['photo_interval_s']),
        'font_scale': models.setting_number(settings['font_scale'], float, defaults['font_scale']),
        'banner_text': settings['banner_text'],
    }


def write_files(data_dir, documents):
    """Write every document to <name>.tmp first, then rename them all into place. If
    anything fails before the renames, the old files are left exactly as they were.
    Each rename is atomic, but the three together aren't: if a later rename fails, the
    earlier files are already new (docs/SPEC.md §11), and the log says which."""
    data_dir.mkdir(parents=True, exist_ok=True)
    temp_files, replaced = [], []
    try:
        for name, document in documents.items():
            temp = data_dir / f'{name}.tmp'
            temp_files.append(temp)
            with open(temp, 'w', encoding='utf-8') as f:
                # allow_nan=False: Python would write Infinity/NaN, which browsers reject.
                json.dump(document, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())  # on disk before the rename, in case the power goes
        for temp in temp_files:
            os.replace(temp, data_dir / temp.stem)  # 'events.json.tmp' → 'events.json'
            replaced.append(temp.stem)
    except OSError:
        if replaced:
            kept = [name for name in documents if name not in replaced]
            log.error('Publishing stopped partway. Already replaced: %s. Still the old copy: %s',
                      ', '.join(replaced), ', '.join(kept))
        raise
    finally:
        for temp in temp_files:
            temp.unlink(missing_ok=True)


def publish(conn, web_dir, time_synced=None):
    """Regenerate the display's JSON files. Raises PublishError if that fails."""
    now = clock.now()
    if time_synced is None:
        time_synced = ntp_synced()
    try:
        # Holding the database write lock while publishing means two publishes (say, from
        # both gunicorn workers) can't interleave and leave an older snapshot on disk.
        with db.transaction(conn):
            documents = {
                'events.json': build_events(conn, now, time_synced),
                'photos.json': build_photos(conn, now),
                'settings.json': build_settings(conn),
            }
            jsonschema.validate(documents['events.json'], EVENTS_SCHEMA)
            write_files(Path(web_dir) / 'data', documents)
    except Exception as error:
        log.error('Could not update the display files: %s', error)  # one line: this can repeat every 30 s
        raise PublishError(str(error)) from error
    log.info('Published %d events and %d photos', len(documents['events.json']['events']),
             len(documents['photos.json']['photos']))
    return documents


# --- Reading the calendar and the photo folder first: what the scripts do -------------

def read_calendar(conn, config):
    """One read of the Google Calendar, saved to the database. Never raises: a read that
    can't even be saved (the SD card is full, say) is logged and reported, so the caller
    can still publish what it has."""
    try:
        return calendar_feed.sync(conn, config['CALENDAR_ICS_URL'])  # logs a warning if it can't read
    except Exception as error:
        log.error('Could not save the calendar: %s', error)
        return calendar_feed.SyncResult(False, f'Could not save the calendar ({error}).', False)


def read_drive(conn, config):
    """One read of the Drive photo folder, saved to the database and web/photos. Never
    raises, for the same reason as read_calendar."""
    try:
        return drive_photos.sync(conn, config['DRIVE_FOLDER_URL'], config['DRIVE_API_KEY'],
                                 Path(config['DISPLAY_WEB']) / 'photos')  # logs a warning if it can't read
    except Exception as error:
        log.error('Could not save the photos: %s', error)
        return calendar_feed.SyncResult(False, f'Could not save the photos ({error}).', False)


def refresh(conn, config):
    """Read the calendar and the photo folder, then publish. Returns both reads'
    SyncResults (calendar, photos); raises PublishError if the files could not be written.
    scripts/publish.py and scripts/init_db.py use this."""
    results = read_calendar(conn, config), read_drive(conn, config)
    publish(conn, config['DISPLAY_WEB'], time_synced=config['TIME_SYNCED'])
    return results
