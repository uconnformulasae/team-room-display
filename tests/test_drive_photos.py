"""Reading the team's photos from a Google Drive folder (app/drive_photos.py), against a
fake Drive API that answers the way Google's does."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from app import drive_photos, models, photos

FOLDER = '1AbCdEfGhIjKlMnOpQrStUvWxYz012345'
LINK = f'https://drive.google.com/drive/folders/{FOLDER}?usp=sharing'
KEY = 'test-api-key-not-a-real-one'


def google_error(code, message, reason):
    """The shape of Google's error replies (both the old 'errors' list and the new 'details')."""
    return code, {'error': {'code': code, 'message': message, 'errors': [{'message': message, 'reason': reason}],
                            'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': reason}]}}


class FakeDrive:
    """What the fake server knows: one shared folder, its files (newest first), and every
    request it was sent."""

    def __init__(self):
        self.folder_name = 'Team Photos'
        self.files = []
        self.requests = []
        self.fail_folder = None     # (code, body) to answer every folder request with
        self.fail_download = {}     # file id → (code, body)
        self.more = False           # pretend the folder has more files than were asked for

    def add(self, file_id, data, name=None, description='', version=None):
        self.files.insert(0, {'id': file_id, 'name': name or f'{file_id}.jpg', 'data': data,
                              'description': description, 'md5Checksum': version or f'md5-{file_id}-{len(data)}'})

    def remove(self, file_id):
        self.files = [f for f in self.files if f['id'] != file_id]

    def downloads(self):
        return [path for path, _ in self.requests if 'alt=media' in path]


class DriveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        drive = self.server.drive
        drive.requests.append((self.path, dict(self.headers)))
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if self.headers.get('X-Goog-Api-Key') != KEY:
            return self.reply(*google_error(400, 'API key not valid. Please pass a valid API key.', 'API_KEY_INVALID'))
        if parts.path == '/drive/v3/files':
            return self.reply(200, self.listing(query))
        file_id = parts.path.removeprefix('/drive/v3/files/')
        if file_id == FOLDER:
            if drive.fail_folder:
                return self.reply(*drive.fail_folder)
            return self.reply(200, {'name': drive.folder_name, 'mimeType': 'application/vnd.google-apps.folder'})
        file = next((f for f in drive.files if f['id'] == file_id), None)
        if file is None:
            return self.reply(*google_error(404, f'File not found: {file_id}.', 'notFound'))
        if query.get('alt') == ['media']:
            if file_id in drive.fail_download:
                return self.reply(*drive.fail_download[file_id])
            self.send_response(200)
            self.send_header('Content-Length', str(len(file['data'])))
            self.end_headers()
            self.wfile.write(file['data'])
            return
        return self.reply(200, {'name': file['name'], 'mimeType': 'image/jpeg'})

    def listing(self, query):
        drive = self.server.drive
        assert query['q'] == [f"'{FOLDER}' in parents and trashed = false and mimeType contains 'image/'"]
        assert query['orderBy'] == ['createdTime desc']
        files = [{'id': f['id'], 'name': f['name'], 'description': f['description'],
                  'md5Checksum': f['md5Checksum'], 'size': str(len(f['data']))}
                 for f in drive.files[:int(query['pageSize'][0])]]
        return {'files': files, **({'nextPageToken': 'more'} if drive.more else {})}

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def drive(monkeypatch):
    server = ThreadingHTTPServer(('127.0.0.1', 0), DriveHandler)
    server.drive = FakeDrive()
    threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
    monkeypatch.setattr(drive_photos, 'API', f'http://127.0.0.1:{server.server_port}/drive/v3')
    yield server.drive
    server.shutdown()


@pytest.fixture
def photos_dir(web_dir):
    return web_dir / 'photos'


@pytest.fixture
def jpeg(images):
    return images['img_portrait_exif.jpg'].read_bytes()


def sync(db_conn, photos_dir, link=LINK, key=KEY):
    return drive_photos.sync(db_conn, link, key, photos_dir)


def saved(db_conn):
    return [dict(row) for row in models.list_photos(db_conn)]


def jpgs(folder):
    return sorted(p.name for p in folder.glob('*.jpg')) if folder.exists() else []


# --- The folder link ------------------------------------------------------------------

