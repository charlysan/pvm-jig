#!/usr/bin/env python3
"""Group a Logic I2C analyzer export (tab-separated) into transactions.

Usage: python3 tools/txn.py <logic .txt export or sigrok-cli output> [--words]

Each line: start time, then per address phase "W 0x50: <data bytes>".
NAKed bytes are marked with '~'. --words also prints EEPROM writes
(first data byte = offset) as 16-bit little-endian words.
"""
import csv
import sys


def parse_sigrok(lines):
    """sigrok-cli i2c annotations ("i2c-1: Data write: 1D"). No timestamps: t = index."""
    tx, cur = [], None
    for line in lines:
        ann = line.split(':', 1)[1].strip() if ':' in line else ''
        if ann in ('Start', 'Start repeat'):
            if cur:
                tx.append(cur)
            cur = {'t': len(tx), 'ops': []}
        elif ann.startswith('Address '):
            rw, addr = ann.split(': ')
            cur['ops'].append({'rw': 'R' if 'read' in rw else 'W',
                               'addr': '0x' + addr, 'ack': True, 'data': []})
        elif ann.startswith('Data '):
            cur['ops'][-1]['data'].append((int(ann.split(': ')[1], 16), True))
        elif ann == 'Stop':
            tx.append(cur)
            cur = None
    if cur:
        tx.append(cur)
    return tx


def parse(path):
    lines = open(path).read().splitlines()
    if lines and lines[0].startswith('i2c-'):
        return parse_sigrok(lines)
    rows = list(csv.reader(lines, delimiter='\t'))[1:]
    tx, cur = [], None
    for r in rows:
        typ, t = r[1], float(r[2])
        if typ == 'start':
            if cur:
                tx.append(cur)  # repeated start without stop
            cur = {'t': t, 'ops': []}
        elif typ == 'address':
            cur['ops'].append({'rw': 'R' if r[6] == 'true' else 'W',
                               'addr': r[5], 'ack': r[4] == 'true', 'data': []})
        elif typ == 'data':
            cur['ops'][-1]['data'].append((int(r[7], 16), r[4] == 'true'))
        elif typ == 'stop':
            tx.append(cur)
            cur = None
    if cur:
        tx.append(cur)
    return tx


def fmt_op(o):
    data = ' '.join(f"{b:02X}{'' if ack else '~'}" for b, ack in o['data'])
    return f"{o['rw']} {o['addr']}{'' if o['ack'] else ' NAK'}: {data}"


def words(o):
    b = [x for x, _ in o['data']]
    off, payload = b[0], b[1:]
    return ' '.join(f"{off + i:02X}={payload[i] | payload[i + 1] << 8:04X}"
                    for i in range(0, len(payload) - 1, 2))


def main():
    args = sys.argv[1:]
    show_words = '--words' in args
    path = [a for a in args if not a.startswith('--')][0]
    for x in parse(path):
        print(f"{x['t']:10.4f} " + ' | '.join(fmt_op(o) for o in x['ops']))
        if show_words:
            for o in x['ops']:
                if o['rw'] == 'W' and len(o['data']) >= 3:
                    print(f"{'':11}{words(o)}")


if __name__ == '__main__':
    main()
