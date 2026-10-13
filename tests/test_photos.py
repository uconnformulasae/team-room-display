"""The photo pipeline: every photo from the Drive folder is checked, turned upright, stripped of EXIF
(location) data and resized before the screen sees it (2.4.1 – 2.4.4)."""
import os

import pytest
from PIL import Image

from app import photos


def save(path, photos_dir, name=None):
    """Run a test image through the pipeline, as drive_photos does with a downloaded file."""
    return photos.save_photo(path.read_bytes(), name or path.name, photos_dir)


@pytest.fixture
def photos_dir(tmp_path):
    return tmp_path / 'photos'


def files_in(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()) if folder.exists() else []


# --- The photo pipeline (app/photos.py) --------------------------------------------

# 2.4.1
def test_12mp_photo_is_shrunk_to_1920_under_500kb_with_no_exif(images, photos_dir):
    name = save(images['img_12mp.jpg'], photos_dir)

    output = photos_dir / name
    with Image.open(output) as image:
        assert image.format == 'JPEG'
        assert max(image.size) == 1920 and image.size == (1920, 1440)
        assert len(image.getexif()) == 0
        assert 'exif' not in image.info and 'comment' not in image.info
    assert output.stat().st_size < 500 * 1024
    assert b'TestCam' not in output.read_bytes() and b'secret camera comment' not in output.read_bytes()


def test_saved_under_a_random_name_with_a_small_thumbnail(images, photos_dir):
    name = save(images['img_12mp.jpg'], photos_dir, 'IMG_0001.JPG')

    assert name != 'IMG_0001.JPG' and photos.FILENAME_PATTERN.fullmatch(name)
    with Image.open(photos_dir / 'thumbs' / name) as thumb:
        assert max(thumb.size) == photos.THUMB_SIDE


# 2.4.2
def test_sideways_portrait_photo_comes_out_upright(images, photos_dir):
    name = save(images['img_portrait_exif.jpg'], photos_dir)

    with Image.open(photos_dir / name) as image:
        assert image.size == (300, 400)
        top, bottom = image.getpixel((150, 50)), image.getpixel((150, 350))
    assert top[0] > 200 and top[2] < 60      # red on top
    assert bottom[2] > 200 and bottom[0] < 60  # blue underneath


# 2.4.3
def test_file_that_is_not_an_image_is_rejected_and_nothing_is_written(images, photos_dir):
    with pytest.raises(photos.PhotoError) as error:
        save(images['img_evil.jpg'], photos_dir)

    assert 'not a photo' in str(error.value)
    assert files_in(photos_dir) == []


def test_real_image_in_an_unsupported_format_is_rejected(images, photos_dir):
    with pytest.raises(photos.PhotoError):
        save(images['gif_named.jpg'], photos_dir)
    assert files_in(photos_dir) == []


def test_the_file_name_does_not_matter_only_what_is_inside(images, photos_dir):
    # Drive file names are whatever the phone or the person chose; the decoders decide.
    name = save(images['img_portrait_exif.jpg'], photos_dir, 'Photo from Sam (1)')

    assert photos.FILENAME_PATTERN.fullmatch(name)


def test_an_empty_file_is_rejected(photos_dir):
    with pytest.raises(photos.PhotoError, match='empty'):
        photos.save_photo(b'', 'blank.jpg', photos_dir)


# 2.4.4
def test_photo_over_20mb_is_too_large(images, photos_dir):
    with pytest.raises(photos.PhotoError, match='at most 20 MB'):
        save(images['img_21mb.jpg'], photos_dir)
    assert files_in(photos_dir) == []


def test_huge_non_jpeg_image_is_refused_before_it_is_decoded(photos_dir, tmp_path):
    # Only JPEGs can be decoded at a reduced size; a 56-megapixel PNG would need
    # hundreds of MB on a Pi. The file itself is tiny, so size limits don't catch it.
    huge = tmp_path / 'panorama.png'
    Image.new('1', (8000, 7000)).save(huge)

    with pytest.raises(photos.PhotoError, match='too many pixels'):
        save(huge, photos_dir)
    assert files_in(photos_dir) == []


def test_16_bit_grayscale_png_keeps_its_tones(photos_dir, tmp_path):
    gray = tmp_path / 'scan.png'
    Image.new('I;16', (64, 48), 30000).save(gray)  # 30000 of 65535: a mid gray

    name = save(gray, photos_dir)

    with Image.open(photos_dir / name) as image:
        red, green, blue = image.getpixel((10, 10))
    assert 105 <= red <= 130 and red == green == blue


def test_png_with_transparency_becomes_a_jpeg(images, photos_dir):
    name = save(images['alpha.png'], photos_dir)

    with Image.open(photos_dir / name) as image:
        assert (image.format, image.mode, image.size) == ('JPEG', 'RGB', (800, 600))


def test_heic_from_an_iphone_becomes_a_jpeg(images, photos_dir):
    if 'photo.heic' not in images or not photos.HEIC_SUPPORTED:
        pytest.skip('pillow-heif is not installed')

    name = save(images['photo.heic'], photos_dir, 'IMG_1234.HEIC')

    with Image.open(photos_dir / name) as image:
        assert (image.format, image.size) == ('JPEG', (640, 480))


def test_photo_files_reach_the_disk_before_they_are_renamed_into_place(images, photos_dir, monkeypatch):
    # Like publish(): fsync, then rename. Otherwise pulling the power right after an
    # upload (test 3.4.1) can leave an empty photo file next to its database row.
    synced, real_fsync, real_replace = set(), os.fsync, os.replace

    def remember_fsync(fd):
        synced.add(os.fstat(fd).st_ino)
        real_fsync(fd)

    def replace_only_if_synced(src, dst):
        assert os.stat(src).st_ino in synced, f'{src} was renamed before being flushed to disk'
        real_replace(src, dst)
    monkeypatch.setattr(os, 'fsync', remember_fsync)
    monkeypatch.setattr(os, 'replace', replace_only_if_synced)

    name = save(images['alpha.png'], photos_dir)

    assert (photos_dir / name).exists() and (photos_dir / 'thumbs' / name).exists()


def test_delete_files_removes_photo_and_thumbnail(images, photos_dir):
    name = save(images['alpha.png'], photos_dir)

    photos.delete_files(photos_dir, name)

    assert files_in(photos_dir) == []


def test_delete_files_refuses_names_that_are_not_ours(tmp_path, photos_dir):
    photos_dir.mkdir()
    (tmp_path / 'important.txt').write_text('keep me')

    photos.delete_files(photos_dir, '../important.txt')

    assert (tmp_path / 'important.txt').exists()


def test_remove_unused_files_clears_old_strays_only(images, photos_dir):
    used, stray, fresh = (save(images['alpha.png'], photos_dir) for _ in range(3))
    (photos_dir / 'README.txt').write_text('not ours')
    an_hour_ago = os.path.getmtime(photos_dir / stray) - 3601
    for name in (used, stray):
        for path in (photos_dir / name, photos_dir / 'thumbs' / name):
            os.utime(path, (an_hour_ago, an_hour_ago))

    removed = photos.remove_unused_files(photos_dir, keep={used})

    # fresh may belong to a read of the folder that hasn't saved its list yet
    assert removed == 1
    assert files_in(photos_dir) == sorted(['README.txt', used, fresh, f'thumbs/{used}', f'thumbs/{fresh}'])


def test_remove_unused_files_without_a_photos_folder(tmp_path):
    assert photos.remove_unused_files(tmp_path / 'photos', keep=set()) == 0