@pytest.mark.parametrize('link', [
    LINK,
    f'https://drive.google.com/drive/folders/{FOLDER}',
    f'https://drive.google.com/drive/u/1/folders/{FOLDER}?usp=drive_link',
    f'https://drive.google.com/open?id={FOLDER}',
    f'  {FOLDER}  ',
])
def test_folder_link_forms_people_paste(link):
    assert drive_photos.parse_folder_link(link) == (FOLDER, None)


def test_folder_link_with_a_resource_key():
    # Folders shared before 2021 have one; Drive needs it sent along.
    assert drive_photos.parse_folder_link(f'{LINK}&resourcekey=0-abcDEF_123') == (FOLDER, '0-abcDEF_123')


@pytest.mark.parametrize('link', [
    'https://example.com/drive/folders/' + FOLDER,
    'http://drive.google.com/drive/folders/' + FOLDER,
    'https://drive.google.com/file/d/xyz/view',
    "1AbC' or name contains '",  # would change the Drive query if it got through
    'short',
])
def test_links_that_are_not_drive_folders_are_refused(link):
    with pytest.raises(drive_photos.DriveError, match='DRIVE_FOLDER_URL'):
        drive_photos.parse_folder_link(link)


# --- Reading the folder ----------------------------------------------------------------

def test_new_photos_are_downloaded_converted_and_saved_in_drive_order(drive, db_conn, photos_dir, jpeg, fake_clock):
    drive.add('older', jpeg, name='IMG_0001.HEIC', description='Car reveal\nnight')
    drive.add('newer', jpeg, name='IMG_0002.jpg')

    result = sync(db_conn, photos_dir)

    assert result == drive_photos.SyncResult(True, '2 photos', True)
    rows = saved(db_conn)
    assert [(r['drive_id'], r['name'], r['caption'], r['problem']) for r in rows] == [
        ('newer', 'IMG_0002.jpg', '', None), ('older', 'IMG_0001.HEIC', 'Car reveal night', None)]
    assert jpgs(photos_dir) == sorted(r['filename'] for r in rows)
    assert all((photos_dir / 'thumbs' / r['filename']).exists() for r in rows)
    status = models.drive_status(db_conn)
    assert (status['ok'], status['message'], status['folder_name']) == (1, '2 photos', 'Team Photos')


def test_a_second_read_downloads_nothing_and_changes_nothing(drive, db_conn, photos_dir, jpeg):
    drive.add('a', jpeg)
    sync(db_conn, photos_dir)
    drive.requests.clear()

    result = sync(db_conn, photos_dir)

    assert result == drive_photos.SyncResult(True, '1 photo', False)
    assert drive.downloads() == []
    assert len(drive.requests) == 2  # the folder's name, and its list of files


def test_a_photo_taken_out_of_the_folder_leaves_with_its_files(drive, db_conn, photos_dir, jpeg):
    drive.add('keep', jpeg)
    drive.add('gone', jpeg)
    sync(db_conn, photos_dir)
    gone = next(r['filename'] for r in saved(db_conn) if r['drive_id'] == 'gone')
    drive.remove('gone')

    result = sync(db_conn, photos_dir)

    assert result.changed
    assert [r['drive_id'] for r in saved(db_conn)] == ['keep']
    assert not (photos_dir / gone).exists() and not (photos_dir / 'thumbs' / gone).exists()


def test_a_photo_replaced_in_drive_is_downloaded_again(drive, db_conn, photos_dir, jpeg, images):
    drive.add('a', jpeg)
    sync(db_conn, photos_dir)
    [before] = saved(db_conn)
    drive.files[0].update(data=images['alpha.png'].read_bytes(), md5Checksum='new-contents')

    sync(db_conn, photos_dir)

    [after] = saved(db_conn)
    assert after['filename'] != before['filename'] and after['version'] == 'new-contents'
    assert jpgs(photos_dir) == [after['filename']]


def test_a_photo_whose_copy_went_missing_is_downloaded_again(drive, db_conn, photos_dir, jpeg):
    # e.g. the database was restored from the USB stick but web/photos was not
    drive.add('a', jpeg)
    sync(db_conn, photos_dir)
    [row] = saved(db_conn)
    (photos_dir / row['filename']).unlink()

    sync(db_conn, photos_dir)

    [again] = saved(db_conn)
    assert (photos_dir / again['filename']).exists()


