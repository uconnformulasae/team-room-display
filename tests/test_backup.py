"""scripts/backup.sh: nightly copy of the database and photos to the USB stick (3.2.4, 3.2.6).

`mountpoint` (Linux only) is replaced by a tiny stand-in so this runs on any machine
with bash and the sqlite3 command-line tool."""
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from helpers import REPO_ROOT

BACKUP_SH = REPO_ROOT / 'scripts' / 'backup.sh'

pytestmark = pytest.mark.skipif(not (shutil.which('bash') and shutil.which('sqlite3')),
                                reason='needs bash and the sqlite3 command-line tool')


@pytest.fixture
def pi(tmp_path):
    """A fake /srv/display with a database and two photos, a fake USB stick, and a fake
    `mountpoint` command whose answer the test controls."""
    data, web, usb, bin_dir = (tmp_path / name for name in ('data', 'web', 'usb', 'bin'))
    for folder in (data, web / 'photos', usb, bin_dir):
        folder.mkdir(parents=True)
    with sqlite3.connect(data / 'display.db') as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE events (title TEXT)')
        conn.execute("INSERT INTO events VALUES ('Weekly Meeting')")
    (web / 'photos' / 'a1.jpg').write_bytes(b'photo one')
    (web / 'photos' / 'a2.jpg').write_bytes(b'photo two')
    (bin_dir / 'mountpoint').write_text('#!/bin/sh\nexit "${FAKE_MOUNTED_STATUS:-0}"\n')
    (bin_dir / 'mountpoint').chmod(0o755)
    env = dict(os.environ, DISPLAY_DB=str(data / 'display.db'), DISPLAY_WEB=str(web), BACKUP_DIR=str(usb),
               PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return {'data': data, 'usb': usb, 'env': env}


def run_backup(pi, **extra_env):
    return subprocess.run(['bash', str(BACKUP_SH)], capture_output=True, text=True, timeout=60,
                          env=dict(pi['env'], **extra_env))


def status(pi):
    return json.loads((pi['data'] / 'last_backup.json').read_text())


def test_backup_copies_database_and_photos_and_reports_success(pi):
    result = run_backup(pi)

    assert result.returncode == 0, result.stderr
    [db_copy] = pi['usb'].glob('display-*.db')
    [tarball] = pi['usb'].glob('photos-*.tar')
    with sqlite3.connect(db_copy) as conn:
        assert conn.execute('SELECT title FROM events').fetchall() == [('Weekly Meeting',)]
    listing = subprocess.run(['tar', '-tf', str(tarball)], capture_output=True, text=True).stdout
    assert 'photos/a1.jpg' in listing and 'photos/a2.jpg' in listing
    assert status(pi)['ok'] is True
    assert db_copy.name in status(pi)['message']
    assert sorted(p.name for p in pi['usb'].iterdir()) == sorted([db_copy.name, tarball.name])  # no -wal/-shm


def test_backup_keeps_only_the_newest_fourteen(pi):
    for day in range(1, 21):
        (pi['usb'] / f'display-2026-08-{day:02}.db').write_text('old')
        (pi['usb'] / f'photos-2026-08-{day:02}.tar').write_text('old')
        os.utime(pi['usb'] / f'display-2026-08-{day:02}.db', (day * 86400, day * 86400))
        os.utime(pi['usb'] / f'photos-2026-08-{day:02}.tar', (day * 86400, day * 86400))

    assert run_backup(pi).returncode == 0

    databases = sorted(p.name for p in pi['usb'].glob('display-*.db'))
    assert len(databases) == 14  # tonight's, plus Aug 8-20
    assert 'display-2026-08-20.db' in databases and 'display-2026-08-08.db' in databases
    assert 'display-2026-08-07.db' not in databases
    assert len(list(pi['usb'].glob('photos-*.tar'))) == 14


# 3.2.6
def test_missing_usb_stick_fails_loudly(pi):
    result = run_backup(pi, FAKE_MOUNTED_STATUS='1')

    assert result.returncode != 0
    assert 'BACKUP FAILED' in result.stderr and 'not mounted' in result.stderr
    assert status(pi)['ok'] is False
    assert 'not mounted' in status(pi)['message']
    assert list(pi['usb'].iterdir()) == []


def test_a_failure_while_pruning_is_reported_not_left_as_ok(pi):
    (pi['data'] / 'last_backup.json').write_text(json.dumps({'at': 'yesterday', 'ok': True, 'message': 'fine'}))
    for day in range(1, 15):
        (pi['usb'] / f'display-2026-08-{day:02}.db').write_text('old')
        os.utime(pi['usb'] / f'display-2026-08-{day:02}.db', (86400 * (day + 10),) * 2)
    stuck = pi['usb'] / 'display-2026-07-01.db'
    stuck.mkdir()  # the oldest "backup" is a folder, so deleting it fails
    os.utime(stuck, (86400, 86400))

    result = run_backup(pi)

    assert result.returncode != 0
    assert status(pi)['ok'] is False


def test_a_failed_backup_leaves_no_half_written_files(pi):
    unreadable = pi['env']['DISPLAY_WEB'] + '/photos/a1.jpg'
    os.chmod(unreadable, 0)  # tar can't read it, so the photo step fails
    try:
        result = run_backup(pi)
    finally:
        os.chmod(unreadable, 0o644)

    assert result.returncode != 0
    assert status(pi)['ok'] is False
    assert list(pi['usb'].iterdir()) == []  # tonight's partial database copy was removed too


def test_first_backup_works_before_any_photo_was_uploaded(pi):
    shutil.rmtree(pi['env']['DISPLAY_WEB'] + '/photos')

    result = run_backup(pi)

    assert result.returncode == 0, result.stderr
    assert status(pi)['ok'] is True


def test_a_failure_is_recorded_even_when_the_data_folder_is_missing(pi, tmp_path):  # L6
    result = run_backup(pi, DISPLAY_DB=str(tmp_path / 'nowhere' / 'display.db'))

    assert result.returncode != 0
    assert json.loads((tmp_path / 'nowhere' / 'last_backup.json').read_text())['ok'] is False


def test_backup_flushes_only_the_stick_not_every_disk():  # M6
    script = BACKUP_SH.read_text()
    assert 'sync -f "$DEST"' in script  # plain `sync` stalls every write on the Pi for seconds
