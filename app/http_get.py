"""Downloading over https, for the calendar reader and the Drive photo reader.

Both run every 30 seconds on a Pi on campus Wi-Fi, so a download here never hangs, never
fills memory, and never follows a link somewhere it shouldn't (stress test 2026-09-30,
M3/M4/L3):
  - the whole download has a time limit, not just each read;
  - it stops at max_bytes;
  - redirects are followed only to https (or to this computer, which the tests use);
  - a body shorter than the server promised is an error, not a short file.
"""
import time
import urllib.request
from urllib.parse import urlsplit

THIS_COMPUTER = ('127.0.0.1', 'localhost', '::1')


class DownloadError(Exception):
    """The download went wrong in one of the ways above. The message is a sentence that
    can be shown on the admin pages."""


def link_allowed(url):
    """Only https, so links and keys are never sent in the clear. (Plain http to this
    computer itself is allowed; that is how the tests serve a feed.)"""
    parts = urlsplit(url)
    return parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in THIS_COMPUTER)


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a link we would have accepted in the first place."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not link_allowed(newurl):
            raise DownloadError('The link redirected somewhere this reader will not follow '
                                '(only https:// links are read).')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, *, max_bytes, timeout_s, headers=None):
    """The body at `url`, read in pieces. Raises DownloadError (too slow, too big, cut
    short, a bad redirect), urllib.error.HTTPError (the server answered with an error) or
    OSError (no network, nothing listening)."""
    started = time.monotonic()
    request = urllib.request.Request(url, headers={'User-Agent': 'team-display', **(headers or {})})
    opener = urllib.request.build_opener(SafeRedirects)
    with opener.open(request, timeout=timeout_s) as response:
        promised = response.headers.get('Content-Length')
        pieces, size = [], 0
        while True:
            piece = response.read1(65536)  # whatever has arrived, up to 64 KB; read() would wait for all of it
            if not piece:
                break
            pieces.append(piece)
            size += len(piece)
            if size > max_bytes:
                raise DownloadError(f'The file is too big (over {max_bytes / 1024 / 1024:g} MB).')
            if time.monotonic() - started > timeout_s:
                raise DownloadError(f'The download took too long (over {timeout_s:g} s).')
    data = b''.join(pieces)
    if promised and promised.isdigit() and int(promised) != len(data):
        raise DownloadError('The download was cut short. It will be tried again.')
    return data