def test_a_file_that_is_not_a_photo_is_noted_and_not_downloaded_every_round(drive, db_conn, photos_dir, jpeg, images):
    drive.add('good', jpeg)
    drive.add('bad', images['img_evil.jpg'].read_bytes(), name='poster.jpg')

    result = sync(db_conn, photos_dir)
    drive.requests.clear()
    sync(db_conn, photos_dir)

    assert result.message == '1 photo, 1 could not be used'
    bad = next(r for r in saved(db_conn) if r['drive_id'] == 'bad')
    assert bad['filename'] is None and 'poster.jpg is not a photo we can read' in bad['problem']
    assert [p['drive_id'] for p in models.photos_for_display(db_conn)] == ['good']
    assert drive.downloads() == []  # until the file changes in Drive


def test_a_photo_over_20mb_is_noted_without_downloading_it(drive, db_conn, photos_dir, images):
    drive.add('big', images['img_21mb.jpg'].read_bytes(), name='huge.jpg')

    sync(db_conn, photos_dir)

    [row] = saved(db_conn)
    assert 'at most 20 MB' in row['problem']
    assert drive.downloads() == []


def test_a_photo_drive_will_not_hand_over_is_noted(drive, db_conn, photos_dir, jpeg):
    # The owner can turn off "Viewers can download"; Drive then refuses with 403.
    drive.add('locked', jpeg, name='locked.jpg')
    drive.fail_download['locked'] = google_error(403, 'The user has not granted the app access.', 'cannotDownloadFile')

    sync(db_conn, photos_dir)

    [row] = saved(db_conn)
    assert row['filename'] is None and row['problem'].startswith('locked.jpg: ')


def test_a_download_that_fails_on_the_way_is_tried_again_next_round(drive, db_conn, photos_dir, jpeg):
    drive.add('flaky', jpeg)
    drive.fail_download['flaky'] = (503, {'error': {'code': 503, 'message': 'Backend Error'}})

    first = sync(db_conn, photos_dir)
    del drive.fail_download['flaky']
    second = sync(db_conn, photos_dir)

    assert first.message == '0 photos, 1 still downloading'
    assert saved(db_conn)[0]['problem'] is None and second.message == '1 photo'


def test_a_big_first_batch_is_spread_over_several_rounds(drive, db_conn, photos_dir, jpeg, monkeypatch):
    # So the calendar, read in the same loop, isn't held up for minutes.
    monkeypatch.setattr(drive_photos, 'DOWNLOAD_BUDGET_S', 0)
    for i in range(3):
        drive.add(f'p{i}', jpeg)

    messages = [sync(db_conn, photos_dir).message for _ in range(3)]

    assert messages == ['1 photo, 2 still downloading', '2 photos, 1 still downloading', '3 photos']
    assert [r['drive_id'] for r in saved(db_conn)] == ['p2', 'p1', 'p0']


def test_only_the_newest_photos_are_used(drive, db_conn, photos_dir, jpeg, monkeypatch):
    monkeypatch.setattr(drive_photos, 'MAX_PHOTOS', 2)
    for i in range(2):
        drive.add(f'p{i}', jpeg)
    drive.more = True

    assert sync(db_conn, photos_dir).message == '2 photos (only the newest 2 are used)'


def test_the_api_key_goes_in_a_header_never_in_a_link(drive, db_conn, photos_dir, jpeg):
    drive.add('a', jpeg)

    sync(db_conn, photos_dir)

    assert drive.requests and all(KEY not in path for path, _ in drive.requests)
    assert all(headers['X-Goog-Api-Key'] == KEY for _, headers in drive.requests)


def test_a_resource_key_in_the_link_is_sent_along(drive, db_conn, photos_dir):
    sync(db_conn, photos_dir, link=f'{LINK}&resourcekey=0-abc')

    assert all(headers['X-Goog-Drive-Resource-Keys'] == f'{FOLDER}/0-abc' for _, headers in drive.requests)


# --- When the folder can't be read: nothing changes, and the reason is saved -----------

