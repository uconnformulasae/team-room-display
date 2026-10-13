# Troubleshooting

Start with the symptom. Most fixes need SSH to the Pi: `ssh display@daq-display.local`
from the team room, or `tailscale ssh display@daq-display` from anywhere on the tailnet.

**First thing to try, always:** unplug the Pi's power for 10 seconds and plug it back in.
Everything on it restarts cleanly. It's designed to survive having the power pulled.

## Where things are

| What | Where |
|---|---|
| Admin password, the Pi's `display` user password, Tailscale account | **Team password manager.** Never in the repo |
| Admin page | `https://daq-display.<tailnet>.ts.net/admin` (Status page: `/admin/status`) |
| Code | `/srv/display` (a git clone) |
| Schedule database | `/srv/display/data/display.db` |
| Settings and secrets | `/etc/display/env` (root:displayapp, mode 640) |
| Events | The team's **Google Calendar**. The Pi reads it through the secret link in `/etc/display/env` (`CALENDAR_ICS_URL`); a copy of the link is in the team password manager |
| Accounts | `display`: the desktop/kiosk user you SSH in as. `displayapp`: runs the web app, backups and data refresh; no login |
| Backups | USB stick, mounted at `/media/backup`; the newest 14 nights |
| SD card image | Team Drive, next to the link to this doc |

## Commands you'll use

```sh
systemctl status display-admin nginx                # are they running?
sudo systemctl restart display-admin                # restart the admin app
sudo systemctl restart nginx                        # restart the web server
pkill -x chromium                                   # restart the browser on the screen
journalctl -u display-admin -n 50                   # admin app log
journalctl -u nginx -n 50                           # web server log
journalctl -u display-publish -n 20                 # reading the calendar (every 30 s)
sudo systemctl restart display-publish              # restart the calendar reader
journalctl -u display-backup -n 20                  # last backups
journalctl -t kiosk -n 50                           # the browser on the screen
journalctl -p err -b                                # every error since boot
```

---

## The screen is black or says "No signal"

