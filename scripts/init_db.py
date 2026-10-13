"""Create the database (if it doesn't exist yet), read the team's Google Calendar and
Drive photo folder, and write the display's JSON files.

    python scripts/init_db.py

Safe to run again at any time; it never deletes anything. (On the Pi, scripts/publish.py
--every 30 then keeps the screen up to date.) Paths, links and the key come from the
same settings as the app: DISPLAY_DB, DISPLAY_WEB, CALENDAR_ICS_URL, DRIVE_FOLDER_URL and
DRIVE_API_KEY (see pi/env.example).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import app` works

from app import create_app, db, load_config, publish  # noqa: E402


def main():
    try:
        app = create_app()  # creating the app applies app/schema.sql and the default settings
        with app.app_context():
            calendar, photos = publish.refresh(db.get_db(), app.config)  # read both, then publish
    except (sqlite3.Error, OSError) as error:
        path = load_config()['DISPLAY_DB']
        hint = (' If the SD card is fine, the file is damaged: restore it from the USB backup (docs/TROUBLESHOOTING.md).'
                if 'not a database' in str(error) or 'malformed' in str(error) else '')
        sys.exit(f'Could not open the database {path} or write the display files: {error}.{hint}')
    print(f"Database ready:      {app.config['DISPLAY_DB']}")
    print(f"Display files ready: {Path(app.config['DISPLAY_WEB']) / 'data'}")
    print(f"Calendar:            {calendar.message}")
    print(f"Photos:              {photos.message}")


if __name__ == '__main__':
    main()
