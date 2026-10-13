"""The Raspberry Pi config files in pi/ can't be run here, but they can be checked for
the mistakes that would break the Pi: a unit pointing at a script that doesn't exist,
a service listening on the network, a sudo rule that allows too much."""
import configparser
import os
import re
import signal
import subprocess
import time

import pytest

from helpers import REPO_ROOT

PI = REPO_ROOT / 'pi'
SYSTEMD = PI / 'systemd'
SERVICES = ['display-admin', 'display-publish', 'display-backup', 'display-netcheck', 'display-reboot']
TIMERS = ['display-backup', 'display-netcheck', 'display-reboot']


def unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # keys are case-sensitive in systemd
    parser.read_string((SYSTEMD / name).read_text())
    return parser


@pytest.mark.parametrize('name', SERVICES)
def test_every_service_exists_and_runs_existing_files(name):
    service = unit(f'{name}.service')['Service']
    commands = [service[key] for key in ('ExecStart', 'ExecStartPost') if key in service]

    assert commands
    for command in commands:
        for path in re.findall(r'/srv/display/(\S+)', command):
            assert (REPO_ROOT / path).exists() or path.startswith('.venv/'), f'{name}: missing {path}'
        for script in re.findall(r'(?<![\w/])(scripts/\S+)', command):
            assert (REPO_ROOT / script).exists(), f'{name}: missing {script}'


@pytest.mark.parametrize('name', ['display-admin', 'display-publish', 'display-backup'])
def test_app_services_run_as_the_locked_down_service_account(name):
    # Not "display": Raspberry Pi OS gives the login user passwordless sudo for everything,
    # so a bug in the web app would be root. "displayapp" has no password, no login and
    # only the two sudo commands in pi/sudoers-display (docs/SPEC.md §11).
    service = unit(f'{name}.service')['Service']

    assert (service['User'], service['Group']) == ('displayapp', 'displayapp')
    assert service.get('EnvironmentFile') == '/etc/display/env'


def test_admin_service_restarts_forever_and_listens_on_localhost_only():
    admin = unit('display-admin.service')

    assert admin['Service']['Restart'] == 'always'
    assert admin['Unit']['StartLimitIntervalSec'] == '0'
    assert '--bind 127.0.0.1:8000' in admin['Service']['ExecStart']
    assert admin['Service']['ExecStartPost'].startswith('-')  # a failed publish must not stop the app
    assert 'NoNewPrivileges' not in admin['Service']  # it would block the Status page's sudo buttons


def test_admin_service_gives_its_start_up_read_enough_time():
    # ExecStartPost reads the calendar and the photo folder before the app counts as
    # started. On slow Wi-Fi that can take minutes; systemd's default 90 s would then
    # fail the whole admin app.
    from app import calendar_feed, drive_photos
    slowest = (calendar_feed.TIMEOUT_S + 2 * drive_photos.TIMEOUT_S + drive_photos.DOWNLOAD_BUDGET_S
               + drive_photos.PHOTO_TIMEOUT_S)

    assert int(unit('display-admin.service')['Service']['TimeoutStartSec']) > slowest + 30


@pytest.mark.parametrize('name', TIMERS)
def test_every_timer_has_a_matching_service_and_is_installable(name):
    timer = unit(f'{name}.timer')

    assert (SYSTEMD / f'{name}.service').exists()
    assert timer['Install']['WantedBy'] == 'timers.target'
    assert any(key in timer['Timer'] for key in ('OnCalendar', 'OnUnitActiveSec'))


def test_the_calendar_is_read_every_30_seconds_by_a_service_that_never_gives_up():
    # An always-on loop, not a timer: starting Python every 30 s would cost a Pi 4 a
    # second of CPU each time. Owen asked for a cycle of 30 s to a minute (2026-09-30).
    service = unit('display-publish.service')

    assert not (SYSTEMD / 'display-publish.timer').exists()
    assert service['Service']['ExecStart'].endswith('scripts/publish.py --every 30')
    assert service['Service']['Restart'] == 'always'
    assert service['Unit']['StartLimitIntervalSec'] == '0'
    assert service['Unit']['After'] == 'network-online.target'
    assert service['Unit']['Wants'] == 'network-online.target'
    assert service['Install']['WantedBy'] == 'multi-user.target'


def test_the_new_libraries_are_pure_python_so_the_pi_needs_no_compiler():
    # Looks at the wheels installed in this .venv, so it checks what requirements.txt
    # resolved to here, not every version the pins allow.
    import importlib.metadata as metadata
    for name in ('icalendar', 'recurring-ical-events', 'python-dateutil', 'x-wr-timezone', 'tzdata'):
        compiled = [str(f) for f in metadata.distribution(name).files if str(f).endswith(('.so', '.pyd', '.dylib'))]
        assert compiled == [], f'{name} ships compiled code'
    requirements = (REPO_ROOT / 'requirements.txt').read_text()
    assert 'icalendar' in requirements and 'recurring-ical-events' in requirements


