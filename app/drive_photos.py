"""Reading the team's photos from a Google Drive folder.

The team keeps the screen's photos in one Drive folder, shared as "Anyone with the link
can view". This module lists that folder with the Google Drive API (read_folder),
downloads the photos it hasn't got yet and runs each through app/photos.py (upright,
JPEG, at most 1920 px, location data removed), and saves the folder's list as the photos
table (sync). scripts/publish.py runs sync every 30 seconds on the Pi, right after
reading the calendar. docs/SETUP.md says how to make the folder link and the API key.

The folder is the source of truth: a photo put in it reaches the screen within about a
minute, one taken out leaves, one replaced is downloaded again. The newest MAX_PHOTOS
(by when they were added to Drive) are used, newest first.

The API key is sent in a request header, never in a link, so it can't end up in an
error message or a log line. Neither the key nor the folder link is ever shown.

DRIVE_FOLDER_URL can also be a folder on this computer (the demo uses fixtures/
sample_photos): no Google account needed, and the same code path from there on.
"""
import http.client
import json
import logging
import re
import time
import urllib.error
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlencode, urlsplit

from . import db, http_get, models, photos
from .calendar_feed import SyncResult, one_line

log = logging.getLogger(__name__)

API = 'https://www.googleapis.com/drive/v3'
TIMEOUT_S = 20            # for the folder's name and list; campus Wi-Fi can hang
PHOTO_TIMEOUT_S = 60      # for one photo (up to 20 MB)
MAX_LIST_BYTES = 2 * 1024 * 1024  # the list of 200 files is about 60 kB
DOWNLOAD_BUDGET_S = 20    # per round; the rest wait for the next round, so the calendar isn't held up
MAX_PHOTOS = 200          # the newest this many are used (the screen never needs more)
MAX_CAPTION = 100
FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
LOCAL_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
# Drive ids are letters, digits, - and _ (a folder's are 20+ long). Checking that also keeps
# quotes out of the folder query and slashes out of the links built from file ids.
FOLDER_ID = re.compile(r'[A-Za-z0-9_-]{10,}')
FILE_ID = re.compile(r'[A-Za-z0-9_-]+')
NOT_A_FOLDER_LINK = ('DRIVE_FOLDER_URL should be the folder\'s link from Google Drive ("Share" → "Copy link"), '
                     'like https://drive.google.com/drive/folders/1AbC…')


class DriveError(Exception):
    """Something Drive (or the network) wouldn't give us. The message is shown on the
    admin pages. retry=False means asking again won't help until the file changes."""

    def __init__(self, message, retry=True):
        super().__init__(message)
        self.retry = retry


class DriveFile(NamedTuple):
    id: str
    version: str           # changes when the contents change
    name: str
    caption: str
    size: int              # bytes, 0 if Drive didn't say
    resource_key: str | None


class Folder(NamedTuple):
    name: str
    files: list            # DriveFile, newest first, at most MAX_PHOTOS
    more: bool             # the folder has more photos than MAX_PHOTOS


# --- Reading the folder ------------------------------------------------------------------

def is_local(link):
    """A folder on this computer rather than a Drive link or id."""
    return '://' not in link and '/' in link


def parse_folder_link(link):
    """(folder id, resource key or None) from a Drive folder link, or a bare folder id."""
    link = link.strip()
    parts = urlsplit(link)
    if parts.scheme:
        if parts.scheme != 'https' or parts.hostname != 'drive.google.com':
            raise DriveError(NOT_A_FOLDER_LINK)
        query = parse_qs(parts.query)
        in_path = re.search(r'/folders/([^/]+)', parts.path)
        folder = in_path.group(1) if in_path else (query.get('id') or [''])[0]
        resource_key = (query.get('resourcekey') or [None])[0]
    else:
        folder, resource_key = link, None
    if not FOLDER_ID.fullmatch(folder) or (resource_key and not FILE_ID.fullmatch(resource_key)):
        raise DriveError(NOT_A_FOLDER_LINK)
    return folder, resource_key


