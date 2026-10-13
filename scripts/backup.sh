#!/bin/bash
# Nightly backup of the schedule (the SQLite database) and the photos to the USB stick.
#
# Run by display-backup.timer at 03:30 as the "displayapp" service account; you can also run it by hand:
#     sudo systemctl start display-backup     (then: journalctl -u display-backup)
# Keeps the newest 14 of each. Writes the result to data/last_backup.json for the admin
# Status page, and exits non-zero on failure so it also shows in `systemctl status`.
# How to restore: docs/TROUBLESHOOTING.md.
# -E: a failure inside a function also runs the ERR trap below.
set -Eeuo pipefail

DB="${DISPLAY_DB:-/srv/display/data/display.db}"
WEB="${DISPLAY_WEB:-/srv/display/web}"
DEST="${BACKUP_DIR:-/media/backup}"
KEEP=14
STATUS_FILE="$(dirname "$DB")/last_backup.json"
STAMP="$(date +%F)"
DB_COPY="$DEST/display-$STAMP.db"
PHOTOS_COPY="$DEST/photos-$STAMP.tar"

write_status() {  # write_status true|false "message"
    mkdir -p "$(dirname "$STATUS_FILE")"  # so a wrong DISPLAY_DB still leaves a note for the Status page
    printf '{"at": "%s", "ok": %s, "message": "%s"}\n' \
        "$(date +%Y-%m-%dT%H:%M:%S)" "$1" "${2//\"/\'}" > "$STATUS_FILE.tmp"
    mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}

fail() {
    trap - ERR
    echo "BACKUP FAILED: $1" >&2
    rm -f -- "$DB_COPY" "$DB_COPY-wal" "$DB_COPY-shm" "$PHOTOS_COPY" 2>/dev/null || true  # no half-written copies
    write_status false "$1"
    exit 1
}

make_room() {  # make_room "display-*.db": keep the newest $KEEP - 1 older copies
    local old
    while read -r old; do
        [ -n "$old" ] || continue
        rm -f -- "$old" || fail "could not delete the old backup $old"
    done <<< "$(ls -1td "$DEST"/$1 2>/dev/null | grep -v -- "-$STAMP\." | tail -n +"$KEEP" || true)"
}

trap 'fail "unexpected error on line $LINENO"' ERR

mountpoint -q "$DEST" || fail "USB stick not mounted at $DEST"
[ -w "$DEST" ] || fail "cannot write to $DEST"

# Delete the oldest copies first, so a nearly full stick has room for tonight's.
make_room 'display-*.db'
make_room 'photos-*.tar'

# .backup makes a consistent copy even while the admin app is writing. The copy is then
# switched out of WAL mode, so it's one self-contained file (no -wal/-shm files beside it).
sqlite3 "$DB" ".backup '$DB_COPY'" || fail "sqlite3 .backup failed"
sqlite3 "$DB_COPY" "PRAGMA journal_mode=DELETE;" > /dev/null || fail "could not finish the database copy"
sqlite3 "$DB_COPY" "PRAGMA integrity_check;" | grep -qx ok \
    || fail "the backup copy failed its integrity check"
rm -f -- "$DB_COPY-wal" "$DB_COPY-shm"  # leftovers from .backup; the copy no longer uses them

# The photos are already-compressed JPEGs, so a plain tar is as small as it gets.
mkdir -p "$WEB/photos"
tar -cf "$PHOTOS_COPY" -C "$WEB" photos || fail "could not tar the photos"
# Flush the stick only: a plain `sync` flushes every disk and stalls the Pi's other writes
# for seconds. (macOS's sync has no -f, hence the fallback, for the tests.)
sync -f "$DEST" 2>/dev/null || sync

write_status true "display-$STAMP.db and photos-$STAMP.tar on $DEST"
echo "Backup OK: $DEST/display-$STAMP.db and $DEST/photos-$STAMP.tar"
