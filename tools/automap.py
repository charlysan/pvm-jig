#!/usr/bin/env python3
"""Automatic mapping: change EEPROM bytes, let the MCU reload, see which VDP values move. See architecture.md.

Usage (project venv, Nano and analyzer connected, nothing else using them):
  .venv/bin/python tools/automap.py baseline NAME            dump + idle capture -> dumps/automap/NAME.{hex,state}
  .venv/bin/python tools/automap.py test NAME OFF BYTE...    write bytes at OFF, reload, capture, diff vs NAME, restore
  .venv/bin/python tools/automap.py test NAME 11=64 52=FD,00 several writes at once (OFF=BYTE,BYTE...)
  .venv/bin/python tools/automap.py fp NAME OFF BYTE... -- FP...   same, but only read FP values with the Nano (no capture)
  .venv/bin/python tools/automap.py restore NAME             write back every byte that differs from NAME.hex

Numbers are hex. Every reload is followed by a check that reg 15 (H drive) and FP 08B-08E (vertical) are
unchanged; if they moved, the test stops and restores.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, HERE)

import validate  # noqa: E402
from pvm.device import EEPROM_SIZE, Device, format_dump, parse_dump  # noqa: E402
from pvm.link import Link  # noqa: E402

OUT = os.path.join(ROOT, "dumps", "automap")
WATCH = ["45 reg 15", "45 FP 08B", "45 FP 08C", "45 FP 08D", "45 FP 08E"]
RELOAD_WAIT = 3.0
CAPTURE_MS = 2500


def capture(ms=CAPTURE_MS):
    path = os.path.join(tempfile.mkdtemp(), "c.sr")
    subprocess.run(["sigrok-cli", "-d", "fx2lafw", "--config", "samplerate=4m", "--time", str(ms),
                    "--channels", "D0=SCL1,D1=SDA1,D2=BINT,D3=SCL0,D4=SDA0", "-o", path],
                   check=True, capture_output=True)
    state = validate.vdp_state(path)
    if not state:
        raise RuntimeError("no bus-0 traffic in the capture (BINT held? probes?)")
    return state


def reload(dev):
    dev.bint(False)
    time.sleep(RELOAD_WAIT)


def write(dev, off, data):
    dev.bint(True)
    dev.ee_write(off, bytes(data))
    if dev.ee_read(off, len(data)) != bytes(data):
        raise RuntimeError("EEPROM verify failed at %03X" % off)


def diff(base, now):
    keys = sorted(set(base) | set(now))
    return [(k, base.get(k), now.get(k)) for k in keys if base.get(k) != now.get(k) and "reg 25" not in k]


def load_base(name):
    with open(os.path.join(OUT, name + ".state")) as f:
        return json.load(f)


def cmd_baseline(dev, name):
    os.makedirs(OUT, exist_ok=True)
    dev.bint(True)
    data = dev.ee_read(0, EEPROM_SIZE)
    reload(dev)
    with open(os.path.join(OUT, name + ".hex"), "w") as f:
        f.write(format_dump(data) + "\n")
    state = capture()
    with open(os.path.join(OUT, name + ".state"), "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)
    print("baseline %s: %d registers, EEPROM sum %s" % (name, len(state), format_dump(data).splitlines()[-1]))


def cmd_test(dev, name, writes, fps=None):
    base = load_base(name)
    dev.bint(True)
    olds = [(off, dev.ee_read(off, len(data))) for off, data in writes]
    label = "  ".join("%03X: %s -> %s" % (off, old.hex(" ").upper(), bytes(data).hex(" ").upper())
                      for (off, data), (_o, old) in zip(writes, olds))
    try:
        for off, data in writes:
            write(dev, off, data)
        if fps:
            dev.bint(False)
            time.sleep(RELOAD_WAIT)
            dev.bint(True)
            print("%s   %s" % (label, "  ".join("FP %03X=%04X" % (fp, dev.fp_read(fp)) for fp in fps)))
            return
        reload(dev)
        now = capture()
        changes = diff(base, now)
        print("%s   %d change(s)" % (label, len(changes)))
        for k, a, b in changes:
            print("   %-12s %s -> %s%s" % (k, a, b, "   ! WATCHED" if k in WATCH else ""))
    finally:
        for off, old in olds:
            write(dev, off, old)
        reload(dev)


def parse_writes(args):
    """'11=64 52=FD,00' or the old form 'OFF BYTE...'."""
    if "=" in args[0]:
        out = []
        for a in args:
            off, data = a.split("=")
            out.append((int(off, 16), [int(x, 16) for x in data.split(",")]))
        return out
    return [(int(args[0], 16), [int(x, 16) for x in args[1:]])]


def cmd_restore(dev, name):
    ref = parse_dump(os.path.join(OUT, name + ".hex"))
    dev.bint(True)
    d = dev.ee_diff(ref)
    left = dev.ee_restore(ref, d) if d else []
    reload(dev)
    print("restore %s: %d byte(s) differed%s" % (name, len(d), "" if not left else ", verify FAILED " + str(left)))


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    dev = Device(Link())
    try:
        if a[0] == "baseline":
            cmd_baseline(dev, a[1])
        elif a[0] in ("test", "fp"):
            fps = None
            if "--" in a:
                i = a.index("--")
                fps, a = [int(x, 16) for x in a[i + 1:]], a[:i]
            cmd_test(dev, a[1], parse_writes(a[2:]), fps)
        elif a[0] == "restore":
            cmd_restore(dev, a[1])
        else:
            sys.exit(__doc__)
    finally:
        if dev.bint():
            dev.bint(False)
        dev.link.close()


if __name__ == "__main__":
    main()
