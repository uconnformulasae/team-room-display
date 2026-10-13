# Photos from a Google Drive folder — design (2026-10-06)

**Asked for:** "I want the photos to come from a google drive … that would be quicker than
having it done through the admin page." Same idea as the calendar (2026-09-29): the team
already keeps photos in Google Drive, so the screen should follow a folder instead of
needing someone to upload through the admin pages.

## Decisions (Owen, 2026-10-06)

| Question | Choice | Why / what it costs |
|---|---|---|
| How does the Pi get at the folder? | Folder shared as **"Anyone with the link can view"**, read with a Google Cloud **API key** | About 10 minutes of setup and no key file on the Pi. Anyone who gets the folder link can see the photos, so the link is treated like a password-lite: env file only, not committed, not logged. (The alternative, a private folder shared with a service account, was offered and not chosen.) |
| What happens to the admin Photos page? | **Read-only, like the calendar** | It shows which Drive photos are on the screen, when the folder was last read, which files couldn't be used and why, and a "Read Drive now" button. Uploading, captions, hide and delete are gone: to change the photos, change the folder. |

## How it works

- `app/drive_photos.py` does for the folder what `app/calendar_feed.py` does for the
  calendar. Every 30 s (the same `display-publish` loop, right after the calendar) it:
  1. asks Drive for the folder's name (this is also how a wrong link or a folder that
     isn't shared is noticed: listing a folder you can't see just returns nothing), then
     lists its newest 200 image files, newest first (`createdTime desc`);
  2. downloads only the files it hasn't got, or whose contents changed (`md5Checksum`),
     and runs each through the existing photo pipeline (`app/photos.py`): upright, JPEG,
     at most 1920 px, location data removed, random file name, 480 px thumbnail;
  3. saves the folder's list as the `photos` table (a rebuilt copy, like `events`), and
     deletes the copies of photos that left the folder.
- Downloads stop after about 20 s in one round; the rest wait for the next round. The
  first read of a folder with 100 photos therefore takes a few minutes, but the calendar
  keeps being read every 30 s while it happens.
- A file that can't be used (too big, not a photo, a format Pillow can't read) is
  remembered with the reason, so it isn't downloaded again every 30 s. Replacing it in
  Drive (new contents, new checksum) makes the Pi try again.
- If the folder can't be read (no network, wrong link, key problem), nothing changes:
  the screen keeps the photos it had, and the Status and Photos pages say why.
- Captions come from the file's **description** in Drive (Drive → file → File
  information → Details → Description). No description, no caption.
- The API key travels in a request header, never in a URL, so it can't end up in an
  error message or a log line.
- Like the calendar link, `DRIVE_FOLDER_URL` can be the path of a folder on this
  computer. The demo (`scripts/seed_demo.py`) uses `fixtures/sample_photos` that way, so a
  laptop needs no Google account to see the screen.

## Shared code

The hardened download (whole-download timeout, size cap, https-only redirects,
cut-short detection; stress test 2026-09-30, M3/M4/L3) moves from `calendar_feed.py` to
`app/http_get.py`, so the calendar and Drive readers use the same one.

## Not carried over

Photos uploaded through the old admin page are dropped when the new `photos` table is
created (their files are removed on the first successful read of the folder). Nothing
was deployed to the Pi yet, so this only affects laptops running the demo.