@pytest.mark.parametrize('fail, key, expected', [
    (None, 'wrong-key', 'DRIVE_API_KEY is not a valid API key'),
    (google_error(403, 'Google Drive API has not been used in project 1 before or it is disabled.',
                  'SERVICE_DISABLED'), KEY, 'Google Drive API is not turned on'),
    (google_error(403, 'Requests to this API drive method are blocked.', 'API_KEY_SERVICE_BLOCKED'), KEY,
     'not allowed to use the Google Drive API'),
    (google_error(404, f'File not found: {FOLDER}.', 'notFound'), KEY, 'Anyone with the link'),
    (google_error(429, 'Quota exceeded.', 'rateLimitExceeded'), KEY, 'too many requests'),
    (google_error(500, 'Internal error.', 'backendError'), KEY, 'error 500: Internal error.'),
])
def test_folder_errors_keep_the_photos_and_say_why(drive, db_conn, photos_dir, jpeg, fail, key, expected):
    drive.add('a', jpeg)
    sync(db_conn, photos_dir)
    drive.fail_folder = fail

    result = sync(db_conn, photos_dir, key=key)

    assert not result.ok and not result.changed and expected in result.message
    assert key not in result.message
    assert len(models.photos_for_display(db_conn)) == 1
    status = models.drive_status(db_conn)
    assert (status['ok'], status['message'], status['folder_name']) == (0, result.message, 'Team Photos')


def test_a_link_to_a_file_instead_of_a_folder(drive, db_conn, photos_dir, jpeg):
    drive.add('just-one-photo-id', jpeg)

    result = sync(db_conn, photos_dir, link='https://drive.google.com/drive/folders/just-one-photo-id')

    assert not result.ok and 'not a folder' in result.message


def test_no_network(db_conn, photos_dir, monkeypatch):
    monkeypatch.setattr(drive_photos, 'API', 'http://127.0.0.1:9/drive/v3')

    result = sync(db_conn, photos_dir)

    assert not result.ok and 'Could not reach Google Drive' in result.message


@pytest.mark.parametrize('link, key, expected', [
    ('', KEY, 'DRIVE_FOLDER_URL'),
    (None, KEY, 'DRIVE_FOLDER_URL'),
    (LINK, '', 'DRIVE_API_KEY'),
])
def test_missing_settings_are_named(db_conn, photos_dir, link, key, expected):
    result = sync(db_conn, photos_dir, link=link, key=key)

    assert not result.ok and expected in result.message and 'docs/SETUP.md' in result.message


# --- A folder on this computer (the demo, and a laptop without Google) ------------------

def test_a_folder_on_this_computer_works_without_a_key(db_conn, photos_dir, tmp_path, images):
    folder = tmp_path / 'demo photos'
    folder.mkdir()
    for i, name in enumerate(['older.jpg', 'newer.png', 'notes.txt']):
        (folder / name).write_bytes(images['alpha.png'].read_bytes())
        os.utime(folder / name, (1_000_000 + i, 1_000_000 + i))

    result = drive_photos.sync(db_conn, str(folder), None, photos_dir)

    assert (result.ok, result.message) == (True, '2 photos')
    assert [r['name'] for r in saved(db_conn)] == ['newer.png', 'older.jpg']
    assert models.drive_status(db_conn)['folder_name'] == 'demo photos'

    (folder / 'older.jpg').unlink()
    assert drive_photos.sync(db_conn, str(folder), None, photos_dir).changed
    assert [r['name'] for r in saved(db_conn)] == ['newer.png']


def test_a_missing_folder_on_this_computer(db_conn, photos_dir, tmp_path):
    result = drive_photos.sync(db_conn, str(tmp_path / 'nope'), None, photos_dir)

    assert not result.ok and 'no folder' in result.message


# --- Tidying up -------------------------------------------------------------------------

def test_old_photo_files_nothing_uses_are_removed_after_a_read(drive, db_conn, photos_dir, jpeg, images):
    # e.g. photos uploaded through the admin page before photos came from Drive
    stray = photos.save_photo(jpeg, 'old upload', photos_dir)
    old = time.time() - 2 * 3600
    os.utime(photos_dir / stray, (old, old))
    drive.add('a', jpeg)

    sync(db_conn, photos_dir)

    assert stray not in jpgs(photos_dir) and len(jpgs(photos_dir)) == 1


def test_descriptions_and_names_are_cleaned_up(drive, db_conn, photos_dir, jpeg):
    drive.add('a', jpeg, name='IMG\x00_1.jpg', description='  Car ‮reveal‬\n\n' + 'x' * 300)

    sync(db_conn, photos_dir)

    [row] = saved(db_conn)
    assert row['name'] == 'IMG_1.jpg'
    assert row['caption'].startswith('Car reveal xxx') and len(row['caption']) == drive_photos.MAX_CAPTION
