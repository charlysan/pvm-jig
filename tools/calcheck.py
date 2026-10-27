#!/usr/bin/env python3
"""Check the model's [calc] formulas against what the MCU writes to the VDP. See architecture.md.

Usage (project venv, Nano connected, BINT released, nothing else using the Nano):
  .venv/bin/python tools/calcheck.py CALC [CALC...] [options]
  .venv/bin/python tools/calcheck.py --all [options]

  --item KEY            test only this input item (repeatable); default: every item the calc uses
  --points KEY=V,V,...  test these values of KEY instead of the default steps (hex, d:N, osd:N, +N/-N)
  --capture             also capture bus 0 (analyzer) to check write-only registers and watch reg 15
  --model PATH          model file (default host/models/pvm-14n2u.toml)
  --yes                 don't ask before writing

For each input item, one at a time (the others stay as they are): write the EEPROM byte, release BINT (the MCU
reloads and recomputes), assert BINT, read the calc's targets and compare with the formula. Default points are
+1, -1, +2, -2 menu steps from the current value, plus the safe min/max for non-geometry calcs; points outside
the safe range, or (geometry) moving an accumulator by more than MAX_DELTA, are skipped. After each item the byte
is restored and the targets must read back as before the test. Report: dumps/calcheck/<time>_<calcs>.txt.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, HERE)

import automap  # noqa: E402
from pvm.device import EEPROM_SIZE, Device, format_dump  # noqa: E402
from pvm.link import Link  # noqa: E402
from pvm.model import Model, Session  # noqa: E402

MODEL = os.path.join(ROOT, "host/models/pvm-14n2u.toml")
OUT = os.path.join(ROOT, "dumps", "calcheck")
STEPS = (1, -1, 2, -2)
MAX_DELTA = 40
SECONDS_PER_RELOAD = 6


class Report:
    def __init__(self, path):
        self.f = open(path, "w")

    def __call__(self, text=""):
        print(text)
        self.f.write(text + "\n")
        self.f.flush()


def parse_args(a):
    opts = {"calcs": [], "items": [], "points": {}, "capture": False, "all": False, "yes": False, "model": MODEL}
    i = 0
    while i < len(a):
        x = a[i]
        if x in ("--item", "--points", "--model"):
            i += 1
            if x == "--item":
                opts["items"].append(a[i])
            elif x == "--points":
                key, vals = a[i].split("=", 1)
                opts["points"][key] = vals.split(",")
            else:
                opts["model"] = a[i]
        elif x.startswith("--") and x[2:] in ("capture", "all", "yes"):
            opts[x[2:]] = True
        elif x.startswith("--"):
            sys.exit(__doc__)
        else:
            opts["calcs"].append(x)
        i += 1
    if not opts["calcs"] and not opts["all"]:
        sys.exit(__doc__)
    return opts


def raw_of(item, dec):
    if dec < 0 and item.signed:
        dec += 1 << (4 * item.width() if item.signed is True else item.signed)
    return dec


def plan_points(model, c, item, backup, field, opts):
    cur = model.decode(item, backup)
    if item.key in opts["points"]:
        values = [item.parse(t, lambda: cur) for t in opts["points"][item.key]]
    else:
        unit = 1 << (item.osd or {}).get("shift", 0)
        values = [raw_of(item, item.decimal(cur) + s * unit) for s in STEPS]
        if c.kind != "matrix":
            values += [raw_of(item, lim) for lim in (item.min, item.max) if lim is not None]
    others = lambda v: lambda it: v if it is item else model.decode(it, backup)
    base = model.calc_values(c.name, field, others(cur))
    out = []
    for v in dict.fromkeys(values):
        why = ""
        if v == cur:
            continue
        if not 0 <= v <= item.limit():
            why = "out of range"
        elif not item.safe(v) and item.key not in opts["points"]:
            why = "outside the safe range %s" % item.safe_range()
        elif c.kind == "matrix":
            new = model.calc_values(c.name, field, others(v))
            big = max(abs(((n - b + 2048) % 4096) - 2048) for n, b in zip(new, base))
            if big > MAX_DELTA:
                why = "an accumulator would move by %d (limit %d)" % (big, MAX_DELTA)
        out.append((v, why))
    return cur, out


def reload(dev, capture):
    automap.reload(dev)
    state = automap.capture() if capture else None
    dev.bint(True)
    return state


def read_targets(dev, c, state):
    out = []
    for kind, addr in c.targets + [("fp", m) for m in c.mirror.values()]:
        if kind == "fp":
            out.append(dev.fp_read(addr))
        elif c.readable:
            out.append(dev.reg_read(addr, c.size))
        elif state is not None and "45 reg %02X" % addr in state:
            out.append(int(state["45 reg %02X" % addr], 16))
        else:
            out.append(None)
    return out


def compare(c, expect, got):
    mask = (1 << c.bits) - 1
    tol = 1 if c.kind == "matrix" and c.round != "each" else 0
    marks = []
    for e, g in zip(expect, got):
        if g is None:
            marks.append(None)
        else:
            d = (((g & mask) - e + (mask + 1) // 2) & mask) - (mask + 1) // 2
            marks.append(abs(d) <= tol)
    return marks


def names(c):
    return [c.target_name(i) for i in range(len(c.targets))] + ["FP %03X (mirror)" % m for m in c.mirror.values()]


def check_point(log, model, dev, c, item, value, field, opts, backup, ref=None):
    model.ee_set(dev, item, value)
    if model.ee_get(dev, item) != value:
        raise RuntimeError("EEPROM verify failed for %s" % item.key)
    state = reload(dev, opts["capture"])
    if opts["capture"] and "reg15" not in opts:
        opts["reg15"] = state.get("45 reg 15")
    got = read_targets(dev, c, state)
    expect = model.calc_values(c.name, field, lambda it: value if it is item else model.decode(it, backup))
    expect += [expect[c.targets.index(("fp", src))] for src in c.mirror]
    marks = compare(c, expect, got)
    log("  %s = %s%s" % (item.key, item.display(value), "" if item.safe(value) else "  (outside the safe range)"))
    for n, e, g, m in zip(names(c), expect, got, marks):
        log("    %-18s expect %04X  vdp %s  %s" % (n, e, "----" if g is None else "%04X" % g,
                                                   "not checked" if m is None else "ok" if m else "BAD"))
    if c.kind == "matrix" and marks[0] is False and all(m is not False for m in marks[1:len(c.targets)]) \
            and ref is not None and got[0] == ref[0]:
        log("    note: A0 didn't move while A1.. did: the MCU may take A0 from the stored word (0x%02X)" % c.save_a0
            if c.save_a0 is not None else "    note: only A0 is off")
    if opts["capture"] and state.get("45 reg 15") != opts["reg15"]:
        raise RuntimeError("reg 15 (H drive) changed: %s -> %s, stopping" % (opts["reg15"], state.get("45 reg 15")))
    return [m for m in marks if m is not None], got


def main():
    opts = parse_args(sys.argv[1:])
    model = Model(opts["model"])
    calcs = [c for c in model.calc.values() if c.computable] if opts["all"] else [model.calc[n] for n in opts["calcs"]]
    for c in calcs:
        if not c.computable:
            sys.exit("calc.%s has no formula, nothing to check" % c.name)
    for key in list(opts["items"]) + list(opts["points"]):
        if key not in model.items or not any(key in c.uses for c in calcs):
            sys.exit("%s isn't an input of %s" % (key, ", ".join(c.name for c in calcs)))

    dev = Device(Link(), model.vdp_addr)
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    log = Report(os.path.join(OUT, "%s_%s.txt" % (stamp, "_".join(c.name for c in calcs) if len(calcs) < 4 else "all")))
    backup = None
    try:
        dev.bint(True)
        backup = dev.ee_read(0, EEPROM_SIZE)
        if dev.ee_read(0, EEPROM_SIZE) != backup:
            raise RuntimeError("two EEPROM reads differ, not starting")
        with open(os.path.join(OUT, stamp + "_backup.hex"), "w") as f:
            f.write(format_dump(backup) + "\n")
        session = Session(dev, model)
        ctx = session.context()
        field = ctx.get("field")
        log("%s (%s, firmware %s), context %s" % (model.name, model.mcu, model.firmware,
                                                   ", ".join("%s %s" % kv for kv in sorted(ctx.items()))))
        log("backup: dumps/calcheck/%s_backup.hex" % stamp)

        plan = []
        for c in calcs:
            if c.when and not session.matches(c.when):
                log("calc.%s: skipped, only applies when %s" % (c.name, c.when))
                continue
            if c.fields() is not None and field not in c.fields():
                log("calc.%s: skipped, no weights for %s Hz" % (c.name, field))
                continue
            if not opts["capture"] and not any(k == "fp" or c.readable for k, _ in c.targets):
                log("calc.%s: skipped, its targets are write-only registers: use --capture" % c.name)
                continue
            for key in sorted(c.uses):
                if opts["items"] and key not in opts["items"]:
                    continue
                item = model.items[key]
                plan.append((c, item) + plan_points(model, c, item, backup, field, opts))

        n = 0
        log("\nPlan (one item at a time, restored after each):")
        for c, item, cur, points in plan:
            log("  calc.%s  %s now %s" % (c.name, item.key, item.display(cur)))
            for v, why in points:
                note = "skip: " + why if why else "" if item.safe(v) else "(outside the safe range %s)" % item.safe_range()
                log("    %s %s" % (item.display(v), note))
                n += not why
        reloads = n + 2 * len(plan)
        log("%d points, about %d reloads (%d s, one short blank each)" % (n, reloads, reloads * SECONDS_PER_RELOAD))
        if not n:
            return
        if not opts["yes"] and input("Run? [y/N] ").strip().lower() != "y":
            log("not run")
            return

        totals = {}
        for c, item, cur, points in plan:
            log("\ncalc.%s, %s (now %s)" % (c.name, item.key, item.display(cur)))
            ok, ref = check_point(log, model, dev, c, item, cur, field, opts, backup)
            res = totals.setdefault(c.name, [0, 0])
            for v, why in points:
                if not why:
                    ok += check_point(log, model, dev, c, item, v, field, opts, backup, ref)[0]
            model.ee_set(dev, item, cur)
            state = reload(dev, opts["capture"])
            back = read_targets(dev, c, state)
            same = back == ref
            log("  restored %s = %s, targets %s" % (item.key, item.display(cur),
                                                    "back as before" if same else "DIFFER: %s" % back))
            if not same:
                raise RuntimeError("targets didn't come back after restoring %s, stopping" % item.key)
            res[0] += sum(ok)
            res[1] += len(ok) - sum(ok)
        log("\nSummary (context %s):" % ", ".join("%s %s" % kv for kv in sorted(ctx.items())))
        for name, (good, bad) in totals.items():
            log("  calc.%-10s %3d ok  %3d bad" % (name, good, bad))
    finally:
        if backup is not None:
            dev.bint(True)
            d = dev.ee_diff(backup)
            left = dev.ee_restore(backup, d) if d else []
            log("EEPROM vs backup: %d byte(s) differed%s" % (len(d), "" if not left else ", restore FAILED at " + str(left)))
            automap.reload(dev)
        elif dev.bint():
            dev.bint(False)
        dev.link.close()


if __name__ == "__main__":
    main()
