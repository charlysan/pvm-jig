#!/usr/bin/env python3
"""pvm-jig: control and debug a Sony PVM over the Nano I2C bridge. See README.md and architecture.md."""

import argparse
import os
import shlex
import sys

from pvm import __version__
from pvm.device import EEPROM_SIZE, Device, format_dump, hexbytes, parse_dump
from pvm.link import Link, LinkError, find_port
from pvm.model import Model, Session

MODELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
DEFAULT_MODEL = "pvm-14n2u"
BUS_CMDS = {"ee", "fp", "reg", "i2c", "list", "get", "set", "commit", "discard"}


def num(s):
    return int(s, 16)


def model_path(name):
    return name if os.path.exists(name) else os.path.join(MODELS, name + ".toml")


class Ctx:
    def __init__(self, port, model, manual=False):
        self.port = port
        self.model = Model(model_path(model))
        self.manual = manual
        self.shell = False
        self.failed = False
        self._session = None

    @property
    def session(self):
        if self._session is None:
            link = Link(self.port)
            print("connected %s: %s" % (link.port, link.banner), file=sys.stderr)
            self._session = Session(Device(link, self.model.vdp_addr), self.model)
            if self.manual:
                self._session.dev.set_manual(True)
        return self._session

    @property
    def dev(self):
        return self.session.dev




def show(ctx, item, live=False):
    value = ctx.model.ee_get(ctx.dev, item)
    s = "%-24s %-14s %-18s" % (item.title, item.location(), item.display(value))
    if item.key in ctx.session.pending:
        s += "  pending %s" % item.display(ctx.session.pending[item.key])
    if item.vdp:
        s += "   -> %s" % item.target()
        if item.when:
            s += " (%s)" % item.when
        if live and ctx.session.live(item):
            v = ctx.model.vdp_get(ctx.dev, item)
            s += " = %s" % ("?" if v is None else "%0*X" % (item.width(), v))
    print(s)


def cmd_ports(ctx, a):
    from serial.tools import list_ports
    for p in list_ports.comports():
        print(p.device, "*" if p.device == find_port() else "")


def cmd_info(ctx, a):
    s = ctx.session
    print("tool   pvm-jig %s" % __version__)
    print("model  %s (%s, firmware %s)" % (ctx.model.name, ctx.model.mcu or "?", ctx.model.firmware or "?"))
    print("port   %s (%s)" % (s.dev.link.port, s.dev.link.banner))
    print("bint   %s, %s mode" % ("asserted" if s.dev.bint() else "released",
                                  "manual" if s.dev.manual else "auto"))


def cmd_bint(ctx, a):
    dev = ctx.dev
    if a.state in ("manual", "auto"):
        dev.set_manual(a.state == "manual")
    elif a.state:
        dev.bint(a.state == "on")
    print("%s, %s mode" % ("asserted" if dev.bint() else "released", "manual" if dev.manual else "auto"))
    if dev.manual and not dev.bint():
        print("note: BINT released, the MCU may use the bus; only bus 1 (EEPROM) is reachable", file=sys.stderr)


def cmd_scan(ctx, a):
    print(" ".join("%02X" % x for x in ctx.dev.scan(a.lo, a.hi)) or "none")


def guard(text, force, what):
    if text and not force:
        raise ValueError("%s: %s (use --force to write anyway)" % (what, text))
    if text:
        print("! %s: %s" % (what, text), file=sys.stderr)


def cmd_raw(ctx, a):
    for line in a.lines:
        guard(ctx.model.raw_danger(line), a.force, line)
    for line in a.lines:
        print(ctx.dev.raw(line))


def cmd_ee(ctx, a):
    dev = ctx.dev
    dev.hold()
    if a.op == "read":
        off = num(a.args[0])
        n = num(a.args[1]) if len(a.args) > 1 else 1
        print(format_dump(dev.ee_read(off, n), off) if n > 16 else hexbytes(dev.ee_read(off, n)))
    elif a.op == "write":
        off = num(a.args[0])
        data = bytes(num(x) for x in a.args[1:])
        if not data:
            raise ValueError("ee write OFF BYTE...")
        hit = ctx.model.protect & set(range(off, off + len(data)))
        if hit and not a.force:
            raise ValueError("protected offset %s, use --force" % " ".join("%03X" % o for o in hit))
        dev.ee_write(off, data)
        print(hexbytes(dev.ee_read(off, len(data))))
    elif a.op == "dump":
        text = format_dump(dev.ee_read(0, EEPROM_SIZE))
        if a.args:
            with open(a.args[0], "w") as f:
                f.write(text + "\n")
            print("saved", a.args[0])
        else:
            print(text)
    elif a.op in ("diff", "restore"):
        ref = parse_dump(a.args[0])
        diff = dev.ee_diff(ref)
        for i, now, new in diff:
            print("%03X: now %02X  file %02X" % (i, now, new))
        print("%d bytes differ" % len(diff))
        if a.op == "restore" and diff:
            if not a.force and input("write %d bytes? [y/N] " % len(diff)).lower() != "y":
                return
            left = dev.ee_restore(ref, diff)
            if left:
                raise ValueError("verify failed at %s" % " ".join("%03X" % i for i in left))
            print("restored")


