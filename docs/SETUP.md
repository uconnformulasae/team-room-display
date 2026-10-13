# Setting up the display Pi from a blank SD card

About an hour. Work top to bottom. Each section ends with the checks from
[TESTING.md](TESTING.md) that prove it worked.

**You need:** the Pi and its official power supply (27 W USB-C for a Pi 5, 15 W for a
Pi 4), a microSD card (32 GB or more), a USB stick for backups, the TV and an HDMI
cable, a laptop with [Raspberry Pi Imager](https://www.raspberrypi.com/software/), the
team password manager (admin password, Pi login, Tailscale account), and the **secret
link to the team's Google Calendar** (step 5 says where to find it).

Commands are for the Pi unless it says otherwise. Anything in `<angle brackets>` is
yours to fill in.

---

## 1. Flash the SD card

1. In Raspberry Pi Imager choose your Pi model, then **Raspberry Pi OS (64-bit)**, the
   version *with* desktop, then the SD card.
2. When it offers **OS customisation**, set:
   - Hostname: `daq-display`
   - Username: `display`, plus a password (save it in the team password manager)
   - Wi-Fi, only if there's no Ethernet jack
   - Time zone `America/New_York`, keyboard `us`
   - Services: enable SSH with **public-key authentication only**, and paste your SSH public key
3. Write the card, put it in the Pi, connect the TV (and Ethernet if you have it), plug in
   the power. The first boot takes a few minutes.

## 2. Base system

From your laptop: `ssh display@daq-display.local`

```sh
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y nginx python3-venv python3-pip sqlite3 chromium git curl
sudo raspi-config nonint do_boot_behaviour B4    # start the desktop, logged in as "display"
sudo raspi-config nonint do_blanking 1           # never blank the screen
```

- **Bluetooth off.** Add `dtoverlay=disable-bt` under `[all]` in
  `/boot/firmware/config.txt`, then `sudo systemctl disable --now hciuart bluetooth`.
- **No automatic updates.** `systemctl is-enabled unattended-upgrades` should say
  `disabled` or `not-found`. Updates are done by hand once a month (step 11).
- **Wi-Fi only:** turn off Wi-Fi power saving, which causes dropouts:
  `sudo nmcli connection modify "<wifi name>" 802-11-wireless.powersave 2`
- **Ethernet:** if campus IT will do a DHCP reservation for the Pi's MAC address
  (`ip link show eth0`), ask for one. It's optional because Tailscale finds the Pi anyway.
- The filesystem already fills the whole card on current Raspberry Pi OS.

✅ Checks 3.1.1 – 3.1.4.

- **The Pi needs the internet**, not just the campus network: every 30 seconds it reads
  the team's Google Calendar. Check with `curl -sI https://calendar.google.com | head -1`.
  Without it the screen keeps showing the events it read last, with an amber warning.

## 3. Tailscale: remote access to the admin page

**Once, by whoever owns the tailnet, in a browser:**

1. Sign up at [tailscale.com](https://tailscale.com) with a **personal Gmail or GitHub**
   account. **Not a uconn.edu address:** a university email makes it a business tailnet
   that belongs to the university. The free Personal plan allows 6 users.
2. Admin console → **DNS**: turn on **MagicDNS**, then click **Enable HTTPS**. (This makes
   the Pi's name, `daq-display.<tailnet>.ts.net`, appear in public certificate logs;
   that's normal and harmless.)

**On the Pi:**

```sh
curl -fsSL https://tailscale.com/install.sh | sh      # adds Tailscale's apt repository
sudo tailscale up --ssh                               # open the link it prints; approve the Pi
sudo tailscale serve --bg http://127.0.0.1:80         # HTTPS on the tailnet → nginx
tailscale serve status                                # shows https://daq-display.<tailnet>.ts.net/
```

- The serve setting survives reboots. To remove it: `sudo tailscale serve --https=443 off`.
- **Disable key expiry:** admin console → **Machines** → `daq-display` → **⋯** →
  **Disable key expiry**. Otherwise the Pi drops off the tailnet after 180 days.
- **Tailscale SSH for unattended use:** by default it asks you to log in again every
  12 hours ("check" mode). In the admin console's **Access controls**, change the `ssh`
  rule's `"action": "check"` to `"action": "accept"`.
- **Adding admins:** invite them (up to 6 users on the free plan), or **Share** just the
  `daq-display` machine with them. Shared users don't count toward the 6.
- The admin URL is `https://daq-display.<tailnet>.ts.net/admin`. The display itself is at
  the same address without `/admin`, handy for checking the screen from home.

✅ Checks 2.5.1, 2.5.8, 2.5.9, 2.5.10.

## 4. Get the app

```sh
sudo mkdir -p /srv/display && sudo chown display:display /srv/display
git clone https://github.com/uconnformulasae/team-room-display.git /srv/display
cd /srv/display
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Two accounts, on purpose.** `display` is the desktop user that shows the screen and the
account you SSH in as. Like any Raspberry Pi login it can `sudo` anything. The admin web
app runs as a separate service account, `displayapp`, which has no password, can't log in,
and can only run the two commands in `pi/sudoers-display`. So a bug in the web app can't
take over the Pi. It owns only the folders it writes to:

```sh
sudo adduser --system --group --no-create-home displayapp
sudo install -d -o displayapp -g displayapp /srv/display/data /srv/display/web/data /srv/display/web/photos
```

## 5. Secrets: /etc/display/env

```sh
sudo mkdir -p /etc/display
sudo cp /srv/display/pi/env.example /etc/display/env
sudo /srv/display/.venv/bin/python /srv/display/scripts/set_password.py --write /etc/display/env
sudo chown root:displayapp /etc/display/env
sudo chmod 640 /etc/display/env
```

`set_password.py` asks for the admin password and adds its hash and a random
`SECRET_KEY` to the file. **Put the password in the team password manager.** The file
is never committed to git.

**The calendar link.** On a computer (not the phone app), open Google Calendar →
**Settings** (the gear) → the team's calendar in the left column → **Integrate
calendar** → copy **Secret address in iCal format**. Then:

```sh
sudoedit /etc/display/env
```

and add one line, with the link you copied:

```
CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/<...>/private-<...>/basic.ics
```

- **Anyone who has this link can read the whole calendar.** Keep it in the team password
  manager and in this file, nowhere else: not in git, a chat, or an email.
- If it ever leaks, press **Reset** next to it in Google Calendar, and put the new link
  here (TROUBLESHOOTING.md, "Events are missing").
- **No "Secret address" on that page?** The university's Google administrators have
  turned it off for university accounts. Make the team calendar under a personal Google
  account instead and share it with the team leads, or ask the DAQ lead about the other
  ways to read a calendar (docs/SPEC.md §11, decision 32).

**The photo folder.** The screen shows the photos in one Google Drive folder. Setting it
up takes about 10 minutes, once, on a computer:

1. **Make the folder.** In Google Drive: **New → New folder**, e.g. "Team Display
   Photos". Right-click it → **Share → Share**. Under **General access** pick **Anyone
   with the link** (role **Viewer**), then **Copy link** → **Done**.
2. **Make a Google Cloud project** (free; no billing account needed). Go to
   <https://console.cloud.google.com>, signed in with the same Google account. Click the
   project picker at the top → **New project** → name it `team-display` → **Create**, and
   make sure it is selected at the top.
3. **Turn on the Drive API.** Menu (☰) → **APIs & Services → Library** → search for
   **Google Drive API** → **Enable**.
4. **Make an API key.** **APIs & Services → Credentials → + Create credentials → API
   key**. Open the new key (its name, or **Edit API key**). Under **API restrictions**
   choose **Restrict key**, tick **Google Drive API**, **Save**. Leave **Application
   restrictions** at **None** (the Pi's address changes). Copy the key: it starts with
   `AIza`.
   - **Landed on "Which API are you using?" / "What data will you be accessing?"** (User
     data / Application data)? That is the **Help me choose** wizard, which only makes an
     OAuth client or a service account, not what the display uses. Cancel it, go to
     **APIs & Services → Credentials** from the menu, and pick **API key** straight from
     the **+ Create credentials** list.
   - **No "API key" in that list, or a "Policy restriction" error?** The account's
     organisation (a university, usually) doesn't allow API keys. Make the Cloud project
     with a personal Google account instead; it doesn't have to own the Drive folder.
5. **Give both to the Pi:** `sudoedit /etc/display/env` and add, with what you copied:

   ```
   DRIVE_FOLDER_URL=https://drive.google.com/drive/folders/<...>?usp=sharing
   DRIVE_API_KEY=AIza<...>
   ```

- **Anyone who has the folder link can see every photo in it** (that is what "Anyone with
  the link" means). Keep the link in the team password manager and with the team leads;
  don't post it anywhere public. Only put photos in it that the people in them are happy
  to have on the team-room wall.
- **The API key** only lets the Pi read Drive files that are already shared by link, and
  only through the Drive API. Still, keep it in this file and the password manager, not in
  git or a chat. If it leaks: **Credentials** → the key → **Regenerate key**, and put the
  new one here.
- **"Anyone with the link" is greyed out, or only offers "Anyone at <university>"?** The
  university's Google administrators restrict sharing for university accounts, and a
  folder shared only inside the university can't be read with an API key. Make the folder
  under a personal Google account and add the team leads as **Editors**.
- To add photos later, team leads just put them in the folder (from the Drive app on a
  phone works). [UPDATING.md](UPDATING.md) has the details.

## 6. Database and first data

```sh
cd /srv/display && sudo -u displayapp .venv/bin/python scripts/init_db.py
```

It creates `/srv/display/data/display.db`, reads the calendar and the photo folder, and
writes `web/data/*.json`. The last two lines it prints should be like `Calendar: 23
events` and `Photos: 12 photos` (your numbers will differ); anything else there is the
reason the calendar or the folder couldn't be read. A big folder is downloaded over the
next few minutes by `display-publish` (step 8): `Photos: 3 photos, 37 still downloading`
is fine. Running it
again is safe. Anything that touches the database or those folders by hand runs as
`displayapp` (`sudo -u displayapp ...`), so the files keep the right owner.

## 7. nginx

```sh
sudo cp /srv/display/pi/nginx/display.conf /etc/nginx/sites-available/display
sudo ln -sf /etc/nginx/sites-available/display /etc/nginx/sites-enabled/display
sudo rm -f /etc/nginx/sites-enabled/default      # the default site listens on the network
sudo nginx -t && sudo systemctl reload nginx
```

## 8. Services, timers, and the two root helpers

```sh
cd /srv/display
sudo cp pi/systemd/display-* /etc/systemd/system/
sudo install -o root -g root -m 755 pi/display-restart /usr/local/bin/display-restart
sudo install -o root -g root -m 755 scripts/netcheck.sh /usr/local/sbin/display-netcheck
sudo install -o root -g root -m 440 pi/sudoers-display /etc/sudoers.d/display
sudo visudo -c                                   # must say "parsed OK"
sudo systemctl daemon-reload
sudo systemctl enable --now display-admin display-publish display-backup.timer \
    display-netcheck.timer display-reboot.timer
```

- **The calendar and the photo folder** are read every 30 seconds by `display-publish`,
  a small service that runs all the time (`journalctl -u display-publish -n 20`), and
  once when the admin app starts. An event added to or removed from the calendar, or a
  photo put in or taken out of the folder, is on the screen within about a minute with
  nobody touching anything. Changing a link or the key in `/etc/display/env` needs no
  restart of `display-publish`; restart `display-admin` so its **Read … now** buttons use
  the new values too.
- **Why the helpers are copied:** root runs them, so they must not be files that a
  non-root account can edit. They live in root-owned copies. **If you ever change
  `pi/display-restart` or `scripts/netcheck.sh`, re-run their `install` lines.**
- `sudo -l -U displayapp` should list exactly those two commands (test 2.5.6).
- **The network check** pings the router every 5 minutes and restarts the network after
  3 misses in a row. If the campus network blocks ping to the router
  (`ping -c 3 $(ip -4 route show default | awk '{print $3; exit}')` fails even though the
  internet works), turn it off: `sudo systemctl disable --now display-netcheck.timer`.

✅ Checks 3.2.1, 3.2.2, 3.2.5, 2.5.2, 2.5.3, 2.5.6.

## 9. Backup USB stick

This **erases** the stick. Check the device name carefully first.

```sh
lsblk                                            # find the stick, e.g. sda with partition sda1
sudo mkfs.ext4 -L DISPLAYBACKUP /dev/<sda1>
sudo mkdir -p /media/backup
echo 'LABEL=DISPLAYBACKUP /media/backup ext4 defaults,nofail,x-systemd.device-timeout=10s 0 2' \
    | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /media/backup
sudo chown displayapp:displayapp /media/backup
sudo systemctl start display-backup && journalctl -u display-backup -n 5
```

`nofail` lets the Pi boot normally without the stick. Backups then fail loudly on the
Status page instead.

✅ Checks 3.2.4, 3.2.6.

## 10. Kiosk: the screen

As the `display` user:

```sh
mkdir -p ~/.config/labwc
cp /srv/display/pi/labwc-autostart ~/.config/labwc/autostart
sudo reboot
```

After the reboot the display should fill the screen within a minute.

- **Portrait TV:** on the Pi's desktop, **Control Centre → Screens** → rotate → apply.
  Details in [pi-config-notes.md](pi-config-notes.md).
- **Mouse pointer:** the page hides it. If a mouse is plugged in and the pointer still
  shows, see the optional lines in `pi/labwc-autostart`.
- **The TV's own sleep timer:** turn off any "auto power off" / "no signal sleep" setting
  in the TV's menu, and set its input to HDMI.

✅ Checks 3.3.1 – 3.3.6.

## 11. Reliability

```sh
echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt
sudo mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
sudo cp /srv/display/pi/watchdog.conf /etc/systemd/system.conf.d/display-watchdog.conf
sudo cp /srv/display/pi/journald.conf /etc/systemd/journald.conf.d/display.conf
sudo systemctl daemon-reexec && sudo systemctl restart systemd-journald
```

- **Hardware watchdog:** if the Pi freezes, it reboots itself within about 30 seconds.
  (`dtparam=watchdog=on` is already the default on current Pi 4/5 firmware; adding it
  is harmless and covers other models.)
- **Nightly reboot at 04:05:** `display-reboot.timer`, enabled in step 8. The page also
  reloads itself at 04:00.
- **Monthly updates** over SSH, then check 3.3.1 (screen back within a minute):
  `sudo apt update && sudo apt full-upgrade -y && sudo reboot`
- **Read-only overlay filesystem: recommendation, leave it off.** PLAN.md makes this a
  decision for after the first stable week; here is what to weigh. SQLite and the
  atomic JSON writes already survive power cuts (test 3.4.1). And with the overlay on,
  everything written to the SD card disappears at the next boot, including Tailscale's
  login and HTTPS certificate in `/var/lib/tailscale`: the Pi would need re-approving
  after every nightly reboot, and Let's Encrypt refuses more than 5 certificates a week.
- **If the team turns the overlay on anyway** (`sudo raspi-config nonint do_overlayfs 0`,
  then reboot), these must be on a writable disk, such as a second partition or the USB
  stick, mounted in place through `/etc/fstab` *before* enabling it: `/srv/display/data`,
  `/srv/display/web/data`, `/srv/display/web/photos`, `/var/lib/tailscale`, and
  `/media/backup` (already the USB stick). Then repeat tests 3.2.3, 3.4.1 and 2.5.9.
  `sudo raspi-config nonint do_overlayfs 1` turns it off again.
- **After the first stable week, image the SD card.** Shut the Pi down, put the card in a
  laptop, and copy the whole card to an image file (`dd` on Linux or macOS, Win32 Disk
  Imager on Windows). Store it in the team Drive next to this doc's link.

✅ Checks 3.4.1 – 3.4.5.

## 12. Rebuilding onto a new card with the old data

Do steps 1 – 11, then **Restore from the USB backup** in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md). ✅ Check 3.2.7.

---

## Updating the code later

```sh
cd /srv/display && git pull                      # as the display user
.venv/bin/pip install -r requirements.txt        # only needed if requirements.txt changed
sudo systemctl restart display-admin
```

**Moving a Pi that was set up before 2026-09-29 to Google Calendar** (one time):

```sh
cd /srv/display && git pull
.venv/bin/pip install -r requirements.txt        # two new libraries; nothing to compile
sudoedit /etc/display/env                        # add the CALENDAR_ICS_URL line (step 5)
sudo systemctl disable --now display-publish.timer 2>/dev/null; sudo rm -f /etc/systemd/system/display-publish.timer
sudo cp pi/systemd/display-publish.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now display-publish && sudo systemctl restart display-admin
journalctl -u display-publish -n 5
```

Events that were typed into the old admin pages are deleted by this; photos and settings
are kept.

**Moving a Pi that was set up before 2026-10-06 to photos from Google Drive** (one time):

```sh
cd /srv/display && git pull                      # no new libraries this time
sudoedit /etc/display/env                        # add DRIVE_FOLDER_URL and DRIVE_API_KEY (step 5)
sudo cp pi/nginx/display.conf /etc/nginx/sites-available/display && sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart display-admin display-publish
journalctl -u display-publish -n 5
```

Photos uploaded through the old admin page are not carried over: put the ones you want
to keep in the Drive folder (the backup on the USB stick has them, in `photos-*.tar`).
Their old copies on the Pi are removed the first time the folder is read.

- If a file in `pi/` changed, copy it again as in the step that installed it.
- The screen picks up changes to `web/` at its 04:00 reload, or straight away with
  **Status → Restart display**.
