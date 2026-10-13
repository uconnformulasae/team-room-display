"""The admin pages in a real browser (phone-sized), against a live copy of the app.
Skipped without Playwright, like tests/test_display.py."""
import threading

import pytest
from werkzeug.serving import make_server

from helpers import FIXTURES, PASSWORD


@pytest.fixture
def live_app(app):
    app.config['CALENDAR_ICS_URL'] = str(FIXTURES / 'calendar_sample.ics')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


@pytest.fixture
def page(browser, live_app):
    context = browser.new_context(viewport={'width': 375, 'height': 800})
    page = context.new_page()
    page.errors = []
    page.on('console', lambda message: message.type == 'error' and page.errors.append(message.text))
    page.on('pageerror', lambda error: page.errors.append(f'uncaught: {error}'))
    page.goto(live_app + '/admin/login')
    page.fill('#password', PASSWORD)
    page.click('button[type=submit]')
    page.wait_for_url(live_app + '/admin/photos')  # logging in lands on Photos
    yield page
    context.close()


def test_the_admin_pages_have_three_sections_and_no_events(page):
    links = page.eval_on_selector_all('.nav a', 'links => links.map(link => link.textContent.trim())')

    assert links == ['Photos', 'Settings', 'Status', 'View screen ↗']
    assert page.get_attribute('.nav a[aria-current=page]', 'href') == '/admin/photos'
    assert page.errors == []


def test_read_calendar_now_shows_what_it_found(page, live_app):
    page.goto(live_app + '/admin/status')
    assert 'has not been read yet' in page.text_content('.status-list')

    page.click('button:has-text("Read calendar now")')
    page.wait_for_selector('.flash-success')

    assert 'Calendar read: 17 events' in page.text_content('.flash-success')
    assert 'Formula SAE: 17 events' in ' '.join(page.text_content('.status-list').split())
    assert page.errors == []


def test_status_page_fits_a_phone(page, live_app):
    page.goto(live_app + '/admin/status')
    page.click('button:has-text("Read calendar now")')
    page.wait_for_selector('.flash-success')

    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