def cmd_fp(ctx, a):
    dev = ctx.dev
    dev.hold()
    if a.op == "read":
        for x in a.args:
            print("FP %03X = %04X" % (num(x), dev.fp_read(num(x))))
    else:
        guard(ctx.model.reg_danger("fp", num(a.args[0])), a.force, "FP %03X" % num(a.args[0]))
        dev.fp_write(num(a.args[0]), num(a.args[1]))
        print("FP %03X = %04X" % (num(a.args[0]), dev.fp_read(num(a.args[0]))))


def cmd_reg(ctx, a):
    dev = ctx.dev
    dev.hold()
    if a.op == "read":
        print("reg %02X = %0*X" % (num(a.args[0]), 2 * a.size, dev.reg_read(num(a.args[0]), a.size)))
    else:
        guard(ctx.model.reg_danger("reg", num(a.args[0])), a.force, "reg %02X" % num(a.args[0]))
        dev.reg_write(num(a.args[0]), num(a.args[1]), a.size)


def cmd_i2c(ctx, a):
    dev = ctx.dev
    dev.hold()
    if a.op == "read":
        n = num(a.args[2]) if len(a.args) > 2 else 1
        print(hexbytes(dev.i2c_read(num(a.args[0]), num(a.args[1]), n)))
    else:
        line = "o " + " ".join(a.args)
        guard(ctx.model.raw_danger(line), a.force, line)
        dev.i2c_write(num(a.args[0]), bytes(num(x) for x in a.args[1:]))


def cmd_list(ctx, a):
    ctx.dev.hold()
    for group in ctx.model.groups():
        if a.group and a.group != group:
            continue
        print("[%s]" % group)
        for item in ctx.model.items.values():
            if item.group == group:
                show(ctx, item, a.live)


def cmd_get(ctx, a):
    ctx.dev.hold()
    item = ctx.model.find(a.item)
    show(ctx, item, live=True)
    if item.help:
        print("  " + item.help)
    for name in item.calc:
        c = ctx.model.calc[name]
        print("  calc %s%s: %s" % (name, "" if c.live else " (not live)", c.formula()))
        if c.note:
            print("    " + c.note)
    if item.danger:
        print("  ! %s: %s" % (item.danger["level"].upper(), item.danger["text"]))
    if item.safe_range():
        print("  safe range: %s" % item.safe_range())


def cmd_set(ctx, a):
    s = ctx.session
    args = [a.item] + a.value
    commit = any(t in ("-c", "--commit") for t in args)
    force = any(t in ("-f", "--force") for t in args)
    args = [t for t in args if t not in ("-c", "--commit", "-f", "--force")]
    if not args or len(args) % 2:
        raise ValueError("set ITEM VALUE [ITEM VALUE ...] [-c] [-f]")
    items = [ctx.model.find(name) for name in args[0::2]]
    if len({item.key for item in items}) != len(items):
        raise ValueError("an item is given twice")
    s.dev.hold()
    # check every value before writing any, so a bad one writes nothing
    todo = []
    for item, text in zip(items, args[1::2]):
        value = item.parse(text, lambda item=item: s.get(item))
        s.check(item, value, force)
        todo.append((item, value))
    for item, value in todo:
        if item.danger:
            print("! %s: %s" % (item.danger["level"].upper(), item.danger["text"]), file=sys.stderr)
        live = s.set(item, value, force)
        how = "(live)" if live else "(EEPROM only, applied on commit)" if ctx.shell and not commit else "(EEPROM only)"
        print("%s = %s %s" % (item.label, item.display(value), how))
    if commit or not ctx.shell:
        s.commit()
        print("committed" + ("" if s.dev.manual else ", MCU reloads"))


def cmd_pending(ctx, a):
    for key, value in ctx.session.pending.items():
        item = ctx.model.items[key]
        print("%-24s %s" % (item.title, item.display(value)))


def cmd_commit(ctx, a):
    n = len(ctx.session.pending)
    ctx.session.commit()
    print("%d items written%s" % (n, ", release BINT to apply" if ctx.dev.manual else ", MCU reloads"))


def cmd_discard(ctx, a):
    ctx.session.discard()
    print("discarded" + ("" if ctx.dev.manual else ", MCU reloads"))


