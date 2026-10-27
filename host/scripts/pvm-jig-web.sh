#!/bin/sh
# Start / stop pvm-jig-web.py in the background. On the MiSTer, start it at boot from
# /media/fat/linux/user-startup.sh:   sh /media/fat/pvm-jig/scripts/pvm-jig-web.sh start
# Extra arguments go to pvm-jig-web.py (e.g. start --listen 0.0.0.0:8183). The log is in RAM (/tmp), not on the SD card.

DIR=$(cd "$(dirname "$0")/.." && pwd)  # the pvm-jig folder (pvm-jig-web.py)
PIDFILE=/tmp/pvm-jig-web.pid
LOG=/tmp/pvm-jig-web.log

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

cmd=$1
[ $# -gt 0 ] && shift
case "$cmd" in
start)
    if running; then
        echo "pvm-jig-web already running (pid $(cat "$PIDFILE"))"
        exit 0
    fi
    cd "$DIR" || exit 1
    nohup python3 -u pvm-jig-web.py "$@" > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "pvm-jig-web started (pid $!), log $LOG"
    ;;
stop)
    if ! running; then
        echo "pvm-jig-web not running"
        rm -f "$PIDFILE"
        exit 0
    fi
    kill "$(cat "$PIDFILE")"  # SIGTERM: pvm-jig-web reverts an edit and releases BINT before it exits
    for _ in 1 2 3 4 5; do
        running || break
        sleep 1
    done
    rm -f "$PIDFILE"
    echo "pvm-jig-web stopped"
    ;;
restart)
    sh "$0" stop  # through sh: the SD card may not keep the exec bit
    sh "$0" start "$@"
    ;;
status)
    if running; then
        echo "pvm-jig-web running (pid $(cat "$PIDFILE")), log $LOG"
    else
        echo "pvm-jig-web not running"
        exit 1
    fi
    ;;
*)
    echo "usage: $0 start|stop|restart|status [pvm-jig-web.py arguments]"
    exit 2
    ;;
esac
