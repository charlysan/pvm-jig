#!/bin/sh
# Stop the web panel and remove it from the boot script. The files stay (with your settings and profiles:
# pvm-jig-web.json, profiles-*.json) unless you pass --all, which also deletes this folder and the Scripts entry.
# Usage: sh /media/fat/pvm-jig/scripts/uninstall.sh [--all]

DIR=$(cd "$(dirname "$0")/.." && pwd)  # the pvm-jig folder
STARTUP=/media/fat/linux/user-startup.sh
MENU=/media/fat/Scripts/pvm-jig.sh

sh "$DIR/scripts/pvm-jig-web.sh" stop

if [ -f "$STARTUP" ] && grep -q -e "pvm-jig-web.sh start" -e "pvm-web.sh start" "$STARTUP"; then
    grep -v -e "pvm-jig-web.sh start" -e "pvm-web.sh start" -e "^# pvm-jig web panel$" "$STARTUP" > "$STARTUP.tmp" \
        && mv "$STARTUP.tmp" "$STARTUP"
    echo "Removed from $STARTUP"
else
    echo "Not in $STARTUP"
fi

if [ "$1" = "--all" ]; then
    # only delete a folder that really is pvm-jig
    if [ -f "$DIR/pvm-jig-web.py" ] && [ -d "$DIR/pvm" ]; then
        rm -f "$MENU"
        rm -rf "$DIR"
        echo "Deleted $DIR and $MENU"
    else
        echo "Not deleting $DIR: it doesn't look like a pvm-jig folder"
        exit 1
    fi
else
    echo "Files kept in $DIR (settings and profiles included). To delete them too: sh $DIR/scripts/uninstall.sh --all"
fi
