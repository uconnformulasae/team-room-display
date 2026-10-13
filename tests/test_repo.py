"""2.1.3 — nothing secret or generated can be committed.

`git ls-files --cached --others --exclude-standard` lists every file git tracks plus every
file it *would* pick up with `git add -A`, so this checks .gitignore as well as the index.
"""
import re
import shutil
import subprocess

import pytest

from helpers import REPO_ROOT

FORBIDDEN = [
    (re.compile(r'(^|/)\.env'), '.env file'),
    (re.compile(r'\.db($|-)'), 'SQLite database'),
    (re.compile(r'^web/data/'), 'generated JSON'),
    (re.compile(r'^web/photos/'), 'photo copied from Drive'),
    (re.compile(r'^data/'), 'runtime data'),
]

# What real secrets look like, as opposed to the words "password"/"secret" in code.
SECRET_VALUE_PATTERNS = [
    (re.compile(r'(scrypt|pbkdf2):[^\s$]*\$[A-Za-z0-9+/=]{8,}\$[0-9a-f]{16,}'), 'password hash'),
    (re.compile(r'tskey-[A-Za-z0-9-]{10,}'), 'Tailscale auth key'),
    (re.compile(r'calendar\.google\.com/calendar/ical/[^/\s]+/private-[0-9a-f]{16,}'), 'secret calendar link'),
    (re.compile(r'AIza[0-9A-Za-z_-]{35}'), 'Google API key'),
    (re.compile(r'^\s*SECRET_KEY\s*=\s*[\'"]?[0-9a-f]{32,}', re.MULTILINE), 'SECRET_KEY value'),
]


def committable_files():
    if not shutil.which('git') or not (REPO_ROOT / '.git').exists():
        pytest.skip('not a git checkout')
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


def test_no_secrets_databases_or_generated_files_would_be_committed():
    offenders = [f'{path} ({what})' for path in committable_files()
                 for pattern, what in FORBIDDEN if pattern.search(path)]

    assert offenders == []


def test_a_real_calendar_link_would_be_caught():
    pattern = next(p for p, what in SECRET_VALUE_PATTERNS if what == 'secret calendar link')
    # Put together here, so this file itself doesn't hold anything that looks like a real link.
    real = ('https://calendar.google.com/calendar/ical/team%40group.calendar.google.com/private-'
            + '0123456789abcdef' * 2 + '/basic.ics')
    example = 'https://calendar.google.com/calendar/ical/XXXX/private-XXXX/basic.ics'

    assert pattern.search(real) and not pattern.search(example)


def test_a_real_google_api_key_would_be_caught():
    pattern = next(p for p, what in SECRET_VALUE_PATTERNS if what == 'Google API key')
    real = 'DRIVE_API_KEY=' + 'AI' + 'za' + 'Sy' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q'  # built here so this file has none

    assert pattern.search(real) and not pattern.search('DRIVE_API_KEY=XXXX')


def test_gitignore_covers_generated_and_secret_paths():
    """Create the dangerous files for real and check git ignores every one of them."""
    probes = ['.env', 'data/display.db', 'data/display.db-wal', 'web/data/events.json',
              'web/photos/a1b2c3.jpg', '.venv/bin/python']
    if not shutil.which('git') or not (REPO_ROOT / '.git').exists():
        pytest.skip('not a git checkout')
    result = subprocess.run(['git', 'check-ignore', '--no-index', *probes],
                            cwd=REPO_ROOT, capture_output=True, text=True)

    assert sorted(result.stdout.split()) == sorted(probes)


def test_no_secret_values_in_committable_files():
    hits = []
    for path in committable_files():
        full = REPO_ROOT / path
        if not full.is_file() or full.stat().st_size > 1_000_000:
            continue
        try:
            text = full.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue  # binary file (image, font)
        hits += [f'{path}: {what}' for pattern, what in SECRET_VALUE_PATTERNS if pattern.search(text)]

    assert hits == []