def explain(code, body):
    """A Drive error reply as a sentence for the admin pages."""
    try:
        error = json.loads(body)['error']
        message = one_line(error.get('message'))
        reasons = {e.get('reason') for e in error.get('errors', []) + error.get('details', [])}
    except (ValueError, KeyError, TypeError, AttributeError):
        message, reasons = '', set()
    if reasons & {'API_KEY_INVALID', 'keyInvalid'} or 'API key not valid' in message:
        return 'DRIVE_API_KEY is not a valid API key. Copy it again from Google Cloud (docs/SETUP.md).'
    if reasons & {'SERVICE_DISABLED', 'accessNotConfigured'}:
        return ("The Google Drive API is not turned on for the API key's Google Cloud project. "
                'Turn it on (docs/SETUP.md, "Photos from Google Drive").')
    if 'API_KEY_SERVICE_BLOCKED' in reasons:
        return ('DRIVE_API_KEY is not allowed to use the Google Drive API. In Google Cloud, add the Drive API '
                "to the key's API restrictions (docs/SETUP.md).")
    if code == 404:
        return ('Google Drive cannot find the folder. Check DRIVE_FOLDER_URL, and that the folder is shared '
                'as "Anyone with the link" (docs/SETUP.md).')
    if code == 429 or any('RateLimit' in (reason or '') or 'rateLimit' in (reason or '') for reason in reasons):
        return 'Google Drive says there were too many requests. The Pi tries again in a moment.'
    return f'Google Drive answered with error {code}: {message or "no details"}'


def api_get(path, query, api_key, resource_keys=(), max_bytes=MAX_LIST_BYTES, timeout_s=TIMEOUT_S):
    """GET a Drive API path. Raises DriveError."""
    headers = {'X-Goog-Api-Key': api_key}
    if resource_keys:
        headers['X-Goog-Drive-Resource-Keys'] = ','.join(resource_keys)
    url = f'{API}/{path}?{urlencode(query)}'
    try:
        return http_get.download(url, max_bytes=max_bytes, timeout_s=timeout_s, headers=headers)
    except http_get.DownloadError as error:
        raise DriveError(str(error)) from None
    except urllib.error.HTTPError as error:
        body = error.read(65536)
        # A 4xx won't change by asking again (except "too many requests" and "timed out").
        raise DriveError(explain(error.code, body), retry=error.code >= 500 or error.code in (408, 429)) from None
    except (OSError, http.client.HTTPException) as error:  # no network, timed out
        reason = getattr(error, 'reason', None) or getattr(error, 'strerror', None) or type(error).__name__
        raise DriveError(f'Could not reach Google Drive ({reason}).') from None


def read_drive_folder(link, api_key):
    folder_id, resource_key = parse_folder_link(link)
    keys = [f'{folder_id}/{resource_key}'] if resource_key else []
    about = json.loads(api_get(f'files/{folder_id}', {'fields': 'name,mimeType', 'supportsAllDrives': 'true'},
                               api_key, keys))
    if about.get('mimeType') != FOLDER_MIME_TYPE:
        raise DriveError('DRIVE_FOLDER_URL is the link to a file, not a folder. Share the folder itself.')
    listing = json.loads(api_get('files', {
        'q': f"'{folder_id}' in parents and trashed = false and mimeType contains 'image/'",
        'orderBy': 'createdTime desc',
        'pageSize': MAX_PHOTOS,
        'fields': 'nextPageToken,files(id,name,description,md5Checksum,modifiedTime,size,resourceKey)',
        'supportsAllDrives': 'true',
        'includeItemsFromAllDrives': 'true',
    }, api_key, keys))
    files = [DriveFile(id=f['id'], version=f.get('md5Checksum') or f.get('modifiedTime') or '',
                       name=one_line(f.get('name')) or '(no name)',
                       caption=one_line(f.get('description'))[:MAX_CAPTION],
                       size=int(f.get('size') or 0), resource_key=f.get('resourceKey'))
             for f in listing.get('files', [])[:MAX_PHOTOS] if FILE_ID.fullmatch(f.get('id', ''))]
    return Folder(name=one_line(about.get('name')), files=files, more=bool(listing.get('nextPageToken')))


def read_local_folder(link):
    folder = Path(link)
    if not folder.is_dir():
        raise DriveError(f'There is no folder at {folder.name}.')
    found = sorted((p for p in folder.iterdir() if p.suffix.lower() in LOCAL_EXTENSIONS and p.is_file()),
                   key=lambda p: (-p.stat().st_mtime_ns, p.name))
    files = [DriveFile(id=p.name, version=f'{p.stat().st_size}-{p.stat().st_mtime_ns}', name=p.name, caption='',
                       size=p.stat().st_size, resource_key=None) for p in found[:MAX_PHOTOS]]
    return Folder(name=folder.name, files=files, more=len(found) > MAX_PHOTOS)


def read_folder(link, api_key):
    """The folder's name and its newest photos. Raises DriveError."""
    return read_local_folder(link) if is_local(link) else read_drive_folder(link, api_key)


def fetch_photo(link, api_key, file):
    """One photo's bytes. Raises DriveError."""
    if is_local(link):
        try:
            return (Path(link) / file.id).read_bytes()
        except OSError as error:
            raise DriveError(f'Could not read {file.name} ({error.strerror}).') from None
    keys = [f'{file.id}/{file.resource_key}'] if file.resource_key else []
    return api_get(f'files/{file.id}', {'alt': 'media', 'supportsAllDrives': 'true'}, api_key, keys,
                   max_bytes=photos.MAX_BYTES, timeout_s=PHOTO_TIMEOUT_S)


