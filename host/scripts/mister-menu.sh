#!/bin/sh
# OSD Scripts menu entry: goes on the SD card as /media/fat/Scripts/pvm-jig.sh. Installs or updates pvm-jig,
# (re)starts the web panel and shows its address. The files go in /media/fat/pvm-jig.

if [ -f /media/fat/pvm-jig/scripts/install.sh ]; then
    sh /media/fat/pvm-jig/scripts/install.sh
else
    echo "pvm-jig isn't in /media/fat/pvm-jig."
    echo "Unzip the pvm-jig zip onto the root of the SD card, then run this again."
fi
