import os
import re
try:
    import tomllib
except ImportError:  # Python < 3.11: same API in the tomli package
    import tomli as tomllib

from .calc import Calc


def bitfield(bits):
    hi, lo = bits
    return ((1 << (hi - lo + 1)) - 1) << lo, lo


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


class UnsafeValue(ValueError):
    pass


class Item:
    def __init__(self, key, d):
        self.key = key
        self.label = d.get("label", key.upper())
        self.menu = d.get("menu")
        self.title = self.label if self.menu is None else "%s (%02d)" % (self.label, self.menu)
        self.group = d.get("group", "")
        self.when = d.get("when")
        self.refresh = d.get("refresh", True)
        self.eeprom = d.get("eeprom")
        self.vdp = d.get("vdp")
        self.signed = d.get("signed", False)
        self.osd = d.get("osd")
        self.calc = []
        self.help = d.get("help", "")
        self.danger = d.get("danger")
        self.min = d.get("min")
        self.max = d.get("max")

    def safe(self, value):
        v = self.decimal(value)
        return (self.min is None or v >= self.min) and (self.max is None or v <= self.max)

    def safe_range(self):
        if self.min is None and self.max is None:
            return ""
        return "%s to %s" % ("-" if self.min is None else self.min, "-" if self.max is None else self.max)

    @property
    def live(self):
        return self.vdp is not None and self.when is None

    def limit(self):
        e = self.eeprom
        if "bits" in e:
            return (1 << (e["bits"][0] - e["bits"][1] + 1)) - 1
        return (1 << (8 * e.get("size", 1))) - 1

    def location(self):
        e = self.eeprom
        s = "%03X" % e["at"]
        if e.get("size", 1) == 2:
            s += "-%03X" % (e["at"] + 1)
        if "bits" in e:
            s += "[%d:%d]" % tuple(e["bits"])
        return s

    def target(self):
        v = self.vdp
        if not v:
            return ""
        s = "FP %03X" % v["fp"] if "fp" in v else "reg %02X" % v["reg"]
        if "bits" in v:
            s += "[%d:%d]" % tuple(v["bits"])
        return s

    def width(self):
        return 4 if self.eeprom.get("size", 1) == 2 and "bits" not in self.eeprom else 2

    def decimal(self, value):
        bits = 4 * self.width() if self.signed is True else self.signed
        if bits and value >= 1 << (bits - 1):
            return value - (1 << bits)
        return value

    def display(self, value):
        s = "%0*X (%d)" % (self.width(), value, self.decimal(value))
        if self.osd and self.osd.get("offset", 0) | self.osd.get("shift", 0):
            s += " OSD %s" % self.osd_value(value)
        return s

    def parse(self, text, current):
        try:
            return self._parse(text, current)
        except ValueError as e:
            raise ValueError(e.args[0] if "OSD" in str(e) else "bad value: %s" % text)

    def _parse(self, text, current):
        t = text.strip().lower()
        sign = {"+": 1, "-": -1}.get(t[:1])
        if sign:
            t = t[1:]
        if t.startswith("osd:"):
            if self.osd is None:
                raise ValueError("%s has no OSD encoding, use hex or d:" % self.label)
            v = int(t[4:], 16 if self.osd.get("hex") else 10) << self.osd.get("shift", 0)
            if not sign:
                v += self.osd.get("offset", 0) << self.osd.get("shift", 0)
        elif t.startswith("d:"):
            v = int(t[2:], 10)
        else:
            v = int(t, 16)
        value = current() + sign * v if sign else v
        if value < 0 and self.signed:
            value += 1 << (4 * self.width() if self.signed is True else self.signed)
        return value

    def osd_value(self, value):
        o = self.osd
        v = (value >> o.get("shift", 0)) - o.get("offset", 0)
        return ("%X" if o.get("hex") else "%d") % v


def merge(base, over):
    for section, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(section), dict):
            for key, v in value.items():
                old = base[section].get(key)
                base[section][key] = {**old, **v} if isinstance(v, dict) and isinstance(old, dict) else v
        else:
            base[section] = value
    return base


def load(path):
    with open(path, "rb") as f:
        d = tomllib.load(f)
    base = d.pop("base", None)
    if base:
        d = merge(load(os.path.join(os.path.dirname(path), base)), d)
    return d


