#!/bin/sh
# Build the release zips into dist/:
#   pvm-jig-mister-<version>.zip  laid out like the MiSTer's SD card (pvm-jig/ + Scripts/), with pyserial and
#                                 tomli bundled for the MiSTer's Python 3.9 (it has no pip)
#   pvm-jig-host-<version>.zip    the host tools for a computer (pvm-jig/: CLI, Tk panel, web panel, models);
#                                 install the packages with: pip install -r pvm-jig/requirements.txt
# Usage: sh tools/build-release.sh [version]   (default: __version__ in host/pvm/__init__.py)
# Needs python3 with pip, and zip. Used by .github/workflows/build.yml.
set -e
cd "$(dirname "$0")/.."
VERSION=${1:-$(python3 -c "import sys; sys.path.insert(0, 'host'); from pvm import __version__; print(__version__)")}
B=build
DIST=dist

# leave out caches, pip metadata, hidden files, and any settings or profiles saved while testing
clean() {
    find "$1" \( -name '*.dist-info' -o -name '__pycache__' -o -name '.*' \) -prune -exec rm -rf {} +
    find "$1" \( -name pvm-jig-web.json -o -name pvm-web.json -o -name 'profiles-*.json' \) -delete
}

rm -rf "$B/mister" "$B/host"
mkdir -p "$B/mister/Scripts" "$B/host/pvm-jig" "$DIST"

# MiSTer: --python-version makes pip evaluate the tomli marker for Python 3.9 (needs --only-binary=:all:)
python3 -m pip install --quiet --target "$B/mister/pvm-jig" --python-version 3.9 --only-binary=:all: \
    -r host/requirements.txt
cp -R host/. "$B/mister/pvm-jig/"
mv "$B/mister/pvm-jig/scripts/mister-menu.sh" "$B/mister/Scripts/pvm-jig.sh"
rm -rf "$B/mister/pvm-jig/pvm-jig-panel.py" "$B/mister/pvm-jig/requirements.txt" "$B/mister/pvm-jig/bin"
cp LICENSE "$B/mister/pvm-jig/"
clean "$B/mister"

# host tools: everything in host/ except the MiSTer scripts
cp -R host/. "$B/host/pvm-jig/"
rm -rf "$B/host/pvm-jig/scripts"
cp LICENSE "$B/host/pvm-jig/"
clean "$B/host"

MISTER_ZIP="$DIST/pvm-jig-mister-$VERSION.zip"
HOST_ZIP="$DIST/pvm-jig-host-$VERSION.zip"
rm -f "$MISTER_ZIP" "$HOST_ZIP"
(cd "$B/mister" && zip -qrX "../../$MISTER_ZIP" pvm-jig Scripts)
(cd "$B/host" && zip -qrX "../../$HOST_ZIP" pvm-jig)
echo "$MISTER_ZIP"
echo "$HOST_ZIP"
