"""The admin Photos page, now that photos come from a Google Drive folder: it shows what
is on the screen and why a file isn't, and reads the folder on demand. Nothing is
uploaded or edited here any more (decisions/2026-10-06-google-drive-photos-design.md)."""
import json

import pytest

from helpers import FIXTURES

DRIVE_LINK = 'https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing'


@pytest.fixture
def folder(app, tmp_path, images):
    """A photo folder on this computer, read the same way as the Drive folder."""
    path = tmp_path / 'Team Photos'
    path.mkdir()
    (path / 'reveal.jpg').write_bytes(images['img_portrait_exif.jpg'].read_bytes())
    (path / 'poster.png').write_bytes(images['img_evil.jpg'].read_bytes())  # not really a picture
    app.config['DRIVE_FOLDER_URL'] = str(path)
    return path


def test_read_drive_now_saves_publishes_and_audits(admin, conn, web_dir, folder):
    response = admin.post('/admin/photos/read-drive', headers={'Tailscale-User-Login': 'alice@example.com'})

    assert response.status_code == 302 and response.headers['Location'] == '/admin/photos'
    published = json.loads((web_dir / 'data' / 'photos.json').read_text())['photos']
    assert len(published) == 1
    audit = conn.execute('SELECT who, action, detail FROM audit_log ORDER BY id DESC').fetchone()
    assert tuple(audit) == ('alice@example.com', 'drive.read', '1 photo, 1 could not be used')
    assert 'Photos read: 1 photo, 1 could not be used' in admin.get('/admin/photos').get_data(as_text=True)


def test_photos_page_shows_the_screen_and_the_files_it_could_not_use(admin, conn, folder):
    admin.post('/admin/photos/read-drive')
    filename = conn.execute('SELECT filename FROM photos WHERE filename IS NOT NULL').fetchone()[0]

    html = admin.get('/admin/photos').get_data(as_text=True)

    assert f'/photos/thumbs/{filename}' in html and 'reveal.jpg' in html
    assert 'poster.png' in html and 'not a photo we can read' in html
    assert 'Team Photos' in html and '2026-09-22 14:10' in html
    assert 'Read Drive now' in html


def test_photos_page_has_nothing_to_upload_or_edit(admin, folder):
    admin.post('/admin/photos/read-drive')

    html = admin.get('/admin/photos').get_data(as_text=True)

    assert 'type="file"' not in html and 'Caption' not in html and 'Delete' not in html


@pytest.mark.parametrize('url', ['/admin/photos/upload', '/admin/photos/1/delete', '/admin/photos/1/toggle',
                                 '/admin/photos/1/caption', f'/admin/photos/{2 ** 64}/delete'])
def test_the_old_upload_and_edit_addresses_are_gone(admin, url):
    assert admin.post(url).status_code == 404


def test_a_folder_that_cannot_be_read_is_obvious_and_the_photos_stay(admin, app, web_dir, folder, tmp_path):
    admin.post('/admin/photos/read-drive')
    app.config['DRIVE_FOLDER_URL'] = str(tmp_path / 'gone')

    admin.post('/admin/photos/read-drive')
    html = admin.get('/admin/photos').get_data(as_text=True)

    assert 'The photo folder could not be read. There is no folder at gone.' in html
    assert 'Not read' in html and 'Showing the 1 photo from 2026-09-22 14:10' in html
    assert len(json.loads((web_dir / 'data' / 'photos.json').read_text())['photos']) == 1


def test_photos_page_before_any_folder_is_set(admin):
    html = admin.get('/admin/photos').get_data(as_text=True)
    assert 'has not been read yet' in html

    admin.post('/admin/photos/read-drive')
    assert 'No Drive folder is set' in admin.get('/admin/photos').get_data(as_text=True)


def test_the_folder_link_is_offered_but_never_the_api_key(admin, app, commands):
    app.config.update(DRIVE_FOLDER_URL=DRIVE_LINK, DRIVE_API_KEY='the-api-key-123')
    admin.post('/admin/photos/read-drive')  # no network in the tests: the read fails, that's fine

    photos_page = admin.get('/admin/photos').get_data(as_text=True)
    status_page = admin.get('/admin/status').get_data(as_text=True)

    assert f'href="{DRIVE_LINK}"' in photos_page.replace('&amp;', '&')  # team leads open it to add photos
    assert 'the-api-key-123' not in photos_page + status_page


def test_a_folder_on_this_computer_is_not_offered_as_a_link(admin, folder):
    assert str(folder) not in admin.get('/admin/photos').get_data(as_text=True)


def test_read_drive_needs_post_and_a_login(admin, client, conn, folder):
    assert admin.get('/admin/photos/read-drive').status_code == 405

    client.post('/admin/logout', data={'csrf_token': admin.token})
    logged_out = client.post('/admin/photos/read-drive', data={'csrf_token': admin.token})

    assert logged_out.status_code in (400, 302)
    assert conn.execute('SELECT checked_at FROM drive_sync').fetchone()[0] is None


def test_status_page_has_a_photos_row(admin, commands, folder):
    admin.post('/admin/photos/read-drive')

    html = admin.get('/admin/status').get_data(as_text=True)

    assert 'Team Photos: 1 photo, 1 could not be used' in html


@pytest.fixture
def commands(monkeypatch):
    """The Status page asks the Pi for facts; on a laptop, answer 'not available'."""
    from app import status as pi_status
    monkeypatch.setattr(pi_status, 'run', lambda command, timeout=5: (None, 'not available here'))
    monkeypatch.setattr(pi_status, 'read_uptime_seconds', lambda: None)


def test_the_sample_photos_fixture_is_a_usable_demo_folder(admin, app):
    app.config['DRIVE_FOLDER_URL'] = str(FIXTURES / 'sample_photos')

    admin.post('/admin/photos/read-drive')

    assert '4 photos' in admin.get('/admin/photos').get_data(as_text=True)
