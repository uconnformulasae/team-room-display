"""Flask application factory for the team display's admin pages.

Configuration comes from environment variables. On the Pi, systemd loads them from
/etc/display/env (docs/SETUP.md). For local development, put the same KEY=value lines in a
`.env` file in the repo root. A variable set in the real environment wins over the file.
"""
import logging
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory
from flask.sessions import SecureCookieSessionInterface
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, publish

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (Path('/etc/display/env'), REPO_ROOT / '.env')
CONFIG_KEYS = ('SECRET_KEY', 'ADMIN_PASSWORD_HASH', 'DISPLAY_DB', 'DISPLAY_WEB', 'CALENDAR_ICS_URL',
               'DRIVE_FOLDER_URL', 'DRIVE_API_KEY')

# Admin pages load nothing from other sites and can't be framed by them.
CONTENT_SECURITY_POLICY = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                           "form-action 'self'; frame-ancestors 'none'")

csrf = CSRFProtect()


def read_env_file(path) -> dict[str, str]:
    """Read KEY=value lines, the format of systemd's EnvironmentFile. '$' is not expanded."""
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.removeprefix('export ')  # a shell-style line still means the same thing
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config() -> dict:
    """Settings from the environment, falling back to the first env file we can read."""
    override = os.environ.get('DISPLAY_ENV_FILE')
    file_values = {}
    for path in [Path(override)] if override else ENV_FILES:
        try:
            file_values = read_env_file(path)
            break
        except OSError:  # missing, or not readable by this user
            continue
    config = {key: os.environ.get(key) or file_values.get(key) for key in CONFIG_KEYS}
    config['DISPLAY_DB'] = config['DISPLAY_DB'] or str(REPO_ROOT / 'data' / 'display.db')
    config['DISPLAY_WEB'] = config['DISPLAY_WEB'] or str(REPO_ROOT / 'web')
    return config


class SessionInterface(SecureCookieSessionInterface):
    """Mark the session cookie Secure whenever the request came in over HTTPS, which is
    every request through `tailscale serve`. Requests made on the Pi itself are plain HTTP."""

    def get_cookie_secure(self, app):
        return request.is_secure


def create_app(test_config=None) -> Flask:
    app = Flask(__name__, static_folder=None, template_folder='admin/templates')
    app.config.update(load_config())
    app.config.update(
        MAX_CONTENT_LENGTH=1024 * 1024,  # the admin forms are a few hundred bytes; nginx's limit is 1 MB too
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        WTF_CSRF_TIME_LIMIT=None,  # a form stays valid for as long as you're logged in
        TIME_SYNCED=None,  # None = ask timedatectl when publishing; tests set True/False
        SECRET_KEY_IS_TEMPORARY=False,
    )
    if test_config:
        app.config.update(test_config)

    app.logger.setLevel(logging.INFO)
    if not app.config['SECRET_KEY']:
        app.logger.warning('SECRET_KEY is not set, so logins will not survive a restart. '
                           'See docs/SETUP.md.')
        app.config['SECRET_KEY'] = secrets.token_hex(32)
        app.config['SECRET_KEY_IS_TEMPORARY'] = True

    # Requests arrive as: phone → tailscale serve → nginx → gunicorn. Trust the
    # X-Forwarded-For entry added by tailscale serve (two hops back) so the login rate
    # limit sees the phone's tailnet IP, and X-Forwarded-Proto so we know it was HTTPS.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1)
    app.session_interface = SessionInterface()
    csrf.init_app(app)
    db.init_app(app)

    from . import admin, auth
    if app.config['ADMIN_PASSWORD_HASH'] and not auth.usable_password_hash(app.config['ADMIN_PASSWORD_HASH']):
        app.logger.warning('ADMIN_PASSWORD_HASH is not a password hash this app can check, so nobody can '
                           'log in. Run scripts/set_password.py again (see docs/SETUP.md).')
        app.config['ADMIN_PASSWORD_HASH'] = None
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.extensions['login_limiter'] = auth.LoginRateLimiter()
    register_error_pages(app)
    app.after_request(add_security_headers)

    # On the Pi, nginx serves web/ itself and only sends /admin to Flask. These two routes
    # let `flask run` on a laptop show the display page (and the photos) as well.
    @app.route('/')
    @app.route('/<filename>')
    def display_file(filename='index.html'):
        return send_from_directory(app.config['DISPLAY_WEB'], filename)

    @app.route('/<any(data, photos, fonts, img):folder>/<path:filename>')
    def display_folder_file(folder, filename):
        return send_from_directory(Path(app.config['DISPLAY_WEB']) / folder, filename)

    return app


def error_page(title, message, status):
    return render_template('message.html', title=title, message=message), status


def register_error_pages(app):
    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        return error_page('Please try again',
                          'This form expired or was already sent. Go back, reload the page, and try again.', 400)

    @app.errorhandler(404)
    def not_found(error):
        return error_page('Not found', "That page doesn't exist. It may have been deleted.", 404)

    @app.errorhandler(413)
    def too_large(error):
        return error_page('Too much text', 'That was too much text for one form. Shorten it and try again.', 413)

    @app.errorhandler(sqlite3.OperationalError)
    def database_unavailable(error):
        # Two things a full or busy Pi does: "disk I/O error" (the SD card is completely
        # full, so SQLite can't even open) and "database is locked" (someone is running
        # sqlite3 on the live database by hand). Neither touches the screen.
        app.logger.error('Database unavailable: %s', error)
        if 'locked' in str(error):
            message = ('The database is busy. Is someone running sqlite3 on the Pi? Try again in a minute. '
                       'The screen is not affected.')
        else:
            message = ('The Pi cannot open its database. The SD card is probably completely full: free some '
                       'space (docs/TROUBLESHOOTING.md, "Amber badge"), then reload this page. The screen is '
                       'not affected; it keeps showing what it had.')
        return error_page('The database is not available', message, 503)

    @app.errorhandler(publish.PublishError)
    def publish_failed(error):
        return error_page('Saved, but the screen was not updated',
                          'Your change was saved, but the screen could not be updated. It will try again '
                          'by itself within a minute. If this keeps happening, check the Status page '
                          '(the SD card may be full).', 500)

    @app.errorhandler(500)
    def server_error(error):
        return error_page('Something went wrong',
                          'Something went wrong on the Pi. Try again; if it keeps happening, see '
                          'docs/TROUBLESHOOTING.md.', 500)


def add_security_headers(response):
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    # Same-origin requests keep their Referer, which Flask-WTF checks on HTTPS POSTs.
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    if request.path.startswith('/admin') and not request.path.startswith('/admin/static/'):
        response.headers['Cache-Control'] = 'no-store'  # don't keep admin pages in caches
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return response
