"""Make a demo calendar (events relative to today) and use the sample photos, so you can
see what the screen looks like on a laptop with no Google Calendar or Drive folder.

    python scripts/seed_demo.py

The demo calendar is a file, data/demo-calendar.ics, read the same way as the real link;
the demo photo folder is fixtures/sample_photos, read the same way as the Drive folder.
The script refuses to run where a real calendar link or folder is set, so demo events
and photos can't end up on the team-room screen.
"""
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app` works

import icalendar  # noqa: E402

from app import calendar_feed, clock, create_app, db, drive_photos, models, publish  # noqa: E402

SAMPLE_PHOTOS = Path(__file__).resolve().parent.parent / 'fixtures' / 'sample_photos'


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute))


def demo_calendar(today):
    """A believable few weeks, as Google Calendar would send them."""
    thursday = today + timedelta(days=(3 - today.weekday()) % 7)
    monday = today + timedelta(days=(0 - today.weekday()) % 7 or 7)
    friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    events = [  # title, start, end, location, repeats weekly this many times
        ('Weekly Meeting', at(thursday, 18, 30), at(thursday, 20), 'Team Room', 8),
        ('Aero subteam', at(monday, 17), at(monday, 18), 'Team Room', 4),
        ('Machine shop open hours', at(today, 13), at(today, 17), 'Machine shop', 0),
        ('Design report due', at(friday, 23, 59), None, '', 0),
        ('Career fair', today + timedelta(days=7), today + timedelta(days=8), 'Student Union', 0),
        ('Testing weekend', friday + timedelta(weeks=1), friday + timedelta(weeks=1, days=3), 'Test track', 0),
        ('Team BBQ', at(friday + timedelta(weeks=1, days=1), 12), at(friday + timedelta(weeks=1, days=1), 15),
         'Picnic pavilion', 0),
        ('Mock competition', today + timedelta(days=25), today + timedelta(days=27), 'Test track', 0),
    ]
    calendar = icalendar.Calendar()
    calendar.add('prodid', '-//team-display//demo//EN')
    calendar.add('version', '2.0')
    calendar.add('x-wr-calname', 'Demo calendar')
    for number, (title, start, end, location, weeks) in enumerate(events):
        event = icalendar.Event()
        event.add('uid', f'demo-{number}@team-display')
        event.add('dtstamp', datetime(2026, 1, 1))
        event.add('summary', title)
        event.add('dtstart', start)
        if end:
            event.add('dtend', end)
        if location:
            event.add('location', location)
        if weeks:
            event.add('rrule', {'freq': 'weekly', 'count': weeks})
        calendar.add_component(event)
    return calendar.to_ical()


def main():
    app = create_app()
    demo_file = Path(app.config['DISPLAY_DB']).parent / 'demo-calendar.ics'
    calendar, folder = app.config['CALENDAR_ICS_URL'], app.config['DRIVE_FOLDER_URL']
    if calendar and Path(calendar) != demo_file:
        sys.exit('This display already reads a calendar (CALENDAR_ICS_URL is set). '
                 'The demo is for a laptop without one.')
    if folder and Path(folder) != SAMPLE_PHOTOS:
        sys.exit('This display already reads a photo folder (DRIVE_FOLDER_URL is set). '
                 'The demo is for a laptop without one.')

    with app.app_context():
        conn = db.get_db()
        today = clock.now().date()
        demo_file.write_bytes(demo_calendar(today))
        events = calendar_feed.sync(conn, str(demo_file))
        photos = drive_photos.sync(conn, str(SAMPLE_PHOTOS), None, Path(app.config['DISPLAY_WEB']) / 'photos')
        for result in (events, photos):
            if not result.ok:
                sys.exit(f'Could not read the demo data: {result.message}')
        with db.transaction(conn):
            models.save_settings(conn, {
                'competition_name': 'Gas car: FSAE Michigan', 'competition_date': date(today.year + 1, 5, 12).isoformat(),
                'competition2_name': 'EV car: FSAE Michigan', 'competition2_date': date(today.year + 1, 5, 13).isoformat()})
        publish.publish(conn, app.config['DISPLAY_WEB'], time_synced=app.config['TIME_SYNCED'])
    print(f"Demo data added to {app.config['DISPLAY_DB']} ({events.message}, {photos.message})")
    print('To keep reading the demo calendar and photos, add these lines to .env:')
    print(f'CALENDAR_ICS_URL={demo_file}')
    print(f'DRIVE_FOLDER_URL={SAMPLE_PHOTOS}')
    print('and, in a second terminal, run:  python scripts/publish.py --every 30')


if __name__ == '__main__':
    main()