def cmd_shell(ctx, a):
    try:
        import readline  # noqa: F401
    except ImportError:
        pass
    ctx.shell = True
    sp = build(shell=True)
    while True:
        try:
            line = input("pvm-jig> ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue
        argv = shlex.split(line)
        if not argv:
            continue
        if argv[0] in ("quit", "exit"):
            break
        if argv[0] == "help":
            sp.print_help()
            continue
        try:
            run(ctx, sp.parse_args(argv))
        except SystemExit:
            pass
    finish(ctx)


def build(shell=False):
    p = argparse.ArgumentParser(prog="" if shell else "pvm-jig", add_help=not shell,
                                description="Control and debug a Sony PVM through the Nano I2C bridge. "
                                            "All numbers are hex.")
    if not shell:
        p.add_argument("--version", action="version", version="pvm-jig " + __version__)
        p.add_argument("-p", "--port", help="serial port (default: first USB serial port)")
        p.add_argument("-m", "--model", default=DEFAULT_MODEL, help="model name or .toml path")
        p.add_argument("--manual", action="store_true",
                       help="never touch BINT; bus commands run whatever its state (BINT gate off)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial ports").set_defaults(fn=cmd_ports)
    sub.add_parser("info", help="model, port, BINT state").set_defaults(fn=cmd_info)
    c = sub.add_parser("bint", help="show or set BINT: on | off | manual | auto")
    c.add_argument("state", nargs="?", choices=["on", "off", "manual", "auto"])
    c.set_defaults(fn=cmd_bint)
    c = sub.add_parser("scan", help="scan I2C addresses (lo hi)")
    c.add_argument("lo", nargs="?", type=num, default=0x08)
    c.add_argument("hi", nargs="?", type=num, default=0x77)
    c.set_defaults(fn=cmd_scan)
    c = sub.add_parser("raw", help="send protocol lines to the Nano, e.g. raw \"i 45 29\"")
    c.add_argument("lines", nargs="+")
    c.add_argument("-f", "--force", action="store_true", help="write danger registers anyway")
    c.set_defaults(fn=cmd_raw)
    c = sub.add_parser("ee", help="eeprom: read OFF [N] | write OFF BYTE.. | dump [FILE] | diff FILE | restore FILE")
    c.add_argument("op", choices=["read", "write", "dump", "diff", "restore"])
    c.add_argument("args", nargs="*")
    c.add_argument("-f", "--force", action="store_true", help="allow protected offsets / no prompt")
    c.set_defaults(fn=cmd_ee)
    c = sub.add_parser("fp", help="VDP FP RAM: read ADDR.. | write ADDR VALUE")
    c.add_argument("op", choices=["read", "write"])
    c.add_argument("args", nargs="+")
    c.add_argument("-f", "--force", action="store_true", help="write danger registers anyway")
    c.set_defaults(fn=cmd_fp)
    c = sub.add_parser("reg", help="VDP direct register: read REG | write REG VALUE")
    c.add_argument("op", choices=["read", "write"])
    c.add_argument("args", nargs="+")
    c.add_argument("-s", "--size", type=int, default=2, choices=[1, 2], help="register size in bytes")
    c.add_argument("-f", "--force", action="store_true", help="write danger registers anyway")
    c.set_defaults(fn=cmd_reg)
    c = sub.add_parser("i2c", help="any device: read DEV REG [N] | write DEV BYTE..")
    c.add_argument("op", choices=["read", "write"])
    c.add_argument("args", nargs="+")
    c.add_argument("-f", "--force", action="store_true", help="write danger registers anyway")
    c.set_defaults(fn=cmd_i2c)
    c = sub.add_parser("list", help="items and their values")
    c.add_argument("group", nargs="?")
    c.add_argument("-l", "--live", action="store_true", help="also read the VDP values")
    c.set_defaults(fn=cmd_list)
    c = sub.add_parser("get", help="one item: EEPROM and VDP value")
    c.add_argument("item")
    c.set_defaults(fn=cmd_get)
    c = sub.add_parser("set", help="set items: ITEM VALUE [ITEM VALUE ...], values 1F | 0x1F | d:31 | osd:28, or relative +N / -d:N / +osd:N; all are checked before any is written, one commit; -c commits, -f forces unsafe values")
    c.add_argument("item")
    c.add_argument("value", nargs=argparse.REMAINDER, help="value, more ITEM VALUE pairs, then optional -c / --commit")
    c.set_defaults(fn=cmd_set)
    sub.add_parser("pending", help="items set but not committed").set_defaults(fn=cmd_pending)
    sub.add_parser("commit", help="write pending items to EEPROM, release BINT").set_defaults(fn=cmd_commit)
    sub.add_parser("discard", help="drop pending items, release BINT").set_defaults(fn=cmd_discard)
    if not shell:
        sub.add_parser("shell", help="interactive session (keeps BINT and pending items)").set_defaults(fn=cmd_shell)
    return p


def run(ctx, a):
    try:
        a.fn(ctx, a)
    except (LinkError, ValueError, KeyError, IndexError, OSError) as e:
        print("error:", e.args[0] if e.args else e, file=sys.stderr)
        ctx.failed = True


def finish(ctx):
    if ctx._session is None:
        return
    sys.stdout.flush()
    s = ctx._session
    try:
        if s.pending:
            print("%d pending items discarded" % len(s.pending), file=sys.stderr)
        if s.dev.bint():
            s.pending.clear()
            s.dev.bint(False)
            print("BINT released, MCU reloads", file=sys.stderr)
    finally:
        s.dev.link.close()


def main():
    a = build().parse_args()
    try:
        ctx = Ctx(a.port, a.model, a.manual)
    except (OSError, KeyError) as e:
        sys.exit("error: %s" % e)
    run(ctx, a)
    if a.cmd != "shell":
        finish(ctx)
    sys.exit(1 if ctx.failed else 0)


if __name__ == "__main__":
    main()
