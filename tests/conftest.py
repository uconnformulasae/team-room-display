"""Shared test fixtures: a throwaway database and web/ folder, a frozen clock, a
logged-in test client that adds the CSRF token to every POST for you, and (for the
browser tests) the display page served from a copy of web/ with a controllable clock."""
import functools
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest
from werkzeug.security import generate_password_hash

from app import clock, create_app, db
from helpers import FIXED_NOW, FIXTURES, PASSWORD, REPO_ROOT, AdminClient, FakeClock, login, make_test_images

NEW_YORK = ZoneInfo('America/New_York')
FIXED = datetime(2026, 9, 22, 14, 10, tzinfo=NEW_YORK)  # FIXED_NOW, as the browser sees it
LANDSCAPE = {'width': 1920, 'height': 1080}


@pytest.fixture(scope='session')
def images(tmp_path_factory):
    """Paths to the test images (img_12mp.jpg, img_portrait_exif.jpg, ...), made once per run."""
    return make_test_images(tmp_path_factory.mktemp('images'))


@pytest.fixture
def fake_clock(monkeypatch):
    fake = FakeClock(FIXED_NOW)
    monkeypatch.setattr(clock, 'now', fake.now)
    return fake


@pytest.fixture
def web_dir(tmp_path):
    path = tmp_path / 'web'
    path.mkdir()
    return path


@pytest.fixture
def app(tmp_path, web_dir, fake_clock, monkeypatch):
    # Ignore any real /etc/display/env or .env on the machine running the tests.
    monkeypatch.setenv('DISPLAY_ENV_FILE', str(tmp_path / 'no-such-env-file'))
    return create_app({
        'TESTING': True,
        'SECRET_KEY': 'test-secret-key',
        # A deliberately cheap hash so the test suite stays fast.
        'ADMIN_PASSWORD_HASH': generate_password_hash(PASSWORD, method='pbkdf2:sha256:1000'),
        'DISPLAY_DB': str(tmp_path / 'data' / 'display.db'),
        'DISPLAY_WEB': str(web_dir),
        'TIME_SYNCED': True,
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    """A plain sqlite3 connection to the test database, for checking what the app wrote."""
    connection = sqlite3.connect(app.config['DISPLAY_DB'])
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def db_conn(app):
    """A connection like the app's own (app.db.connect), for calling models/publish/sync
    functions directly."""
    connection = db.connect(app.config['DISPLAY_DB'])
    yield connection
    connection.close()


@pytest.fixture
def admin(client):
    response = login(client)
    assert response.status_code == 302, 'test login failed'
    return AdminClient(client)


@pytest.fixture(scope='session')
def browser():
    """Headless Chromium for the browser tests. Needs `pip install playwright` and
    `playwright install chromium` (or PLAYWRIGHT_CHROMIUM=/path/to/chrome); skipped otherwise."""
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM') or None)
        except sync_api.Error as error:
            pytest.skip(f'no browser for Playwright: {error.message.splitlines()[0]}')
        yield chromium
        chromium.close()


# --- The display page in a browser (tests/test_display*.py) --------------------------------

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Site:
    """A copy of web/ served over HTTP, with fixture files as its data/ folder."""

    def __init__(self, root, url):
        self.root, self.url = root, url

    def use(self, events='events_normal.json', settings=None, photos='photos.json'):
        data = self.root / 'data'
        shutil.copy(FIXTURES / events, data / 'events.json')
        shutil.copy(FIXTURES / photos, data / 'photos.json')
        values = json.loads((FIXTURES / 'settings.json').read_text())
        values.update(settings or {})
        (data / 'settings.json').write_text(json.dumps(values))

    def write(self, name, document):
        (self.root / 'data' / name).write_text(json.dumps(document))


@pytest.fixture(scope='session')
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp('site')
    shutil.copytree(REPO_ROOT / 'web', root, dirs_exist_ok=True, ignore=shutil.ignore_patterns('data', 'photos'))
    (root / 'data').mkdir()
    shutil.copytree(FIXTURES / 'sample_photos', root / 'photos')
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield Site(root, f'http://127.0.0.1:{server.server_port}/')
    server.shutdown()


class Screen:
    """One browser tab showing the display, with a controllable clock."""

    def __init__(self, page, site):
        self.page, self.site = page, site
        self.errors = []    # console errors and uncaught exceptions
        self.requests = []  # every URL the page asked for
        page.on('console', lambda message: message.type == 'error' and self.errors.append(message.text))
        page.on('pageerror', lambda error: self.errors.append(f'uncaught: {error}'))
        page.on('request', lambda request: self.requests.append(request.url))

    def open(self, at=FIXED, **data):
        self.site.use(**data)
        self.page.clock.install(time=at)
        self.page.goto(self.site.url)
        self.wait_until("document.getElementById('status-dot').dataset.state !== 'waiting'")  # data loaded
        self.page.evaluate('document.fonts.ready')

    def advance(self, **duration):
        self.page.clock.run_for(int(timedelta(**duration).total_seconds() * 1000))

    def wait_until(self, js_condition, timeout=None):
        # Passed as a function, not a bare expression: the page's Content-Security-Policy
        # forbids eval(), which is how Playwright would run a bare expression.
        self.page.wait_for_function(f'() => {js_condition}', timeout=timeout)

    def text(self, element_id):
        return self.page.text_content(f'#{element_id}').strip()

    def shows(self, element_id):
        return self.page.is_visible(f'#{element_id}')

    def rows(self):
        return self.page.eval_on_selector_all(
            '#agenda > li', 'rows => rows.map(row => [...row.children].map(cell => cell.textContent))')

    def call(self, expression, *args):
        """Evaluate a function from app.js, e.g. call('relativeLabel(readEvent(a), parseLocal(b))', ev, now)."""
        return self.page.evaluate(f'([a, b]) => {expression}', list(args) + [None] * (2 - len(args)))

    def overflow_report(self):
        """Anything sticking out of the screen, or list rows sticking out of their box."""
        return self.page.evaluate('''() => {
          const problems = [];
          const root = document.documentElement;
          if (root.scrollWidth > innerWidth || root.scrollHeight > innerHeight) problems.push('page scrolls');
          for (const region of document.querySelectorAll('.top, .hero, .upcoming, .photos, .bottom')) {
            const box = region.getBoundingClientRect();
            if (region.hidden || !box.height) continue;
            if (box.left < 0 || box.top < 0 || box.right > innerWidth + 0.5 || box.bottom > innerHeight + 0.5)
              problems.push(region.className + ' off screen');
            if (region.scrollWidth > region.clientWidth + 1) problems.push(region.className + ' too wide');
          }
          const list = document.getElementById('upcoming').getBoundingClientRect();
          for (const item of document.querySelectorAll('#agenda > li, #agenda-more:not([hidden])'))
            if (item.getBoundingClientRect().bottom > list.bottom + 0.5) problems.push('list row cut off');
          return problems;
        }''')

    def boxes_overlap(self, first, second):
        return self.page.evaluate('''([a, b]) => {
          const x = document.querySelector(a).getBoundingClientRect(), y = document.querySelector(b).getBoundingClientRect();
          return x.left < y.right && y.left < x.right && x.top < y.bottom && y.top < x.bottom;
        }''', [first, second])


@pytest.fixture
def screen(browser, site):
    context = browser.new_context(viewport=LANDSCAPE, timezone_id='America/New_York')
    yield Screen(context.new_page(), site)
    context.close()
