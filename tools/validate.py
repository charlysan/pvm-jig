#!/usr/bin/env python3
"""Check a bus-0 capture against an EEPROM dump: does the model explain every VDP value?

Usage: python3 tools/validate.py dumps/<capture>.sr dumps/<eeprom dump> [host/models/<model>.toml]

Capture: idle, BINT released, nothing touched (>= 2 s, so the MCU's 1.3 s refresh writes every value).
Dump: `pvm --manual ee dump FILE` taken around the same time.
Prints one line per check (ok / BAD) and a summary. Needs Python 3.11+ (tomllib).
Everything checked comes from the model file: raw/packed items whose `when` matches the capture's input and
field rate, and every [calc] that has a formula (live or not). The field rate is read from VSDT (FP 0E7).
"""
import os
import sys

import sr_i2c

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))
from pvm.model import Model  # noqa: E402

MODEL = os.path.join(ROOT, "host/models/pvm-14n2u.toml")


def vdp_state(path):
    z, chunks, rate, names = sr_i2c.load(path)
    bus = sr_i2c.I2CBus(0, 1 << names["SCL0"], 1 << names["SDA0"])
    prev = None
    for n, v in sr_i2c.edges(z, chunks):
        if v is None:
            break
        if prev is not None:
            bus.step(n / rate, prev, v)
        prev = v
    return sr_i2c.vdp_changes(bus)[0]


def load_dump(path):
    data = bytearray()
    with open(path) as f:
        for line in f:
            if ":" in line:
                data += bytes(int(x, 16) for x in line.split(":", 1)[1].split())
    return data


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    state = vdp_state(sys.argv[1])
    if not state:
        sys.exit("no bus-0 writes in the capture (BINT held low? probes on SCL0/SDA0?)")
    ee = load_dump(sys.argv[2])
    model = Model(sys.argv[3] if len(sys.argv) > 3 else MODEL)
    field = "50" if state.get("45 FP 0E7") == "0270" else "60"
    ctx = {"field": field}
    for name, spec in model.context.items():
        if "eeprom" in spec:
            ctx[name] = spec["map"].get(str(ee[spec["eeprom"]]))
    matches = lambda when: not when or ctx.get(when.split("=")[0]) == when.split("=")[1]
    value_of = lambda item: model.decode(item, ee)
    res = []

    def check(key, expect, label, tol=0, bits=None, mask=0xFFFF):
        got = state.get(key)
        g = None if got is None else int(got, 16) & mask
        if g is not None and bits:
            g = (g >> bits[1]) & ((1 << (bits[0] - bits[1] + 1)) - 1)
            key += "[%d:%d]" % tuple(bits)
        res.append((g is not None and abs(g - expect) <= tol, key, label, expect, g))

    for item in model.items.values():
        v = item.vdp
        if not v or not matches(item.when):
            continue
        key = "45 FP %03X" % v["fp"] if "fp" in v else "45 reg %02X" % v["reg"]
        if not item.refresh and key not in state:
            continue
        check(key, value_of(item), item.key, bits=v.get("bits"))

    for c in model.calc.values():
        if not c.computable or not matches(c.when) or (c.fields() and field not in c.fields()):
            continue
        out = model.calc_values(c.name, field, value_of)
        for i, v in enumerate(out):
            label = "calc.%s" % c.name + ("[%d]" % i if len(out) > 1 else "")
            check("45 " + c.target_name(i), v, label, 1 if c.kind == "matrix" and c.round != "each" else 0, mask=(1 << c.bits) - 1)
        for src, dst in c.mirror.items():
            if "45 FP %03X" % src in state:
                check("45 FP %03X" % dst, int(state["45 FP %03X" % src], 16), "calc.%s mirror" % c.name)
        if c.save_a0 is not None:
            check("45 " + c.target_name(0), ee[c.save_a0] | ee[c.save_a0 + 1] << 8, "calc.%s saved A0" % c.name)

    for good, key, label, exp, got in res:
        print("%s %-16s %-26s expect %04X  vdp %s" % ("ok " if good else "BAD", key, label, exp,
                                                       "----" if got is None else "%04X" % got))
    bad = sum(not r[0] for r in res)
    print("\n%s, input %s, %s Hz: %d checked, %d ok, %d bad (%d registers in capture)"
          % (model.name, ctx.get("input"), field, len(res), len(res) - bad, bad, len(state)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
