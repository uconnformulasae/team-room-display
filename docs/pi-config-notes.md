# Notes on /boot/firmware/config.txt and friends

(Was `pi/config.txt.notes.md`; moved here because everything else in `pi/` is a file that gets installed on the Pi.)

Nothing in `config.txt` is required for the display. These notes cover the settings you
might look for, and what to use instead on current Raspberry Pi OS (trixie, with the
labwc desktop and the KMS graphics driver). Checked on 2026-09-22.

## Watchdog

SETUP.md adds `dtparam=watchdog=on`, as PLAN.md asks. Current Pi 4 and Pi 5 firmware
already has the watchdog on, so the line is only a harmless belt-and-braces. What matters
is telling systemd to use it: `pi/watchdog.conf` goes in `/etc/systemd/system.conf.d/`.
Keep `RuntimeWatchdogSec` at 15 or lower; that is the hardware's limit.

## Screen rotation (portrait TV)

`display_rotate=` / `display_hdmi_rotate=` in config.txt **do nothing** on current Raspberry
Pi OS. Instead, on the Pi's desktop:

- **Control Centre → Screens**, rotate the output, apply. This saves
  `~/.config/kanshi/config`, which the desktop applies at every login. That's all you need.
- Or write that file by hand (the output name is shown by `wlr-randr`):

  ```
  profile {
      output HDMI-A-1 enable transform 90
  }
  ```

  `transform 270` rotates the other way.

## TV turned off or unplugged at boot

If the Pi boots while the TV is off, it may pick a low resolution or no output at all.
If test 3.3.5 (replug HDMI, power-cycle the TV) fails, force the HDMI output on by adding
this to the end of the single line in `/boot/firmware/cmdline.txt`:

```
video=HDMI-A-1:1920x1080@60D
```

(Use your TV's real resolution; the `D` means "enabled even if nothing is detected".)

## Bluetooth off

Not needed by the display. Add to `/boot/firmware/config.txt` under `[all]`:

```
dtoverlay=disable-bt
```

and run `sudo systemctl disable --now hciuart bluetooth`.

## Power

Use the official supply: 27 W USB-C for a Pi 5, 15 W USB-C for a Pi 4. Never power the Pi
from the TV's USB port. `vcgencmd get_throttled` should print `throttled=0x0`; the admin
Status page shows the same thing under **Power**.