1. Is the TV on, and on the right **HDMI input**? (The remote's Input/Source button.)
   Setting the TV's input to HDMI permanently avoids this; see test 4.5.
2. Is the Pi's red power light on? If not, check the power supply and the wall outlet. It
   must not be a switched power strip.
3. Unplug the Pi for 10 seconds, plug it back in. The display appears within a minute.
4. Still black after the TV was switched off and on: see "TV turned off or unplugged at
   boot" in [pi-config-notes.md](pi-config-notes.md).

## The screen shows an error page, a white page, or the desktop

- The browser keeps retrying on its own. Wait 30 seconds.
- `systemctl status nginx`. If it isn't `active (running)`, run `sudo systemctl restart nginx`
  and `sudo nginx -t` (the latter shows what's wrong with the config).
- The desktop with no browser: the browser only opens once nginx answers, so fix nginx
  first (above) and it appears by itself. `journalctl -t kiosk -n 50` shows "Still waiting"
  lines in that case. No lines at all: is `~/.config/labwc/autostart` still there?
  (SETUP.md step 10.)

## The screen is frozen (the clock isn't moving)

1. From the admin page: **Status → Restart display**. Or over SSH: `pkill -x chromium`.
   It's back within about 10 seconds.
2. If SSH doesn't answer either, the Pi is hung. The watchdog reboots it within about
   30 seconds; otherwise unplug it for 10 seconds.

## Events are missing, wrong, or out of date

The events are whatever is in the team's Google Calendar. Start on the admin page:
**Status → Calendar**.

- **"OK ... last read" a few minutes ago, but an event is missing:** look at the event in
  Google Calendar. Is it on the *team* calendar (not someone's personal one)? Is it
  cancelled? Does it start within 30 days? Google can also take a few minutes to pass a
  change on. Press **Read calendar now**.
- **"No calendar link is set":** the `CALENDAR_ICS_URL` line is missing from
  `/etc/display/env` (SETUP.md step 5). Add it, then `sudo systemctl restart display-admin`.
- **"Could not reach the calendar":** the Pi has no internet. See "Tailscale offline, or
  the Pi fell off the network" below. The screen keeps the events it read last.
- **"The calendar link answered with error 404"**, or **"did not return a calendar":**
  the link is wrong, or someone pressed **Reset** on it in Google Calendar. Copy the
  secret iCal address again (SETUP.md step 5), put it in `/etc/display/env` with
  `sudoedit /etc/display/env`. The 30-second reader picks the new link up by itself;
  `sudo systemctl restart display-admin` makes the **Read calendar now** button use it too.
- **"N events (1 could not be read and was left out)":** one event in the calendar is
  something the reader can't handle (a repeat every second or minute, or a broken rule
  from an imported file). Everything else still shows. `journalctl -u display-publish`
  names it; fix or delete that event in Google Calendar.
- **Every event is off by the same number of hours:** the Pi's time zone is wrong.
  `timedatectl` should say `America/New_York`; fix it with
  `sudo timedatectl set-timezone America/New_York`, reboot, **Read calendar now**.
- **The link leaked** (pasted in a chat, committed to git): in Google Calendar press
  **Reset** next to the secret address. The old link stops working at once. Then put the
  new one on the Pi as above.
- Over SSH: `journalctl -u display-publish -n 20` shows each read. The link itself is
  never printed there, only `calendar.google.com`.

## Photos are missing, old, or not changing

The photos are whatever is in the team's Google Drive folder. Start on the admin page:
**Photos** (the same line is on **Status → Photos**).

- **"OK ... last read" a few minutes ago, but a photo is missing:** is it directly in the
  folder (not in a folder inside it)? Is it listed under **Files the screen can't show**,
  with the reason? Is it among the newest 200? Press **Read Drive now**.
- **"N photos, M still downloading":** a big batch was just added. The Pi downloads about
  20 seconds' worth every 30 seconds; give it a few minutes.
- **"No Drive folder is set"** or **"No Drive API key is set":** a line is missing from
  `/etc/display/env` (SETUP.md step 5). Add it with `sudoedit /etc/display/env`; the
  30-second reader picks it up by itself, and `sudo systemctl restart display-admin` makes
  the **Read Drive now** button use it too.
- **"Google Drive cannot find the folder":** the link is wrong, the folder was deleted, or
  it is no longer shared as **Anyone with the link**. In Drive: right-click the folder →
  **Share** → **General access: Anyone with the link, Viewer**.
- **"DRIVE_API_KEY is not a valid API key":** the key was mistyped, deleted, or
  regenerated. Copy it again from Google Cloud (**APIs & Services → Credentials**).
- **"The Google Drive API is not turned on":** Google Cloud → the `team-display` project →
  **APIs & Services → Library → Google Drive API → Enable**.
- **"... is not allowed to use the Google Drive API":** the key's **API restrictions**
  don't include the Drive API. Edit the key in **Credentials** and tick it.
- **"Could not reach Google Drive":** the Pi has no internet. See "Tailscale offline, or
  the Pi fell off the network" below. The screen keeps the photos it had.
- **A file listed as "not a photo we can read" or "too big":** save it as a JPEG under
  20 MB and put that in the folder instead (the Pi tries a file again when it changes).
- **The folder link or the key leaked:** for the link, make a new folder, move the photos into it,
  share it as above, put its link on the Pi, and delete the old folder (a folder's link
  never changes, so turning sharing off and on again doesn't help). For the key, **Regenerate key** in Google Cloud and put the new one on the Pi.
- Over SSH: `journalctl -u display-publish -n 20`. Neither the link nor the key is ever
  printed there.

## Amber badge: "Schedule may be out of date"

Either the Pi hasn't been able to read the Google Calendar for over an hour (see the
section above; **Status → Calendar** says which), or the screen's data hasn't been
refreshed for over an hour, so the refresh job isn't running.

- **Status page → SD card:** is it almost full? Free space (old files in
  `/srv/display/data`, `journalctl --vacuum-size=100M`).
- `systemctl status display-publish` should be `active (running)`. If not:
  `sudo systemctl restart display-publish`.
  `journalctl -u display-publish -n 20` shows why a refresh failed.
- Run a refresh by hand: **Status → Read calendar now**.

## Every admin page says "The Pi cannot open its database"

The SD card is completely full (the page says which). SQLite can't even open the
database then, so the admin pages can't work, but the screen keeps showing what it had.

1. Over SSH: `df -h /` to confirm, then free space: delete `/srv/display/data/fill` if a test
   left it, `sudo journalctl --vacuum-size=100M`, old files in `/srv/display/data`.
2. Reload the admin page. Nothing needs restarting.

If the page says "database is locked" instead, someone is running `sqlite3` on the live
database by hand. Wait for them to finish, or ask them to `.quit`.

## The database file is damaged

`journalctl -u display-admin -n 20` or `-u display-publish` says `file is not a database`
or `database disk image is malformed`. Restore last night's copy: "Restore from the USB
backup" below. The screen keeps showing what it had until then.

## Red badge: "Can't load new data"

The page can't read `/data/events.json` from nginx. It keeps showing the last copy.

- `curl -s localhost/data/events.json | head` should print JSON.
- Otherwise it's nginx: see "error page" above.

## Wrong time or date on the screen, or no countdown

- `timedatectl` should say `System clock synchronized: yes` and
  `Time zone: America/New_York`.
- Not synchronized: the Pi has no internet. Fix the network (below). The countdown hides
  itself until the clock is synced, and comes back within 5 minutes of that (the files
  are rewritten at least that often).
- Wrong time zone: `sudo timedatectl set-timezone America/New_York`, then reboot.
- A countdown is also hidden when its competition has no date, or the date has passed
  (**Settings**).

## The admin page won't load

1. Is **Tailscale on** on your phone or laptop? Is the Pi online in the Tailscale admin
   console (Machines)?
2. If the Pi shows offline in Tailscale, the Pi has lost its network: see the next
   section.
3. On the Pi: `systemctl status display-admin` and `journalctl -u display-admin -n 50`.
   Restart it with `sudo systemctl restart display-admin`.
4. `tailscale serve status` should list `https://daq-display.<tailnet>.ts.net/`. If not, run
   `sudo tailscale serve --bg http://127.0.0.1:80` (SETUP.md step 3).

## Can't log in

- The password is in the team password manager.
- "Too many wrong passwords": wait 10 minutes.
- **Change the password:**
  `sudo /srv/display/.venv/bin/python /srv/display/scripts/set_password.py --write /etc/display/env`,
  then `sudo systemctl restart display-admin`.

## Tailscale offline, or the Pi fell off the network

- Wi-Fi or Ethernet down: the display keeps working with the events it read last
  (everything it needs is on the Pi), and shows the amber badge after an hour. New
  calendar events don't arrive, and the admin page is unreachable, until the network is
  back. Then the badge clears within a minute.
- The Pi restarts its own networking after 15 minutes of no router
  (`journalctl -u display-netcheck`).
- `tailscale status`. If it asks you to log in, run `sudo tailscale up --ssh` and approve it.
  Then check that key expiry is disabled for the Pi (SETUP.md step 3).

## Status page says "Backup failed"

- **"USB stick not mounted":** is the stick plugged in? `sudo mount /media/backup`. If the
  stick was replaced, format and set it up as in SETUP.md step 9.
- Then run a backup by hand: `sudo systemctl start display-backup` and check
  `journalctl -u display-backup -n 20`.

## Someone leaves the team, or loses their phone

1. Remove them from the tailnet (Tailscale admin console → Users), or stop sharing the
   machine with them. They can no longer reach the admin page at all.
2. If they knew the team password, change it (see "Can't log in"). To also log out
   everyone who's already logged in, delete the `SECRET_KEY=` line from `/etc/display/env`
   before running `set_password.py --write`, then restart `display-admin`.

---

## Restore from the USB backup

Use this when the database is damaged, or when rebuilding onto a new SD card (after
SETUP.md steps 1 – 11).

```sh
ls -lt /media/backup | head                      # pick the newest good date
sudo systemctl stop display-admin
cd /srv/display
sudo -u displayapp cp /media/backup/display-<YYYY-MM-DD>.db data/display.db
sudo -u displayapp rm -f data/display.db-wal data/display.db-shm          # important!
sudo -u displayapp tar -xf /media/backup/photos-<YYYY-MM-DD>.tar -C web
sudo -u displayapp sqlite3 data/display.db "PRAGMA integrity_check"       # must say: ok
sudo systemctl start display-admin               # also rewrites the screen's data
```

Removing the old `-wal` and `-shm` files matters: they belong to the old database and
must not be applied to the restored one. Everything runs as `displayapp` so the web app
can still write the files afterwards. Never run `sqlite3` on the live database as root:
it can leave root-owned `-wal`/`-shm` files that lock the web app out.

## Reflash from the SD card image

Write the image from the team Drive to a new card with Raspberry Pi Imager (**Use
custom**), put it in the Pi, boot. Then restore last night's backup as above, since the
image is older than the schedule.

## Card for the back of the display

Print this, laminate it, and stick it on the back of the TV:

> **Team room display**
> Screen stuck or black? **Unplug the Pi for 10 seconds, then plug it back in.**
> Still broken? Ping the DAQ lead.
> Changing events or photos: `https://daq-display.<tailnet>.ts.net/admin` (needs Tailscale)