class Model:
    def __init__(self, path):
        d = load(path)
        m = d["model"]
        self.name = m["name"]
        self.mcu = m.get("mcu", "")
        self.firmware = m.get("firmware", "")
        self.vdp_addr = m.get("vdp", 0x45)
        self.context = d.get("context", {})
        self.protect = set(m.get("protect", []))
        self.danger_regs = {}
        for key, text in {**m.get("danger_regs", {}), **d.get("danger_regs", {})}.items():
            kind, addr = key.lower().split()
            self.danger_regs[(kind, int(addr, 16))] = text
        self.items = {k: Item(k, v) for k, v in d["items"].items()}
        self.calc = {k: Calc(k, v) for k, v in d.get("calc", {}).items()}
        for c in self.calc.values():
            for key in sorted(c.uses):
                if key not in self.items:
                    raise KeyError("calc.%s: unknown item %s" % (c.name, key))
                self.items[key].calc.append(c.name)

    def can_live(self, item):
        return bool(item.calc) and all(self.calc[n].live and self.calc[n].computable for n in item.calc)

    def calc_values(self, name, field, value_of):
        c = self.calc[name]
        env = {k: self.items[k].decimal(value_of(self.items[k])) for k in c.uses}
        return c.values(env, field)

    def reg_danger(self, kind, addr):
        return self.danger_regs.get((kind, addr))

    def raw_danger(self, line):
        t = line.lower().split()
        try:
            if len(t) < 3 or t[0] != "o" or int(t[1], 16) != self.vdp_addr:
                return None
            reg = int(t[2], 16)
            if reg == 0x27 and len(t) >= 5:
                return self.reg_danger("fp", int(t[3], 16) << 8 | int(t[4], 16))
            return self.reg_danger("reg", reg)
        except ValueError:
            return None

    def find(self, name):
        n = norm(name)
        for item in self.items.values():
            if n in (norm(item.key), norm(item.label)):
                return item
        raise KeyError("unknown item: %s" % name)

    def groups(self):
        out = []
        for item in self.items.values():
            if item.group not in out:
                out.append(item.group)
        return out

    def decode(self, item, data):
        e = item.eeprom
        raw = int.from_bytes(data[e["at"]:e["at"] + e.get("size", 1)], "little")
        if "bits" in e:
            mask, shift = bitfield(e["bits"])
            return (raw & mask) >> shift
        return raw

    def ee_get(self, dev, item):
        e = item.eeprom
        return self.decode(item, b"\0" * e["at"] + dev.ee_read(e["at"], e.get("size", 1)))

    def ee_set(self, dev, item, value):
        e = item.eeprom
        size = e.get("size", 1)
        old = dev.ee_read(e["at"], size)
        raw = int.from_bytes(old, "little")
        if "bits" in e:
            mask, shift = bitfield(e["bits"])
            raw = (raw & ~mask) | ((value << shift) & mask)
        else:
            raw = value
        new = raw.to_bytes(size, "little")
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                dev.ee_write(e["at"] + i, bytes([b]))

    def siblings(self, item):
        key = ("fp", item.vdp["fp"]) if "fp" in item.vdp else ("reg", item.vdp["reg"])
        return [i for i in self.items.values()
                if i.live and key in i.vdp.items() and "bits" in i.vdp]

    def vdp_get(self, dev, item):
        v = item.vdp
        if "fp" in v:
            raw = dev.fp_read(v["fp"])
        elif v.get("readable"):
            raw = dev.reg_read(v["reg"], v.get("size", 2))
        else:
            return None
        if "bits" in v:
            mask, shift = bitfield(v["bits"])
            return (raw & mask) >> shift
        return raw

    def vdp_set(self, dev, item, value, current):
        v = item.vdp
        if "bits" in v:
            raw = 0
            for sib in self.siblings(item):
                mask, shift = bitfield(sib.vdp["bits"])
                raw |= ((value if sib is item else current(sib)) << shift) & mask
        else:
            raw = value
        if "fp" in v:
            dev.fp_write(v["fp"], raw)
        else:
            dev.reg_write(v["reg"], raw, v.get("size", 2))


