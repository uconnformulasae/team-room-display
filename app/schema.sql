-- Database schema (docs/PLAN.md §5.1, and docs/SPEC.md §11 for the calendar and the photos). Applied every time the app starts, so every
-- statement must be safe to run again: IF NOT EXISTS / INSERT OR IGNORE.
PRAGMA journal_mode=WAL;

-- A copy of the team's Google Calendar, rebuilt by app/calendar_feed.py. The calendar is
-- the source of truth; nothing here is edited by hand.
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  uid         TEXT NOT NULL,          -- the calendar's id for the event (repeats share one)
  title       TEXT NOT NULL,
  start_at    TEXT NOT NULL,          -- 'YYYY-MM-DDTHH:MM' local wall clock
  end_at      TEXT,                   -- nullable; for all_day: the last day, 'YYYY-MM-DDT00:00'
  all_day     INTEGER NOT NULL DEFAULT 0,
  location    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);

-- How the last reads of the calendar went. Always exactly one row.
CREATE TABLE IF NOT EXISTS calendar_sync (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  checked_at    TEXT,                 -- the last attempt
  synced_at     TEXT,                 -- the last attempt that worked
  ok            INTEGER,
  message       TEXT,                 -- '12 events', or what went wrong
  calendar_name TEXT
);
INSERT OR IGNORE INTO calendar_sync (id) VALUES (1);

-- A copy of the team's Google Drive photo folder, rebuilt by app/drive_photos.py, in the
-- order the screen shows them (the folder's newest first). Nothing here is edited by hand.
CREATE TABLE IF NOT EXISTS photos (
  id          INTEGER PRIMARY KEY,
  drive_id    TEXT NOT NULL UNIQUE,   -- the Drive file's id (or the file name, for a folder on this computer)
  version     TEXT NOT NULL,          -- changes when the file's contents change (Drive's md5Checksum)
  filename    TEXT UNIQUE,            -- our resized copy in web/photos, random name .jpg; NULL if unusable
  name        TEXT NOT NULL,          -- the file's name in Drive, for the admin Photos page
  caption     TEXT,                   -- the file's description in Drive
  problem     TEXT                    -- why the file can't be used ('... is not a photo we can read.')
);

-- How the last reads of the Drive folder went. Always exactly one row.
CREATE TABLE IF NOT EXISTS drive_sync (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  checked_at    TEXT,                 -- the last attempt
  synced_at     TEXT,                 -- the last attempt that worked
  ok            INTEGER,
  message       TEXT,                 -- '40 photos', or what went wrong
  folder_name   TEXT
);
INSERT OR IGNORE INTO drive_sync (id) VALUES (1);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
-- keys: competition_name, competition_date, competition2_name, competition2_date (two
-- countdowns, e.g. one per car), photo_interval_s, font_scale, banner_text
INSERT OR IGNORE INTO settings (key, value) VALUES
  ('competition_name', ''),
  ('competition_date', ''),
  ('competition2_name', ''),
  ('competition2_date', ''),
  ('photo_interval_s', '15'),
  ('font_scale', '1.0'),
  ('banner_text', '');

CREATE TABLE IF NOT EXISTS audit_log (
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  who      TEXT,                      -- Tailscale-User-Login header or 'local'
  action   TEXT NOT NULL,             -- e.g. calendar.read, drive.read, settings.update
  target   TEXT,
  detail   TEXT
);
