"""Turning a photo from the team's Drive folder into what the screen shows.

Every photo is decoded and re-encoded, never stored as it came: it comes out upright, as
a JPEG (quality 85), at most 1920 px on its long side, with no EXIF data (so no GPS
location or camera details), under a random file name. A 480 px thumbnail is saved for
the admin page. Anything Pillow can't read as a photo is rejected.
"""
import io
import logging
import math
import os
import re
import secrets
import time
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhones save HEIC; pillow-heif teaches Pillow to read it.
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

log = logging.getLogger(__name__)

# The only decoders Pillow may use on a photo. (Multi-picture JPEGs from some cameras
# open through the JPEG decoder and report their format as 'MPO'.)
ALLOWED_FORMATS = ['JPEG', 'PNG', 'WEBP'] + (['HEIF'] if HEIC_SUPPORTED else [])
MAX_MB = 20  # per photo
MAX_BYTES = MAX_MB * 1024 * 1024
# JPEGs can be decoded at a reduced size, so even a 108-megapixel phone photo is cheap.
# Other formats are decoded at full size (about 3 bytes a pixel), so they get a lower cap
# that still fits a 48-megapixel iPhone photo, to keep a 2 GB Pi from running out of memory.
MAX_JPEG_PIXELS = 100_000_000
MAX_OTHER_PIXELS = 50_000_000
SIXTEEN_BIT_MODES = {'I', 'I;16', 'I;16B', 'I;16L', 'I;16N'}  # e.g. 16-bit grayscale PNGs
MAX_SIDE = 1920
THUMB_SIDE = 480
QUALITY = 85
FILENAME_PATTERN = re.compile(r'[0-9a-f]{16}\.jpg')


class PhotoError(Exception):
    """A file we can't use. The message is shown on the admin Photos page."""


def open_photo(stream, name):
    """Decode the file into an upright RGB image no bigger than MAX_SIDE."""
    try:
        with Image.open(stream, formats=ALLOWED_FORMATS) as probe:
            probe.verify()  # catches many broken files; the image can't be used after this
        stream.seek(0)
        image = Image.open(stream, formats=ALLOWED_FORMATS)
        limit = MAX_JPEG_PIXELS if image.format in ('JPEG', 'MPO') else MAX_OTHER_PIXELS
        if image.width * image.height > limit:
            raise PhotoError(f'{name} has too many pixels ({image.width}×{image.height}). '
                             'Put a smaller copy in the folder instead.')
        icc_profile = image.info.get('icc_profile') if image.mode in ('RGB', 'RGBA') else None
        # For JPEGs, decode at a reduced size straight away (much faster on the Pi).
        scale = MAX_SIDE / max(image.size)
        if scale < 1:
            image.draft('RGB', (math.ceil(image.width * scale), math.ceil(image.height * scale)))
        if image.mode in SIXTEEN_BIT_MODES:
            image = image.convert('I').point(lambda value: value / 256).convert('L')
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)  # shrink before making copies
        image = to_rgb(ImageOps.exif_transpose(image))  # apply the EXIF rotation
    except PhotoError:
        raise
    except Exception as error:  # anything the decoders throw at a bad file
        log.warning('Could not use the photo %r: %s', name, error)
        raise PhotoError(f'{name} is not a photo we can read.') from error
    image.info = {'icc_profile': icc_profile} if icc_profile else {}  # drop EXIF, comments, XMP
    return image


def to_rgb(image):
    """JPEG has no transparency: put transparent images on black, like the screen."""
    if image.mode in ('RGBA', 'LA', 'PA') or (image.mode == 'P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, (0, 0, 0))
        background.paste(rgba, mask=rgba.getchannel('A'))
        return background
    return image.convert('RGB')


def write_jpeg(image, path, quality):
    """Write to a temporary name, make sure it is on disk, then rename: a half-written
    file never appears, and the photo survives the power going right after the download."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.tmp')
    try:
        with open(temp, 'wb') as f:
            image.save(f, 'JPEG', quality=quality, optimize=True, icc_profile=image.info.get('icc_profile'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_photo(data, name, photos_dir):
    """Check, convert and save one photo (its bytes, and its name for messages). Returns
    the new file name inside photos_dir. Raises PhotoError if the file can't be used."""
    if not data:
        raise PhotoError(f'{name} is empty.')
    if len(data) > MAX_BYTES:
        raise PhotoError(f'{name} is {len(data) / 1024 / 1024:.0f} MB. Photos can be at most {MAX_MB} MB.')
    image = open_photo(io.BytesIO(data), name)
    photos_dir = Path(photos_dir)
    filename = f'{secrets.token_hex(8)}.jpg'
    thumbnail = image.copy()
    thumbnail.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
    try:
        write_jpeg(image, photos_dir / filename, QUALITY)
        write_jpeg(thumbnail, photos_dir / 'thumbs' / filename, 75)
    except Exception:
        delete_files(photos_dir, filename)
        raise
    return filename


def delete_files(photos_dir, filename):
    """Remove a photo and its thumbnail. Only touches names this module created."""
    if not FILENAME_PATTERN.fullmatch(filename):
        log.warning('Refusing to delete unexpected photo file name %r', filename)
        return
    for path in (Path(photos_dir) / filename, Path(photos_dir) / 'thumbs' / filename):
        path.unlink(missing_ok=True)


def remove_unused_files(photos_dir, keep, older_than_s=3600):
    """Delete photos (and thumbnails) that no saved photo uses: copies of photos that left
    the Drive folder, or uploads from before Drive. Files younger than older_than_s are
    left alone; they may belong to a read of the folder that is still saving its list.
    Returns how many photos were removed."""
    photos_dir = Path(photos_dir)
    if not photos_dir.is_dir():
        return 0
    cutoff = time.time() - older_than_s
    removed = 0
    for path in photos_dir.iterdir():
        if FILENAME_PATTERN.fullmatch(path.name) and path.name not in keep and path.stat().st_mtime < cutoff:
            delete_files(photos_dir, path.name)
            removed += 1
    return removed