def test_nginx_listens_on_localhost_only_and_proxies_admin():
    conf = (PI / 'nginx' / 'display.conf').read_text()
    listens = re.findall(r'^\s*listen\s+([^;]+);', conf, re.MULTILINE)

    assert listens and all(value.split()[0].startswith('127.0.0.1:') for value in listens)
    assert 'root /srv/display/web;' in conf
    assert 'client_max_body_size' not in conf  # nginx's 1 MB default: nothing is uploaded any more
    assert 'proxy_pass http://127.0.0.1:8000;' in conf
    assert 'proxy_set_header Host $host;' in conf  # Flask-WTF checks the Referer against it
    assert 'proxy_set_header Tailscale-User-Login $http_tailscale_user_login;' in conf
    assert 'proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;' in conf


def test_sudoers_allows_exactly_two_commands_without_arguments():
    rules = [line for line in (PI / 'sudoers-display').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]

    assert rules == ['displayapp ALL=(root) NOPASSWD: /usr/local/bin/display-restart "", /sbin/reboot ""']


def test_setup_creates_the_service_account_and_hands_it_only_the_data_folders():
    setup = (REPO_ROOT / 'docs' / 'SETUP.md').read_text()

    assert 'adduser --system --group --no-create-home displayapp' in setup
    assert 'install -d -o displayapp -g displayapp /srv/display/data /srv/display/web/data /srv/display/web/photos' in setup
    assert 'sudo chown root:displayapp /etc/display/env' in setup
    assert 'sudo -l -U displayapp' in (REPO_ROOT / 'docs' / 'TESTING.md').read_text()


@pytest.mark.parametrize('script', ['pi/kiosk.sh', 'pi/display-restart', 'scripts/backup.sh', 'scripts/netcheck.sh'])
def test_shell_scripts_are_executable_and_parse(script):
    path = REPO_ROOT / script

    assert path.stat().st_mode & 0o111, f'{script} is not executable (chmod +x)'
    assert subprocess.run(['bash', '-n', str(path)], capture_output=True).returncode == 0


def test_kiosk_uses_the_current_chromium_command_and_flags():
    kiosk = (PI / 'kiosk.sh').read_text()

    assert re.search(r'^[^#\n]*\bchromium \\$', kiosk, re.MULTILINE)  # the command, not a comment
    assert 'chromium-browser' not in kiosk  # that name no longer exists on Raspberry Pi OS (trixie)
    for flag in ('--kiosk', '--noerrdialogs', '--incognito', '--password-store=basic'):
        assert flag in kiosk


def test_kiosk_waits_for_nginx_however_long_it_takes_then_keeps_the_browser_running(tmp_path):
    """kiosk.sh with stand-ins for curl, chromium, systemd-cat, logger and sleep: the
    browser must not open until the page answers (an error page in kiosk mode has no way
    back), and it must be opened again after it exits."""
    fakes, ready, launches = tmp_path / 'bin', tmp_path / 'nginx-is-up', tmp_path / 'launches'
    fakes.mkdir()
    stand_ins = {
        'curl': f'[ -e "{ready}" ]',                       # fails until the test says nginx is up
        'chromium': f'echo "$@" >> "{launches}"',          # "runs" and exits at once
        'systemd-cat': 'shift; exec "$@"',                 # systemd-cat --identifier=kiosk <command>
        'logger': f'echo "$@" >> "{tmp_path / "journal"}"',
        'sleep': 'exec /bin/sleep 0.01',                   # the script's waits, sped up 100×
    }
    for name, body in stand_ins.items():
        (fakes / name).write_text(f'#!/bin/sh\n{body}\n')
        (fakes / name).chmod(0o755)
    env = dict(os.environ, PATH=f"{fakes}{os.pathsep}{os.environ['PATH']}")
    kiosk = subprocess.Popen(['bash', str(PI / 'kiosk.sh')], env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(4)  # a few hundred sped-up seconds without nginx
        assert not launches.exists(), 'Chromium was opened before the page answered'
        assert 'waiting' in (tmp_path / 'journal').read_text().lower()

        ready.touch()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not launches.exists() or len(launches.read_text().splitlines()) < 2):
            time.sleep(0.05)
        started = launches.read_text().splitlines()
        assert len(started) >= 2, 'Chromium was not opened again after it exited'
        assert '--kiosk' in started[0] and 'http://localhost/' in started[0]
    finally:
        os.killpg(kiosk.pid, signal.SIGTERM)
        kiosk.wait(timeout=5)


def test_autostart_starts_the_kiosk_script():
    assert '/srv/display/pi/kiosk.sh &' in (PI / 'labwc-autostart').read_text()


def test_env_example_shows_where_the_calendar_link_goes_without_holding_one():
    text = (PI / 'env.example').read_text()

    assert '# CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/' in text
    assert 'Secret address in iCal format' in text


def test_env_example_holds_no_real_secrets():
    active = [line for line in (PI / 'env.example').read_text().splitlines()
              if line.strip() and not line.startswith('#')]

    assert all(line.split('=')[0] in ('DISPLAY_DB', 'DISPLAY_WEB') for line in active)
