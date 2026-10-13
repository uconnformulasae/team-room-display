"""Logging in to the admin pages: one shared team password, a session cookie, and a
limit on wrong guesses."""
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import db, models
from .forms import LoginForm

bp = Blueprint('auth', __name__, url_prefix='/admin')


class LoginRateLimiter:
    """Counts wrong passwords per client IP. After 5 in 10 minutes, that IP can't log in
    until the oldest of those 5 is 10 minutes old.

    The counts live in memory, so each gunicorn worker keeps its own and a restart clears
    them. That's fine for an admin page that is only reachable over the tailnet.
    """

    def __init__(self, max_failures=5, window_seconds=600, clock=time.monotonic):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.clock = clock
        self.failures = defaultdict(deque)

    def _recent(self, key):
        """This client's failures inside the window. Older ones are dropped, and a client
        with none left is forgotten, so the dict doesn't grow with every phone that ever
        logged in."""
        failures = self.failures.get(key)
        if failures is None:
            return ()
        cutoff = self.clock() - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            del self.failures[key]
        return failures

    def is_blocked(self, key):
        return len(self._recent(key)) >= self.max_failures

    def retry_after(self, key):
        """Seconds until this client may try again (0 if it isn't blocked)."""
        failures = self._recent(key)
        if len(failures) < self.max_failures:
            return 0
        return max(0, round(failures[0] + self.window_seconds - self.clock()))

    def record_failure(self, key):
        self.failures[key].append(self.clock())

    def reset(self, key):
        self.failures.pop(key, None)


def usable_password_hash(value):
    """Whether check_password_hash() can use this ADMIN_PASSWORD_HASH. A hash pasted into
    /etc/display/env by hand can lose its tail or pick up a stray character; without this
    check that would show everyone "Wrong password." (or crash the login page) with no
    hint of why."""
    try:
        method, salt, hashval = value.split('$', 2)
        check_password_hash(value, '')  # raises ValueError for an unknown method or bad settings
    except ValueError:
        return False
    return True


def who():
    """Who is making this request, for the audit log. `tailscale serve` adds the person's
    Tailscale login as a header; requests made on the Pi itself don't have one."""
    return (request.headers.get('Tailscale-User-Login') or 'local')[:models.WHO_MAX]


def safe_next(target):
    """Where to go after logging in. Only plain paths under /admin are allowed, so a
    crafted login link can't bounce someone off to another site, and can't smuggle a
    newline or other control character into the redirect (which would fail with a 500)."""
    if target and target.startswith('/admin') and target.isascii() and target.isprintable() and '\\' not in target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            return target
    return url_for('admin.home')


def login_required():
    """Runs before every admin page (see admin/routes.py): no session, no page."""
    if request.endpoint == 'admin.static' or session.get('logged_in'):
        return None
    if request.method == 'GET':
        wanted = request.full_path if request.query_string else request.path
        return redirect(url_for('auth.login', next=wanted))
    return redirect(url_for('auth.login'))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if request.method != 'POST':  # GET, and HEAD (uptime checkers, link previews): just the page
        if session.get('logged_in'):  # e.g. a bookmarked login page
            return redirect(safe_next(request.args.get('next')))
        return render_template('login.html', form=form)

    limiter = current_app.extensions['login_limiter']
    client = request.remote_addr or 'unknown'
    if limiter.is_blocked(client):
        minutes = max(1, -(-limiter.retry_after(client) // 60))  # round up
        error = f'Too many wrong passwords. Try again in {minutes} min.'
        return render_template('login.html', form=form, error=error), 429

    password_hash = current_app.config.get('ADMIN_PASSWORD_HASH')
    if form.validate_on_submit() and password_hash and check_password_hash(password_hash, form.password.data):
        limiter.reset(client)
        session.clear()  # start a fresh session (and a fresh CSRF token) on login
        session.permanent = True
        session['logged_in'] = True
        conn = db.get_db()
        with db.transaction(conn):
            models.audit(conn, who(), 'auth.login')
        return redirect(safe_next(request.args.get('next')))

    limiter.record_failure(client)
    conn = db.get_db()
    with db.transaction(conn):
        models.audit(conn, who(), 'auth.login_failed', detail=client)
    if password_hash:
        error = 'Wrong password.'
    else:
        error = 'No admin password has been set yet. Run scripts/set_password.py (see docs/SETUP.md).'
    return render_template('login.html', form=form, error=error), 401


@bp.post('/logout')
def logout():
    session.clear()
    flash('You are logged out.', 'info')
    return redirect(url_for('auth.login'))
