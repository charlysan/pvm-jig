#!/usr/bin/env python3
"""Decode a sigrok .sr capture: I2C transactions on both buses + BINT edges on one timeline.

Usage: python3 tools/sr_i2c.py dumps/<name>.sr [--bus 0|1] [--summary] [--vdp] [--state] [--ee-diff DUMP]

Channel names (as recorded with --channels D0=SCL,D1=SDA,D2=BINT,D3=SCL0,D4=SDA0):
  SCL/SDA   -> bus 1 (service header + EEPROM)
  SCL0/SDA0 -> bus 0 (MCU <-> VDP3108)
  BINT      -> optional
Any pair that is missing is skipped. No numpy/sigrok needed.

Output: one line per transaction, e.g.
    5.515766 +  19.39 ms  b1  S W@50 10 Sr R@50 1C 01 ... 00~ P   (1.85 ms)
'~' = NAK. BINT edges closer than DEBOUNCE are merged (hand-held wire bounces).
--summary prints per-bus/per-address statistics and idle-gap distribution instead.
--vdp prints only bus-0 register values that change during the capture (FP writes resolved via 27/28).
--state prints the last value written to every bus-0 register (the MCU rewrites all of them every 1.3 s,
so any capture longer than that holds the complete VDP state). Diff two captures' states to compare.
--ee-diff DUMP expands every EEPROM write on bus 1 (SAVE pages, user-setting saves) into bytes and prints the
offsets whose last written value differs from DUMP (an EEPROM dump taken before the test), e.g. 060: 39 -> 3A.
"""
import configparser
import sys
import zipfile
from collections import Counter, defaultdict

DEBOUNCE = 0.005  # s
BUSES = {1: ('SCL', 'SDA'), 0: ('SCL0', 'SDA0')}
ALIASES = {'SCL1': 'SCL', 'SDA1': 'SDA'}


def load(path):
    z = zipfile.ZipFile(path)
    meta = configparser.ConfigParser()
    meta.read_string(z.read('metadata').decode())
    dev = meta['device 1']
    value, unit = dev['samplerate'].split()
    rate = float(value) * {'Hz': 1, 'kHz': 1e3, 'MHz': 1e6, 'GHz': 1e9}[unit]
    if dev.get('unitsize', '1') != '1':
        sys.exit('only unitsize=1 supported')
    names = {ALIASES.get(dev[k], dev[k]): int(k[5:]) - 1 for k in dev if k.startswith('probe')}
    prefix = dev['capturefile']
    chunks = sorted((n for n in z.namelist() if n.startswith(prefix + '-')),
                    key=lambda n: int(n.rsplit('-', 1)[1]))
    return z, chunks, rate, names


def edges(z, chunks):
    """Yield (sample_index, value) for every sample where the byte changes."""
    pos, prev = 0, None
    for name in chunks:
        data = z.read(name)
        if prev is not None and data.count(prev) == len(data):
            pos += len(data)  # no change in this chunk
            continue
        for i, v in enumerate(data):
            if v != prev:
                yield pos + i, v
                prev = v
        pos += len(data)
    yield pos, None  # end marker


class I2CBus:
    def __init__(self, num, scl, sda):
        self.num, self.scl, self.sda = num, scl, sda
        self.bits, self.txn, self.t0 = [], None, None
        self.done = []  # (t_start, t_end, tokens)

    def step(self, t, prev, v):
        scl, sda = self.scl, self.sda
        if not ((v ^ prev) & (scl | sda)):
            return
        scl_hi = prev & scl and v & scl
        if scl_hi and (prev & sda) and not (v & sda):  # START / repeated START
            # Before a repeated START the master raises SCL once (sampled as a
            # stray bit), so up to one pending bit still means "repeated".
            if self.txn is not None and len(self.bits) <= 1:
                self.txn.append('Sr')
            else:
                self.txn, self.t0 = ['S'], t
            self.bits = []
        elif scl_hi and not (prev & sda) and (v & sda):  # STOP
            if self.txn is not None:
                self.txn.append('P')
                self.done.append((self.t0, t, self.txn))
            self.txn, self.bits = None, []
        elif self.txn is not None and not (prev & scl) and (v & scl):  # SCL rising
            self.bits.append(1 if v & sda else 0)
            if len(self.bits) == 9:
                b = int(''.join(map(str, self.bits[:8])), 2)
                ack = '' if self.bits[8] == 0 else '~'
                if self.txn[-1] in ('S', 'Sr'):
                    self.txn.append(f"{'R' if b & 1 else 'W'}@{b >> 1:02X}{ack}")
                else:
                    self.txn.append(f'{b:02X}{ack}')
                self.bits = []


