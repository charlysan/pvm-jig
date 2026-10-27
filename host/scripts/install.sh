#!/bin/sh
# Install or update pvm-jig on the MiSTer: check Python and the bundled packages, start the web panel at boot
# (from /media/fat/linux/user-startup.sh), (re)start it and show its address. Safe to run again after an update.
# From the OSD: Scripts > pvm-jig. Over ssh: sh /media/fat/pvm-jig/scripts/install.sh

DIR=$(cd "$(dirname "$0")/.." && pwd)  # the pvm-jig folder
WEB="$DIR/scripts/pvm-jig-web.sh"
STARTUP=/media/fat/linux/user-startup.sh
TEMPLATE=/media/fat/linux/_user-startup.sh
PORT=8183

echo "pvm-jig: installing from $DIR"
echo

# 1. Python and the bundled packages (pyserial, tomli): nothing to install, they are in this folder
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on this MiSTer."
    exit 1
fi
echo "$(python3 --version 2>&1)"
if ! err=$(cd "$DIR" && python3 -c "import pvm.link, pvm.model" 2>&1); then
    echo "ERROR: the pvm-jig library doesn't load:"
    echo "$err"
    exit 1
fi
echo "Library and bundled packages: OK"

# 2. The Nano: only a warning, the panel connects whenever it appears
ports=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null)
if [ -n "$ports" ]; then
    echo "USB serial port(s): $(echo $ports)"
else
    echo "WARNING: no USB serial port yet. Plug the Nano in; the panel finds it by itself."
fi

# 3. Start at boot: one line in user-startup.sh (created from the MiSTer's template if missing)
if [ ! -f "$STARTUP" ]; then
    if [ -f "$TEMPLATE" ]; then
        cp "$TEMPLATE" "$STARTUP"
    else
        printf '#!/bin/sh\n' > "$STARTUP"
    fi
fi
LINE="[ -f $WEB ] && sh $WEB start"
if grep -qxF "$LINE" "$STARTUP"; then
    echo "Start at boot: already in $STARTUP"
else
    # drop a line from an older install (other path or the old name pvm-web.sh), then add the current one
    grep -v -e "pvm-jig-web.sh start" -e "pvm-web.sh start" -e "^# pvm-jig web panel$" "$STARTUP" > "$STARTUP.tmp" \
        && mv "$STARTUP.tmp" "$STARTUP"
    printf '\n# pvm-jig web panel\n%s\n' "$LINE" >> "$STARTUP"
    echo "Start at boot: added to $STARTUP"
fi

# 4. (Re)start the panel, so an update takes effect. An older version was called pvm-web (pvm-web.py and
#    pvm-web.sh in this folder): stop it if it runs (it would hold the serial port) and remove its files.
echo
if [ -f /tmp/pvm-web.pid ] && kill -0 "$(cat /tmp/pvm-web.pid)" 2>/dev/null; then
    kill "$(cat /tmp/pvm-web.pid)"
    sleep 2
    echo "Stopped the older pvm-web"
fi
rm -f /tmp/pvm-web.pid "$DIR/pvm-web.py" "$DIR/pvm-web.sh"
sh "$WEB" restart
sleep 2
if ! sh "$WEB" status; then
    echo "ERROR: the panel stopped. Last lines of /tmp/pvm-jig-web.log:"
    tail -n 15 /tmp/pvm-jig-web.log
    exit 1
fi

# 5. Where to open it
echo
ips=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
if [ -n "$ips" ]; then
    for ip in $ips; do
        echo "Open http://$ip:$PORT on a phone or computer on the same network."
    done
else
    echo "Open http://<this MiSTer's IP>:$PORT on a phone or computer on the same network."
fi
echo "Log: /tmp/pvm-jig-web.log. Uninstall: sh $DIR/scripts/uninstall.sh"
