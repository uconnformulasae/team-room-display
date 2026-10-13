"""Login, logout, the login rate limit, and 'every admin page needs a login' (2.3.1, 2.3.2)."""
import pytest

from app import create_app
from app.auth import LoginRateLimiter
from helpers import PASSWORD, csrf_token, login

ADMIN_PAGES = ['/admin', '/admin/photos', '/admin/settings', '/admin/status']
TAILNET_CLIENT_A = {'X-Forwarded-For': '100.64.0.5, 127.0.0.1'}  # as set by tailscale serve, then nginx
TAILNET_CLIENT_B = {'X-Forwarded-For': '100.64.0.9, 127.0.0.1'}


def is_logged_in(client):
    return client.get('/admin/photos').status_code == 200


# 2.3.1
def test_wrong_password_six_times_is_rejected_and_the_sixth_is_rate_limited(client):
    responses = [login(client, 'wrong password') for _ in range(6)]

    assert [r.status_code for r in responses] == [401, 401, 401, 401, 401, 429]
    assert all('Set-Cookie' not in r.headers for r in responses)  # no session handed out
    assert not is_logged_in(client)


def test_even_the_right_password_is_refused_while_rate_limited(client):
    for _ in range(5):
        login(client, 'wrong password')

    response = login(client, PASSWORD)

    assert response.status_code == 429
    assert 'Too many wrong passwords' in response.get_data(as_text=True)
    assert not is_logged_in(client)


def test_rate_limit_is_per_client_ip_behind_tailscale_and_nginx(client):
    for _ in range(5):
        login(client, 'wrong password', headers=TAILNET_CLIENT_A)

    assert login(client, 'wrong password', headers=TAILNET_CLIENT_A).status_code == 429
    assert login(client, 'wrong password', headers=TAILNET_CLIENT_B).status_code == 401


def test_rate_limiter_forgets_failures_after_ten_minutes():
    now = [1000.0]
    limiter = LoginRateLimiter(max_failures=5, window_seconds=600, clock=lambda: now[0])
    for _ in range(5):
        limiter.record_failure('ip')
    assert limiter.is_blocked('ip')
    assert limiter.retry_after('ip') == 600

    now[0] += 601

    assert not limiter.is_blocked('ip')


def test_rate_limiter_forgets_clients_that_are_not_blocked():
    # login() asks is_blocked() for every attempt, so without this every phone that ever
    # logged in would stay in the dict for as long as the worker runs.
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    for i in range(100):
        limiter.is_blocked(f'100.64.0.{i}')
    limiter.record_failure('100.64.0.1')
    now[0] += 601
    limiter.is_blocked('100.64.0.1')

    assert limiter.failures == {}


def test_right_password_logs_in_and_lands_on_the_admin_pages(client):
    response = login(client)

    assert response.status_code == 302
    assert response.headers['Location'] == '/admin'
    assert is_logged_in(client)


def test_login_page_sends_someone_already_logged_in_on_to_the_admin_pages(client):
    login(client)

    response = client.get('/admin/login?next=/admin/photos')

    assert response.status_code == 302
    assert response.headers['Location'] == '/admin/photos'


def test_missing_password_hash_gives_a_helpful_error(app, client):
    app.config['ADMIN_PASSWORD_HASH'] = None

    response = login(client, PASSWORD)

    assert response.status_code == 401
    assert 'set_password.py' in response.get_data(as_text=True)


@pytest.mark.parametrize('broken', ['scrypt:32768:8:1', 'bcrypt$salt$hash', 'scrypt:x:8:1$salt$hash'])
def test_a_mangled_password_hash_is_reported_not_treated_as_a_wrong_password(tmp_path, monkeypatch, caplog, broken):
    # e.g. half a hash pasted into /etc/display/env by hand. Everyone would see "Wrong
    # password." (or a crash), with nothing in the log saying why.
    monkeypatch.setenv('DISPLAY_ENV_FILE', str(tmp_path / 'missing'))
    app = create_app({'TESTING': True, 'SECRET_KEY': 'x', 'ADMIN_PASSWORD_HASH': broken,
                      'DISPLAY_DB': str(tmp_path / 'd.db'), 'DISPLAY_WEB': str(tmp_path / 'web')})

    response = login(app.test_client(), PASSWORD)

    assert response.status_code == 401
    assert 'set_password.py' in response.get_data(as_text=True)
    assert 'ADMIN_PASSWORD_HASH' in caplog.text


# 2.3.2
@pytest.mark.parametrize('page', ADMIN_PAGES)
def test_admin_pages_redirect_to_login_when_logged_out(client, page):
    response = client.get(page)

    assert response.status_code == 302
    assert response.headers['Location'].startswith('/admin/login')


def test_login_returns_you_to_the_page_you_asked_for(client):
    token = csrf_token(client)
    response = client.post('/admin/login?next=/admin/photos', data={'password': PASSWORD, 'csrf_token': token})

    assert response.headers['Location'] == '/admin/photos'


@pytest.mark.parametrize('evil', ['https://evil.example/', '//evil.example/', '/\\evil.example', '/other-site',
                                  'javascript:alert(1)', '/admin\r\nX-Injected: 1', '/admin\x00'])
def test_login_never_redirects_off_the_admin_pages(client, evil):
    token = csrf_token(client)
    response = client.post('/admin/login', query_string={'next': evil},
                           data={'password': PASSWORD, 'csrf_token': token})

    assert response.headers['Location'] == '/admin'


def test_logout_ends_the_session(client):
    login(client)
    token = csrf_token(client, '/admin/settings')

    response = client.post('/admin/logout', data={'csrf_token': token})

    assert response.status_code == 302
    assert not is_logged_in(client)


def test_logout_only_accepts_post(client):
    login(client)

    assert client.get('/admin/logout').status_code == 405
    assert is_logged_in(client)


def test_session_cookie_is_httponly_and_samesite_lax_and_not_secure_over_http(client):
    cookie = login(client).headers['Set-Cookie']

    assert 'HttpOnly' in cookie
    assert 'SameSite=Lax' in cookie
    assert 'Secure' not in cookie


def test_session_cookie_is_secure_when_tailscale_says_https(client):
    https = {'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '100.64.0.5, 127.0.0.1',
             'Referer': 'https://localhost/admin/login'}
    token = csrf_token(client)

    response = client.post('/admin/login', data={'password': PASSWORD, 'csrf_token': token}, headers=https)

    assert response.status_code == 302
    assert 'Secure' in response.headers['Set-Cookie']


def test_successful_login_is_audited_with_the_tailscale_user(client, conn):
    login(client, headers={'Tailscale-User-Login': 'alice@example.com'})

    row = conn.execute("SELECT who, action FROM audit_log WHERE action = 'auth.login'").fetchone()

    assert tuple(row) == ('alice@example.com', 'auth.login')


# --- Found by the stress test --------------------------------------------------------------

def test_head_and_other_non_post_requests_are_not_login_attempts(client, conn):  # H1
    # Uptime checkers and chat link previews send HEAD. Five of them used to lock the IP out.
    codes = [client.head('/admin/login').status_code for _ in range(6)]

    assert codes == [200] * 6
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'auth.login_failed'").fetchone()[0] == 0
    assert login(client).status_code == 302  # the right password still works afterwards
