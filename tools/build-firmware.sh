#!/bin/sh
# Build the firmware zip into dist/: pvm-jig-firmware-<version>.zip with the compiled hex for the classic
# Arduino Nano (ATmega328P; the same hex for the old and the new bootloader, only the upload speed differs),
# the sketch, readme.txt (wiring, flashing, protocol) and the LICENSE.
# Usage: sh tools/build-firmware.sh [version]   (default: __version__ in host/pvm/__init__.py)
# Needs arduino-cli with the arduino:avr core (>= 1.8.3), and zip. Used by .github/workflows/build.yml.
set -e
cd "$(dirname "$0")/.."
VERSION=${1:-$(python3 -c "import sys; sys.path.insert(0, 'host'); from pvm import __version__; print(__version__)")}
B=build/firmware
OUT=$B/pvm-jig-firmware
DIST=dist

rm -rf "$B"
mkdir -p "$OUT" "$DIST"
arduino-cli compile -b arduino:avr:nano --output-dir "$B/compile" firmware/pvm_jig
cp "$B/compile/pvm_jig.ino.hex" "$OUT/pvm_jig-nano.hex"
cp -R firmware/pvm_jig "$OUT/"
cp firmware/readme.txt LICENSE "$OUT/"
find "$OUT" -name '.*' -prune -exec rm -rf {} +

ZIP="$DIST/pvm-jig-firmware-$VERSION.zip"
rm -f "$ZIP"
(cd "$B" && zip -qrX "../../$ZIP" pvm-jig-firmware)
echo "$ZIP"
