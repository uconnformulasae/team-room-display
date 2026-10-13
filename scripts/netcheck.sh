#!/bin/bash
# Restart NetworkManager if the router (default gateway) hasn't answered a ping for 3
# checks in a row. display-netcheck.timer runs this every 5 minutes, so that's about
# 15 minutes without a network. Wi-Fi on campus sometimes gets stuck; this unsticks it.
#
# Runs as root, so it is installed as /usr/local/sbin/display-netcheck, owned by root
# (docs/SETUP.md). Re-run that install step if you change this file.
#
# If the campus network blocks ping to the router, this would restart the network for
# no reason. Check with:  ping -c 3 "$(ip -4 route show default | awk '{print $3; exit}')"
# and if that fails while the network works: sudo systemctl disable --now display-netcheck.timer
set -u

COUNT_FILE=/run/display-netcheck.failures
LIMIT=3

gateway=$(ip -4 route show default 2>/dev/null | awk '/default/ {print $3; exit}')
if [ -n "$gateway" ] && ping -c 1 -W 3 "$gateway" > /dev/null 2>&1; then
    echo 0 > "$COUNT_FILE"
    exit 0
fi

failures=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$failures" > "$COUNT_FILE"
echo "Router ${gateway:-(no default route)} did not answer (${failures} of ${LIMIT})"
if [ "$failures" -ge "$LIMIT" ]; then
    echo "Restarting NetworkManager"
    systemctl restart NetworkManager
    echo 0 > "$COUNT_FILE"
fi
