"""Admin page routes. Every page here requires a logged-in session (see auth.login_required).

Events and photos are not edited here: they come from the team's Google Calendar
(app/calendar_feed.py) and Drive folder (app/drive_photos.py). These pages show what the
screen has, hold what neither can (the banner, the countdown, the text size), and show
the Pi's status.

Every route that changes something does three things, in this order:
  1. writes the change and an audit-log row in one database transaction,
  2. calls publish_now() so the screen picks it up within a minute,
  3. redirects back to a page with a short confirmation message.
"""
import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import calendar_feed, db, drive_photos, models, publish
from .. import status as pi_status
from ..auth import login_required, who
from ..forms import SettingsForm

bp = Blueprint('admin', __name__, url_prefix='/admin', static_folder='static')
bp.before_request(login_required)


def publish_now():
    """Regenerate the display's JSON files. If that fails, PublishError shows the 500 page
    (the database change is already saved at this point)."""
    publish.publish(db.get_db(), current_app.config['DISPLAY_WEB'], time_synced=current_app.config['TIME_SYNCED'])


def form_status(form):
    """422 when a submitted form had errors, so it's clear that nothing was saved."""
    return 422 if form.errors else 200


# --- Home --------------------------------------------------------------------------

@bp.route('', strict_slashes=False)
def home():
    """The Photos page: what's on the screen, and the button to read the folder now."""
    return redirect(url_for('admin.photos'))


# --- Settings ----------------------------------------------------------------------

def stored_date(text):
    """A stored 'YYYY-MM-DD' as a date, or None if it's empty or not a real date."""
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def settings_form_data(settings):
    """Stored setting strings → SettingsForm values (bad stored values fall back to defaults)."""
    defaults = models.SETTING_DEFAULTS
    return {
        'competition_name': settings['competition_name'],
        'competition_date': stored_date(settings['competition_date']),
        'competition2_name': settings['competition2_name'],
        'competition2_date': stored_date(settings['competition2_date']),
        'banner_text': settings['banner_text'],
        'photo_interval_s': models.setting_number(settings['photo_interval_s'], int, defaults['photo_interval_s']),
        'font_scale': models.setting_number(settings['font_scale'], Decimal, defaults['font_scale']),
    }


@bp.route('/settings', methods=['GET', 'POST'])
def settings():
    conn = db.get_db()
    form = SettingsForm(data=settings_form_data(models.get_settings(conn)))
    if form.validate_on_submit():
        with db.transaction(conn):
            changed = models.save_settings(conn, form.values())
            if changed:
                detail = json.dumps({key: [old, new] for key, (old, new) in changed.items()}, ensure_ascii=False)
                models.audit(conn, who(), 'settings.update', 'settings', detail)
        publish_now()
        flash('Settings saved. The screen picks them up within a minute.', 'success')
        return redirect(url_for('admin.settings'))
    return render_template('settings.html', form=form), form_status(form)


# --- Photos: a copy of the team's Drive folder ----------------------------------------

def photos_dir():
    return Path(current_app.config['DISPLAY_WEB']) / 'photos'


def drive_link():
    """The folder's Drive link, for an "Open the folder" button. Team leads open it to add
    photos. A folder on this computer (the demo) is not a link anyone else can open."""
    link = current_app.config['DRIVE_FOLDER_URL'] or ''
    return link if link.startswith('https://drive.google.com/') else None


@bp.route('/photos')
def photos():
    conn = db.get_db()
    return render_template('photos.html', photos=models.list_photos(conn), drive=models.drive_status(conn),
                           folder_link=drive_link(), max_photos=drive_photos.MAX_PHOTOS)


@bp.post('/photos/read-drive')
def read_drive():
    """Read the Drive folder now, instead of waiting for the next 30-second read."""
    conn = db.get_db()
    result = drive_photos.sync(conn, current_app.config['DRIVE_FOLDER_URL'], current_app.config['DRIVE_API_KEY'],
                               photos_dir())
    with db.transaction(conn):
        models.audit(conn, who(), 'drive.read', detail=result.message[:200])
    publish_now()
    if result.ok:
        flash(f'Photos read: {result.message}. The screen picks them up within a minute.', 'success')
    else:
        flash(f'The photo folder could not be read. {result.message}', 'error')
    return redirect(url_for('admin.photos'))


# --- Status ------------------------------------------------------------------------

@bp.route('/status')
def status():
    """The Pi's health. Shown even when the database can't be opened (a completely full
    SD card does that), because this is the page that says the card is full."""
    try:
        conn = db.get_db()
        database = dict(calendar=models.calendar_status(conn), event_count=models.count_events(conn),
                        drive=models.drive_status(conn), photos=models.list_photos(conn),
                        audit=models.recent_audit(conn, limit=50), database_error=None)
    except sqlite3.OperationalError as error:
        current_app.logger.error('Status page without a database: %s', error)
        database = dict(calendar=None, event_count=None, drive=None, photos=[], audit=[], database_error=str(error))
    return render_template('status.html', info=pi_status.gather(current_app.config), **database)


@bp.post('/status/read-calendar')
def read_calendar():
    """Read the Google Calendar now, instead of waiting for the next 30-second read."""
    conn = db.get_db()
    result = calendar_feed.sync(conn, current_app.config['CALENDAR_ICS_URL'])
    with db.transaction(conn):
        models.audit(conn, who(), 'calendar.read', detail=result.message[:200])
    publish_now()
    if result.ok:
        flash(f'Calendar read: {result.message}. The screen picks them up within a minute.', 'success')
    else:
        flash(f'The calendar could not be read. {result.message}', 'error')
    return redirect(url_for('admin.status'))


@bp.post('/status/restart-display')
def restart_display():
    ok, output = pi_status.restart_display()
    conn = db.get_db()
    with db.transaction(conn):
        models.audit(conn, who(), 'system.restart_display', detail=None if ok else output[:200])
    if ok:
        flash('The screen is restarting. It should be back within 15 seconds.', 'success')
    else:
        flash(f'Could not restart the screen: {output}', 'error')
    return redirect(url_for('admin.status'))


@bp.post('/status/reboot')
def reboot():
    conn = db.get_db()
    with db.transaction(conn):
        models.audit(conn, who(), 'system.reboot')
    pi_status.reboot_soon(current_app.config.get('REBOOT_DELAY_S', 2))
    return render_template('message.html', title='Rebooting the Pi',
                           message='The screen and these admin pages will be back in about 2 minutes. '
                                   'Reload this page then.'), 202