def debounce(bint_edges):
    """Merge bursts of BINT edges into (t_first, t_last, final_level, n_edges)."""
    events = []
    for t, level in bint_edges:
        if events and t - events[-1][1] < DEBOUNCE:
            first, _, _, n = events[-1]
            events[-1] = (first, t, level, n + 1)
        else:
            events.append((t, t, level, 1))
    out = []
    for first, last, level, n in events:
        if out and out[-1][2] == level:
            continue  # burst ended at the same level it started from
        out.append((first, last, level, n))
    return out


def summary(buses, end):
    for bus in buses:
        done = bus.done
        print(f'bus {bus.num}: {len(done)} transactions over {end:.3f} s')
        if not done:
            continue
        per_addr = Counter()
        kinds = defaultdict(Counter)
        for _, _, tok in done:
            addrs = [x for x in tok if '@' in x]
            key = addrs[0][2:4] if addrs else '??'
            per_addr[key] += 1
            kinds[key][' '.join(a[0] for a in addrs)] += 1
        for a, n in per_addr.most_common():
            k = ', '.join(f'{p}:{c}' for p, c in kinds[a].most_common())
            print(f'  0x{a}: {n:6d} txns ({n / end:7.1f}/s)  [{k}]')
        busy = sum(t1 - t0 for t0, t1, _ in done)
        gaps = sorted(b[0] - a[1] for a, b in zip(done, done[1:]))
        print(f'  bus busy {busy / end * 100:.1f}% of the time')
        if gaps:
            pct = lambda p: gaps[min(len(gaps) - 1, int(p * len(gaps)))] * 1e3
            print(f'  idle gaps ms: min {gaps[0] * 1e3:.2f}  p10 {pct(.1):.2f}  median {pct(.5):.2f}'
                  f'  p90 {pct(.9):.2f}  max {gaps[-1] * 1e3:.2f}')
            hist = Counter(min(int(g * 1e3), 50) for g in gaps)
            print('  gap histogram (ms bucket: count, 50 = >=50):')
            print('   ' + '  '.join(f'{k}:{hist[k]}' for k in sorted(hist)))


def vdp_changes(bus):
    """Track bus-0 writes per register (FP writes via 27/28 for 0x45); return (state, changes)."""
    state, changes, fp = {}, [], None
    for t0, _, tok in bus.done:
        if 'Sr' in tok or len(tok) < 4 or not tok[1].startswith('W@'):
            continue
        addr = tok[1][2:4]
        data = [x.rstrip('~') for x in tok[2:-1]]
        reg, val = data[0], ''.join(data[1:])
        if addr == '45' and reg == '27':
            fp = val[-3:]
            continue
        if addr == '45' and reg == '26':
            continue
        key = f'{addr} FP {fp}' if addr == '45' and reg == '28' else f'{addr} reg {reg}'
        if key in state and state[key] != val:
            changes.append((t0, key, state[key], val))
        state[key] = val
    return state, changes


