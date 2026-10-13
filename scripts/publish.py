"""Read the team's Google Calendar and Drive photo folder, then regenerate
web/data/events.json, photos.json and settings.json.

    python scripts/publish.py               read once, publish, exit
    python scripts/publish.py --every 30    keep running: read every 30 seconds

The Pi runs the second form as display-publish.service, so an event added to (or removed
from) the calendar, or a photo put in (or taken out of) the folder, reaches the screen
within about a minute with nobody doing anything. The screen's files are rewritten when
something changed, and every 5 minutes anyway as a heartbeat (the screen warns when its
files are over an hour old). The calendar link, the folder link and the API key are
re-read from the env file every round, so changing them needs no restart.

If the calendar or the folder can't be read (no network, say), the files are still
written from what was saved last time; the reason is logged and shown on the admin
Status page. The
once-only form exits with status 1 if the files could not be written, so that failure
shows up in `journalctl -u display-admin` (which runs it at start). The loop never
exits: whatever goes wrong (a full SD card, a database it can't open) it logs once and
tries again next round.
"""
import argparse
import logging
import math
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app` works

from app import create_app, db, load_config, publish  # noqa: E402

log = logging.getLogger('publish')
HEARTBEAT_S = 5 * 60   # rewrite the files at least this often, even when nothing changed
QUIET_REPEATS_S = 600  # the same log line again within this many seconds is dropped


class QuietRepeats(logging.Filter):
    """Let a message through once, then drop repeats of it for a while. While the SD card
    is full, every round would otherwise log the same complaint (2,880 times a day)."""

    def __init__(self, window_s=QUIET_REPEATS_S):
        super().__init__()
        self.window_s = window_s
        self.last_seen = {}

    def filter(self, record):
        key = (record.name, record.levelno, record.getMessage())
        now = time.monotonic()
        if key in self.last_seen and now - self.last_seen[key] < self.window_s:
            return False
        self.last_seen[key] = now
        return True


def explain(error, config):
    """A one-line explanation of a database or file error, for people, not tracebacks."""
    text = str(error)
    if 'not a database' in text or 'malformed' in text:
        return (f"Could not open the database {config['DISPLAY_DB']}: {text}. If the SD card is fine, the file is "
                'damaged: restore it from the USB backup (docs/TROUBLESHOOTING.md).')
    return f"Could not open the database {config['DISPLAY_DB']} or write the display files: {text}"


def run_once(app, conn):
    try:
        publish.refresh(conn, app.config)
    except publish.PublishError:
        return 1  # the details were already logged
    return 0


def run_forever(every):
    """Read and publish, every `every` seconds, forever. Nothing that goes wrong ends the
    loop; systemd would only start it again."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    quiet = QuietRepeats()
    for handler in logging.getLogger().handlers:
        handler.addFilter(quiet)
    print(f'Reading the calendar and the photo folder every {every:g} s', flush=True)
    app = None
    last_published = None
    while True:
        started = time.monotonic()
        try:
            if app is None:
                app = create_app()  # its log lines reach the root handler above, filter included
            with app.app_context():
                links = {key: value for key, value in load_config().items()
                         if key in ('CALENDAR_ICS_URL', 'DRIVE_FOLDER_URL', 'DRIVE_API_KEY')}
                config = dict(app.config, **links)
                calendar = publish.read_calendar(db.get_db(), config)
                photos = publish.read_drive(db.get_db(), config)
                if calendar.changed or photos.changed or last_published is None or started - last_published >= HEARTBEAT_S:
                    try:
                        publish.publish(db.get_db(), config['DISPLAY_WEB'], time_synced=config['TIME_SYNCED'])
                        last_published = started
                    except publish.PublishError:
                        pass  # already logged; the next round tries again
        except (sqlite3.Error, OSError) as error:  # a full SD card, a damaged database, ...
            log.error('%s. Trying again in %g s.', explain(error, app.config if app else load_config()), every)
        time.sleep(max(0.0, every - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description="Read the team's calendar and photo folder and rewrite the "
                                                 "screen's data files.")
    parser.add_argument('--every', type=float, metavar='SECONDS',
                        help='keep running, reading them every SECONDS seconds (how the Pi runs it)')
    args = parser.parse_args()
    if args.every is not None and not (math.isfinite(args.every) and 0.1 <= args.every <= 3600):
        parser.error('--every needs a number of seconds between 0.1 and 3600')

    if args.every:
        run_forever(args.every)
    try:
        app = create_app()
        with app.app_context():
            return run_once(app, db.get_db())
    except (sqlite3.Error, OSError) as error:
        sys.exit(explain(error, load_config()))


if __name__ == '__main__':
    sys.exit(main())
