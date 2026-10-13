"""Constants and helpers shared by the tests (fixtures live in conftest.py)."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / 'fixtures'
PASSWORD = 'correct horse battery staple'
FIXED_NOW = datetime(2026, 9, 22, 14, 10)  # a Tuesday afternoon


class FakeClock:
    """Stands in for app.clock.now() so tests control what time it is."""

    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)


def make_test_images(folder):
    """Create the test images named in PLAN.md §4 (plus a few extras) in `folder`.
    They're generated rather than committed: a 21 MB file doesn't belong in git."""
    from PIL import ExifTags, Image, ImageDraw

    paths = {}

    # A 12-megapixel "photo" with camera and GPS EXIF data, like a phone would upload.
    photo = Image.new('RGB', (4000, 3000), (40, 90, 160))
    draw = ImageDraw.Draw(photo)
    for x in range(0, 4000, 250):
        draw.ellipse((x, 800 + x % 700, x + 600, 1400 + x % 700), fill=(220, 120 + x % 100, 40))
    photo = Image.blend(photo, Image.effect_noise((4000, 3000), 20).convert('RGB'), 0.15)
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = 'TestCam'
    exif[ExifTags.Base.Model] = 'Phone 12'
    exif[ExifTags.IFD.GPSInfo] = {ExifTags.GPS.GPSLatitudeRef: 'N', ExifTags.GPS.GPSLatitude: (41.0, 48.0, 30.0)}
    paths['img_12mp.jpg'] = folder / 'img_12mp.jpg'
    photo.save(paths['img_12mp.jpg'], 'JPEG', quality=92, exif=exif, comment=b'secret camera comment')

    # Portrait photo taken with the phone sideways: stored as 400x300 pixels plus an EXIF
    # "rotate 90 degrees clockwise" tag. Upright, it's red on top and blue underneath.
    upright = Image.new('RGB', (300, 400), (0, 0, 255))
    ImageDraw.Draw(upright).rectangle((0, 0, 299, 199), fill=(255, 0, 0))
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    paths['img_portrait_exif.jpg'] = folder / 'img_portrait_exif.jpg'
    upright.transpose(Image.Transpose.ROTATE_90).save(paths['img_portrait_exif.jpg'], 'JPEG', exif=exif)

    # Not an image at all, whatever the name says.
    paths['img_evil.jpg'] = folder / 'img_evil.jpg'
    paths['img_evil.jpg'].write_text("<?php system($_GET['cmd']); ?>\nThis is not a JPEG.\n")

    # Too big: just over the 20 MB per-photo limit.
    paths['img_21mb.jpg'] = folder / 'img_21mb.jpg'
    paths['img_21mb.jpg'].write_bytes(b'\xff\xd8\xff\xe0' + bytes(21 * 1024 * 1024))

    # A real image in a format we don't accept, renamed to .jpg.
    paths['gif_named.jpg'] = folder / 'gif_named.jpg'
    Image.new('RGB', (40, 30), (0, 128, 0)).save(paths['gif_named.jpg'], 'GIF')

    # A PNG with transparency, and an iPhone-style HEIC.
    paths['alpha.png'] = folder / 'alpha.png'
    Image.new('RGBA', (800, 600), (255, 0, 0, 128)).save(paths['alpha.png'])
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        paths['photo.heic'] = folder / 'photo.heic'
        Image.new('RGB', (640, 480), (30, 160, 60)).save(paths['photo.heic'], 'HEIF')
    except ImportError:
        pass
    return paths


def published(web_dir, name='events.json'):
    """A file the app published to web/data/, parsed."""
    return json.loads((web_dir / 'data' / name).read_text())


def calendar_event(title, start_at, end_at=None, all_day=False, location=''):
    """One event as models.replace_events() takes it (a row of the saved calendar copy)."""
    return {'uid': f'{title}@test', 'title': title, 'start_at': start_at, 'end_at': end_at,
            'all_day': all_day, 'location': location}


def csrf_token(client, page='/admin/login'):
    """Fetch a page and pull the CSRF token out of its form, like a browser would."""
    html = client.get(page).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, f'no CSRF token found on {page}'
    return match.group(1)


def login(client, password=PASSWORD, **kwargs):
    token = csrf_token(client)
    return client.post('/admin/login', data={'password': password, 'csrf_token': token}, **kwargs)


class AdminClient:
    """A logged-in test client. post() adds the session's CSRF token automatically (it is
    fetched once; the token stays valid for the whole session)."""

    def __init__(self, client):
        self.client = client
        self.token = None

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url, data=None, **kwargs):
        if self.token is None:
            self.token = csrf_token(self.client, '/admin/settings')
        data = dict(data or {})
        data.setdefault('csrf_token', self.token)
        return self.client.post(url, data=data, **kwargs)