def ee_writes(bus):
    """EEPROM byte writes on bus 1 (0x50.. = block 0, 0x51 = block 1): [(t, offset, value)] in order."""
    out = []
    for t0, _, tok in bus.done:
        if 'Sr' in tok or len(tok) < 4 or not tok[1].startswith('W@5'):
            continue
        base = (int(tok[1][2:4], 16) - 0x50) * 0x100
        data = [int(x.rstrip('~'), 16) for x in tok[2:-1]]
        out += [(t0, base + data[0] + i, b) for i, b in enumerate(data[1:])]
    return out


def load_dump(path):
    data = bytearray()
    with open(path) as f:
        for line in f:
            if ':' in line:
                data += bytes(int(x, 16) for x in line.split(':', 1)[1].split())
    return data


def main():
    args = sys.argv[1:]
    dump = args[args.index('--ee-diff') + 1] if '--ee-diff' in args else None
    paths = [a for a in args if not a.startswith('--') and not a.isdigit() and a != dump]
    if not paths:
        sys.exit(__doc__)
    path = paths[0]
    only = int(args[args.index('--bus') + 1]) if '--bus' in args else None
    z, chunks, rate, names = load(path)
    buses = [I2CBus(n, 1 << names[c], 1 << names[d])
             for n, (c, d) in BUSES.items()
             if c in names and d in names and only in (None, n)]
    bint = 1 << names['BINT'] if 'BINT' in names else 0

    bint_edges, prev, first = [], None, None
    for n, v in edges(z, chunks):
        t = n / rate
        if v is None:
            end = t
            break
        if prev is None:
            prev, first = v, v
            continue
        if bint and (v ^ prev) & bint:
            bint_edges.append((t, 1 if v & bint else 0))
        for bus in buses:
            bus.step(t, prev, v)
        prev = v

    if '--vdp' in args or '--state' in args:
        for bus in buses:
            if bus.num != 0:
                continue
            state, changes = vdp_changes(bus)
            if '--state' in args:
                for key in sorted(state):
                    print(f'{key:12s} {state[key]}')
                return
            print(f'bus 0: {len(state)} registers written, {len(changes)} value changes')
            for t, key, old, new in changes:
                print(f'{t:10.6f}  {key:12s} {old} -> {new}')
        return

    if dump:
        ref = load_dump(dump)
        writes = [w for bus in buses if bus.num == 1 for w in ee_writes(bus)]
        last = {}
        for t, off, b in writes:
            last[off] = (t, b)
        diff = [(t, off, b) for off, (t, b) in sorted(last.items()) if off >= len(ref) or ref[off] != b]
        print(f'bus 1: {len(writes)} EEPROM byte writes to {len(last)} offsets, {len(diff)} differ from {dump}')
        for t, off, b in diff:
            old = f'{ref[off]:02X}' if off < len(ref) else '--'
            print(f'{t:10.6f}  {off:03X}: {old} -> {b:02X}')
        return

    if '--summary' in args:
        summary(buses, end)
        for t0, t1, level, n in debounce(bint_edges):
            print(f'BINT {"released" if level else "asserted"} at {t0:.6f} s')
        return

    events = [(0.0, f'start of capture, levels: '
               + ' '.join(f'{k}={int(bool(first & (1 << i)))}' for k, i in sorted(names.items(), key=lambda x: x[1])))]
    for bus in buses:
        for t0, t1, tok in bus.done:
            events.append((t0, f'b{bus.num}  ' + ' '.join(tok) + f'   ({(t1 - t0) * 1e3:.2f} ms)'))
    for t0, t1, level, n in debounce(bint_edges):
        what = 'released (high)' if level else 'ASSERTED (low)'
        bounce = f'  [{n} edges over {(t1 - t0) * 1e3:.1f} ms]' if n > 1 else ''
        events.append((t0, f'BINT {what}{bounce}'))
    events.append((end, 'end of capture'))

    last_t = None
    for t, text in sorted(events):
        gap = f'+{(t - last_t) * 1e3:9.2f} ms' if last_t is not None else ' ' * 13
        print(f'{t:10.6f} {gap}  {text}')
        last_t = t


if __name__ == '__main__':
    main()