class Session:
    def __init__(self, dev, model):
        self.dev = dev
        self.model = model
        self.pending = {}
        self.ctx = None
        self.calc_offset = {}
        self.calc_last = {}
        self.no_live = set()
        self.applied = {}

    def context(self):
        if self.ctx is None:
            self.dev.hold()
            ctx = {}
            for name, spec in self.model.context.items():
                if "eeprom" in spec:
                    ctx[name] = spec["map"].get(str(self.dev.ee_read(spec["eeprom"], 1)[0]))
                elif "fp" in spec:
                    ctx[name] = spec["then"] if self.dev.fp_read(spec["fp"]) < spec["below"] else spec["else"]
            self.ctx = ctx
        return self.ctx

    def matches(self, when):
        if not when:
            return True
        key, value = when.split("=")
        return self.context().get(key) == value


    def live(self, item):
        if item.key in self.no_live:
            return False
        if item.calc:
            field = self.context().get("field")
            return self.model.can_live(item) and all(
                self.model.calc[n].fields() is None or field in self.model.calc[n].fields() for n in item.calc)
        return bool(item.vdp) and self.matches(item.when)

    def get(self, item):
        if item.key in self.pending:
            return self.pending[item.key]
        return self.model.ee_get(self.dev, item)

    @staticmethod
    def check(item, value, force=False):
        if not 0 <= value <= item.limit():
            raise ValueError("%s: %X out of range 0-%X" % (item.label, value, item.limit()))
        if not force and not item.safe(value):
            raise UnsafeValue("%s: %d is outside the safe range %s (force to write anyway)"
                              % (item.label, item.decimal(value), item.safe_range()))

    def set(self, item, value, force=False):
        self.check(item, value, force)
        self.dev.hold()
        live = self.live(item)
        if live:
            for name in item.calc:
                if self.matches(self.model.calc[name].when):
                    self.calc_write(self.model.calc[name], item, value)
            if item.vdp:
                self.model.vdp_set(self.dev, item, value, self.get)
        self.pending[item.key] = value
        latch = (item.vdp or {}).get("latch") if live else None
        if latch:
            other = self.model.items[latch]
            self.model.vdp_set(self.dev, other, self.get(other), self.get)
        return live

    def read_target(self, c, kind, addr):
        if kind == "fp":
            return self.dev.fp_read(addr)
        return self.dev.reg_read(addr, c.size) if c.readable else None

    def write_target(self, c, kind, addr, value):
        if kind == "fp":
            self.dev.fp_write(addr, value)
        else:
            self.dev.reg_write(addr, value, c.size)

    def calc_write(self, c, item, value):
        field = self.context().get("field")
        mask, half = (1 << c.bits) - 1, 1 << (c.bits - 1)
        if c.name not in self.calc_offset:
            on_vdp = lambda it: self.applied.get(it.key, self.model.ee_get(self.dev, it))
            model_now = self.model.calc_values(c.name, field, on_vdp)
            vdp_now = [self.read_target(c, *t) for t in c.targets]
            if None in vdp_now:
                vdp_now = model_now
            vdp_now = [v & mask for v in vdp_now]
            off = [((v - m + half) & mask) - half for v, m in zip(vdp_now, model_now)]
            if max(abs(o) for o in off) > 4:
                raise ValueError("calc.%s doesn't match the VDP (offsets %s): not writing"
                                 % (c.name, " ".join("%+d" % o for o in off)))
            self.calc_offset[c.name] = off
            self.calc_last[c.name] = vdp_now
        new = self.model.calc_values(c.name, field, lambda it: value if it is item else self.get(it))
        out = [(n + o) & mask for n, o in zip(new, self.calc_offset[c.name])]
        for t, v, last in reversed(list(zip(c.targets, out, self.calc_last[c.name]))):
            if v != last:
                self.write_target(c, *t, v)
                if t[1] in c.mirror and t[0] == "fp":
                    self.dev.fp_write(c.mirror[t[1]], v)
        self.calc_last[c.name] = out
        for key in c.uses:
            self.applied[key] = value if key == item.key else self.get(self.model.items[key])

    def save(self):
        self.dev.hold()
        calcs = {n for k in self.pending for n in self.model.items[k].calc}
        for key, value in self.pending.items():
            self.model.ee_set(self.dev, self.model.items[key], value)
        for name in calcs:
            at = self.model.calc[name].save_a0
            if at is not None and name in self.calc_last:
                old = self.dev.ee_read(at, 2)
                new = self.calc_last[name][0].to_bytes(2, "little")
                for i in range(2):
                    if old[i] != new[i]:
                        self.dev.ee_write(at + i, new[i:i + 1])
        n = len(self.pending)
        self.pending.clear()
        return n

    def reset(self):
        self.ctx = None
        self.calc_offset = {}
        self.calc_last = {}
        self.applied = {}

    def commit(self):
        self.save()
        self.reset()
        if not self.dev.manual:
            self.dev.bint(False)

    def discard(self):
        self.pending.clear()
        self.reset()
        if not self.dev.manual:
            self.dev.bint(False)
