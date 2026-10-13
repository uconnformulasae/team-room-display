#!/bin/bash
# Show the display page in Chromium, full screen, forever.
#
# Started by the desktop session (pi/labwc-autostart → ~/.config/labwc/autostart).
# If Chromium closes or crashes it is opened again 5 seconds later; the admin page's
# "Restart display" button relies on that. Chromium's messages go to the system journal:
#     journalctl -t kiosk
URL="http://localhost/"

# Don't open the browser until nginx answers: a browser opened on an error page has no
# way back. Normally nginx is up long before the desktop. If it isn't, this waits (and
# says so in the journal once a minute) and the browser opens by itself once nginx is
# fixed, e.g. after `sudo systemctl restart nginx`.
waited=0
until curl -sf -o /dev/null "$URL"; do
    sleep 1
    waited=$((waited + 1))
    if [ $((waited % 60)) -eq 0 ]; then
        logger --tag kiosk "Still waiting for $URL after $waited s (is nginx running?)"
    fi
done

while true; do
    # --incognito: no "Restore pages?" bubble after the power is pulled.
    # --password-store=basic: no "unlock your keyring" prompt after auto-login.
    systemd-cat --identifier=kiosk chromium \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --no-first-run \
        --incognito \
        --password-store=basic \
        --enable-features=OverlayScrollbar \
        --start-maximized \
        --check-for-update-interval=31536000 \
        "$URL"
    logger --tag kiosk "Chromium exited; starting it again in 5 seconds"
    sleep 5
done