# --- sync: the folder → the photos table ----------------------------------------------

def record_failure(conn, message):
    """A read that failed changes nothing but the status row: the screen keeps its photos."""
    with db.transaction(conn):
        models.record_drive_check(conn, False, message)
    return SyncResult(False, message, False)


def download_new(folder, link, api_key, photos_dir, saved):
    """Download and convert the photos that aren't saved yet (or changed, or whose copy
    went missing), for at most DOWNLOAD_BUDGET_S. Returns ({drive id: {filename, problem}},
    how many are still waiting)."""
    found, waiting, attempts = {}, 0, 0
    started = time.monotonic()
    for file in folder.files:
        if is_saved(saved.get(file.id), file, photos_dir):
            continue
        if file.size > photos.MAX_BYTES:  # no need to download it to know
            found[file.id] = {'filename': None, 'problem': f'{file.name} is {file.size / 1024 / 1024:.0f} MB. '
                                                           f'Photos can be at most {photos.MAX_MB} MB.'}
            continue
        if attempts and time.monotonic() - started > DOWNLOAD_BUDGET_S:
            waiting += 1
            continue
        attempts += 1
        try:
            found[file.id] = {'filename': photos.save_photo(fetch_photo(link, api_key, file), file.name, photos_dir),
                              'problem': None}
        except photos.PhotoError as error:
            found[file.id] = {'filename': None, 'problem': str(error)}
        except DriveError as error:
            if error.retry:
                log.warning('Could not download %s from Drive, will try again: %s', file.name, error)
                waiting += 1
            else:
                found[file.id] = {'filename': None, 'problem': f'{file.name}: {error}'}
    return found, waiting


def is_saved(row, file, photos_dir):
    """Whether `row` already holds this version of the file (and its copy is still there)."""
    if row is None or row['version'] != file.version:
        return False
    return row['filename'] is None or (Path(photos_dir) / row['filename']).exists()


def sync(conn, link, api_key, photos_dir):
    """Read the folder and save its photos. Returns a SyncResult. A read that fails
    changes nothing but the status row, so the screen keeps the photos it had."""
    if not link:
        return record_failure(conn, 'No Drive folder is set. Add DRIVE_FOLDER_URL to /etc/display/env '
                                    '(docs/SETUP.md).')
    if not is_local(link) and not api_key:
        return record_failure(conn, 'No Drive API key is set. Add DRIVE_API_KEY to /etc/display/env '
                                    '(docs/SETUP.md).')
    try:
        folder = read_folder(link, api_key)
    except DriveError as error:
        log.warning('Could not read the Drive folder: %s', error)
        return record_failure(conn, str(error))

    photos_dir = Path(photos_dir)
    saved = {row['drive_id']: row for row in models.list_photos(conn)}
    found, waiting = download_new(folder, link, api_key, photos_dir, saved)

    with db.transaction(conn):
        # Read the saved list again: another reader (the admin's "Read Drive now") may
        # have saved some of the same photos while we were downloading.
        saved = {row['drive_id']: row for row in models.list_photos(conn)}
        rows = []
        for file in folder.files:
            row = saved.get(file.id)
            if is_saved(row, file, photos_dir):
                kept = {'filename': row['filename'], 'problem': row['problem']}
            elif file.id in found:
                kept = found[file.id]
            else:
                continue  # still to download
            rows.append({'drive_id': file.id, 'version': file.version, 'name': file.name,
                         'caption': file.caption, **kept})
        changed = models.replace_photos(conn, rows)
        message = summary(rows, waiting, folder.more)
        models.record_drive_check(conn, True, message, folder.name)

    # Only now that the new list is saved: remove the copies it no longer uses.
    in_use = {row['filename'] for row in rows if row['filename']}
    for filename in {row['filename'] for row in saved.values()} | {f['filename'] for f in found.values()}:
        if filename and filename not in in_use:
            photos.delete_files(photos_dir, filename)
    photos.remove_unused_files(photos_dir, keep=in_use)
    return SyncResult(True, message, changed)


def summary(rows, waiting, more):
    """'40 photos, 3 still downloading, 1 could not be used', for the admin pages."""
    shown = sum(1 for row in rows if row['filename'])
    problems = len(rows) - shown
    message = f'{shown} photo{"" if shown == 1 else "s"}'
    if waiting:
        message += f', {waiting} still downloading'
    if problems:
        message += f', {problems} could not be used'
    if more:
        message += f' (only the newest {MAX_PHOTOS} are used)'
    return message
