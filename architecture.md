# Architecture

An Arduino Nano (classic, ATmega328P, 5 V) bridges a serial line protocol to a Sony PVM's service header.
The Nano reads and writes the EEPROM while BINT holds the MCU off the bus, and reaches the video processor
(VDP) in that mode. Releasing BINT makes the MCU reload the EEPROM and re-apply every setting (about a 2 s
blackout). Examples use the PVM-14N2U (header CN052, VDP3108 at 0x45); the findings behind them are in
`register_map.md`. Another model has its own addresses and items, in its own model file.

```
host/pvm-jig.py ── USB serial 115200 ── Nano ── service header (SDA, SCL, BINT, GND)
                                                  └ BINT low: header joined to the VDP bus (0x45)
```

The firmware is deliberately thin: BINT, EEPROM bytes and generic I²C reads/writes. Everything that knows about the monitor (16-bit words, FP handshake, item names, encodings) lives on the host, in `host/`.

## Table of contents

- [Files](#files)
- [Wiring](#wiring)
- [Build and upload](#build-and-upload)
- [Connect by hand](#connect-by-hand)
- [Protocol](#protocol)
- [Workflow](#workflow)
- [Cautions](#cautions)
- [Host tool (host/)](#host-tool-host)
  - [Install](#install)
  - [Layers](#layers)
  - [BINT: auto and manual mode](#bint-auto-and-manual-mode)
  - [One-shot commands vs shell](#one-shot-commands-vs-shell)
  - [Commands](#commands)
  - [Panel (host/pvm-jig-panel.py)](#panel-hostpvm-jig-panelpy)
  - [Model files (host/models/*.toml)](#model-files-hostmodelstoml)
- [Mapping a new monitor](#mapping-a-new-monitor)
  - [What the method relies on (PVM-14N2U findings, check them on a new model)](#what-the-method-relies-on-pvm-14n2u-findings-check-them-on-a-new-model)
  - [Setup](#setup)
  - [1. Discover (new model only)](#1-discover-new-model-only)
  - [2. Baseline (start of every session)](#2-baseline-start-of-every-session)
  - [3. One-item test (repeat per item)](#3-one-item-test-repeat-per-item)
  - [4. Decode](#4-decode)
  - [5. Record](#5-record)
  - [Rules](#rules)
  - [Pitfalls (seen on the 14N2U)](#pitfalls-seen-on-the-14n2u)
- [Automatic mapping (EEPROM → VDP, no front panel)](#automatic-mapping-eeprom--vdp-no-front-panel)
- [Checking the formulas (tools/calcheck.py)](#checking-the-formulas-toolscalcheckpy)
- [The colour matrix (worked example of a calculated value)](#the-colour-matrix-worked-example-of-a-calculated-value)
- [Finding which byte a value comes from (candidate search)](#finding-which-byte-a-value-comes-from-candidate-search)
- [Power-cycle test (bytes the MCU reads only at power-on)](#power-cycle-test-bytes-the-mcu-reads-only-at-power-on)

## Files
| File | Role |
|---|---|
| `firmware/pvm_jig/pvm_jig.ino` | Nano firmware: line protocol, I²C/EEPROM, BINT. |
| `firmware/readme.txt` | Plain-text firmware docs: wiring, build, protocol, VDP access, cautions. |
| `host/pvm-jig.py` | Host CLI: control and debug, one-shot commands or interactive shell. |
| `host/pvm-jig-panel.py` | Tk control panel (same style as pico-ttl2dvi's panel): one tab per model group, BINT session, Save. |
| `host/pvm-jig-web.py`, `host/web/` | Web panel (standard library server + plain HTML/CSS/JS), made for the MiSTer; see `mister.md`. |
| `host/pvm/` | Host library shared by the CLI and the panel (`link`, `device`, `model`, `calc`). |
| `host/models/<model>.toml` | Model file: every known item of one monitor (addresses, encodings, formulas, safe ranges, danger notes, help texts). |
| `register_map.md` | PVM-14N2U: service item ↔ EEPROM ↔ VDP register ↔ datasheet map, and how it was derived. |
| `dumps/<model>/` | Captures, EEPROM dumps and test reports behind the map (`dumps/pvm-14n2u/`). |
| `skill/pvm-mapping/SKILL.md` | Entry point for coding agents: points to "Mapping a new monitor" and the related sections below, plus the rules they must not skip. |
| `docs/` | Service manual and VDP datasheet used for the PVM-14N2U. |
| `tools/txn.py` | Decode Saleae / sigrok-cli text exports into transactions. |
| `tools/sr_i2c.py` | Decode sigrok `.sr` captures: I²C on both buses + BINT on one timeline (`--vdp`, `--state`). |
| `tools/validate.py` | Check an idle capture against an EEPROM dump: every VDP value the model file can predict, items and `[calc]` formulas (Python 3.11+). |
| `tools/automap.py` | Automatic mapping: change EEPROM bytes, let the MCU reload, diff the VDP state, restore. |
| `tools/calcheck.py` | Check the model's `[calc]` formulas against what the MCU computes: step each input, reload, read the VDP, compare, restore. |

## Wiring
The Nano runs at 5 V like the PVM bus, so it connects directly: no shifter, no transistor. PVM-14N2U header CN052:

| Nano | CN052 |
|---|---|
| A4 (SDA) | 3 SDA |
| A5 (SCL) | 2 SCL |
| D2 | 4 BINT |
| GND | 1 GND |

AGING (pin 5) stays unconnected.

- Only the classic Nano or the Nano Every are 5 V. 
- `Wire` enables the AVR internal pull-ups (~30–50 k to 5 V), in parallel with the PVM's 4.7 k. Harmless.
- BINT is open drain in software: asserted = `OUTPUT` with the latch at `LOW`, released = `INPUT`
  (the PVM's 10 k pull-up R016 brings it back to 5 V). The pin is never driven high.
- At reset and in the bootloader all pins are inputs, so a reset always releases BINT.
- Plug in the Nano's USB before powering the PVM, so the PVM's pull-ups don't back-feed an unpowered Nano.
- The USB ground ties the PVM ground to the computer. Check header GND ↔ BNC shell continuity first: the service
  manual warns that the chassis can be connected to the AC line.
- Logic analyzer stays on in parallel: D0 SCL, D1 SDA, D2 BINT (+ D3/D4 on bus 0).

## Build and upload
Arduino IDE, board "Arduino Nano". Most clones need Processor "ATmega328P (Old Bootloader)".
With `arduino-cli` (`brew install arduino-cli`):
```bash
arduino-cli core install arduino:avr
arduino-cli compile -b arduino:avr:nano:cpu=atmega328old firmware/pvm_jig
arduino-cli upload  -b arduino:avr:nano:cpu=atmega328old -p /dev/tty.usbserial-* firmware/pvm_jig
```
The bench Nano is a mini-USB CH340 clone (USB 1a86:7523) on `/dev/cu.usbserial-210`, old bootloader:
`cpu=atmega328old` confirmed. Use `cpu=atmega328` for boards with the new bootloader. `Wire.setWireTimeout` needs AVR core ≥ 1.8.3.
CH340 clones show up as `/dev/tty.usbserial-*` or `/dev/tty.wchusbserial*` (macOS), `/dev/ttyUSB*` (Linux);
boards with native USB (Nano Every) as `/dev/ttyACM*` on Linux.

## Connect by hand
The Nano doesn't echo, so use a terminal with local echo:
```bash
picocom -b 115200 --echo --omap crlf /dev/tty.usbserial-*
```
(`brew install picocom`, Ctrl-A Ctrl-X quits). The Arduino IDE serial monitor also works (115200).
`screen /dev/cu.usbserial-210 115200` works too, but you type blind (no local echo); Ctrl-A K quits.

Opening the port resets the Nano (DTR auto-reset). That releases BINT, so the MCU reloads the
EEPROM. Don't reconnect in the middle of a write session.

## Protocol
ASCII, one command per line, ended by `\r`, `\n` or both (the empty line from CRLF is ignored). Case-insensitive. All numbers are hex.
No echo, no prompt. Every command gets exactly one final line, `OK [data]` or `ERR <reason>`;
any longer output comes before it, so a host reads until it sees `OK` or `ERR`. Empty lines are
ignored. On boot the Nano prints `READY pvm 1` (name, protocol version); a host waits for it.

EEPROM offsets are `000`–`1FF`: `000–0FF` → I²C 0x50, `100–1FF` → 0x51.

| Command | Reply | Does |
|---|---|---|
| `b` | `OK 0\|1` | BINT state, 1 = asserted (low) |
| `b 1` | `OK 1` | Assert BINT: MCU off the bus |
| `b 0` | `OK 0` | Release BINT: MCU reloads the EEPROM and re-inits (picture blanks ~2 s) |
| `g` / `g 0` / `g 1` | `OK 0\|1` | BINT gate: 1 (default after reset) = `r`/`w`/`i`/`o` need `b 1`; 0 = allowed whatever BINT is (manual mode, collisions with the MCU possible) |
| `s [lo hi]` | `OK 50 51` | Scan 0x08–0x77 (or `lo`–`hi`, max 7F), list addresses that ACK. Works with BINT released too (address-only, no writes) |
| `r <off> [n]` | `OK 9D 0D` | Read `n` bytes (default 1). For `n` ≤ 16 the data is on the `OK` line |
| `r <off> <n>` (n > 16) | `<off>: <bytes>` … `OK <sum>` | One line per 16-byte-aligned block, then the 16-bit byte sum |
| `w <off> <b> [b…]` | `OK` | Single-byte writes (10 ms each, up to 32 bytes), then read back and verify |
| `i <dev> <reg> [n]` | `OK 0D 9D` | Generic register read: write `reg`, repeated START, read `n` bytes (1–16, default 1) from `dev` |
| `o <dev> <b> [b…]` | `OK` | Generic raw write: START, `dev`, the bytes, STOP (up to 32 bytes). **Any device, no verify, use with care** |

VDP3108 (0x45, reachable with BINT asserted) with `i`/`o`:
- Direct register read: `i 45 <reg> 2` (16-bit, high byte first) or `i 45 <reg>` (8-bit).
- FP RAM read: `i 45 29` (expect `00` = ready), `o 45 26 <addr hi> <addr lo>`, then `i 45 28 2`.
  E.g. V CENT: `o 45 26 00 80`, `i 45 28 2` → `OK 0D 9D`.
- FP RAM write: `i 45 29` (ready), `o 45 27 <addr hi> <addr lo>`, `o 45 28 <hi> <lo>`. Takes effect at once.
  E.g. V CENT +0x10: `o 45 27 00 80`, `o 45 28 0D AD` moves the picture down. `b 0` reloads the EEPROM value.
  FP addresses for the service items are in the model file and `register_map.md`.

`r`, `w`, `i` and `o` need `b 1` first, so the Nano never reads or writes while the MCU may be using the bus, unless the
gate is turned off with `g 0` (manual mode). `s` is always allowed: comparing a scan with BINT released vs asserted shows
whether BINT exposes other devices (e.g. the VDP at 0x45). With BINT released only bus 1 (EEPROM) is reachable.

Errors:
| Reply | Meaning |
|---|---|
| `ERR cmd` | Unknown command |
| `ERR args` | Bad or missing arguments, out of range, or line too long (111 chars max) |
| `ERR bint` | BINT not asserted |
| `ERR i2c <addr> <code>` | I²C failure: 2 = address NACK, 3 = data NACK, 4 = other / short read, 5 = timeout |
| `ERR verify <off> <got>` | Read-back after a write didn't match |

A multi-line read can end in `ERR` after some data lines; the data before it is valid.

## Workflow
With BINT asserted the header is joined to the VDP bus: `s 40 77` → `OK 45 50 51`. A device at 0x30 also
ACKs, but probing it left SCL held low, so use `s 40 77` rather than a full `s` while BINT is asserted.
EEPROM and VDP reads/writes work in this mode (that's how the host tool runs).

Back up first (the dump is `r 000 200`; run it twice and compare the sums):
```
b 1          → OK 1
r 000 200    → 000: ... (32 lines) → OK 1A2B
b 0          → OK 0
```
Change a value and apply it, e.g. V CENT (word at 0x4C, little-endian, default 0D9D):
```
b 1          → OK 1
r 04C 2      → OK 9D 0D
w 04C 9E     → OK
b 0          → OK 0      (picture blanks, comes back with the new value)
```

## Cautions
- **Byte writes only**, 10 ms each. The ST24C04 has 8-byte pages, but the firmware doesn't use them.
- **Don't touch 0xFF (= 0x55).** It's probably the "EEPROM valid" marker; clearing it might make the MCU load factory defaults.
- Write both bytes of a 16-bit word before releasing BINT. A half-written word gets loaded as is.
- There's no BINT timeout on purpose: if the host dies mid-write, BINT stays asserted until `b 0` or a Nano reset.
- Releasing BINT discards unsaved service-menu edits (the RAM copy is reloaded from the EEPROM).
- Untested: whether the MCU still writes user settings (volume, contrast → 0x10–0x1F) while BINT
  is low. Don't touch front-panel controls during a write session.
- Keep the dumps safe before any write. They're the only way back.

## Host tool (`host/`)

### Install
Python 3.11+ (for `tomllib`; older versions need the `tomli` package) and pyserial. One venv at the repo root serves the host tool and `tools/`:
```bash
python3 -m venv .venv
.venv/bin/pip install -r host/requirements.txt
.venv/bin/python host/pvm-jig.py -h
```

### Layers
```
pvm-jig.py            argparse CLI + interactive shell (thin)
pvm/model.py          Model (loads models/*.toml), Item, Session (pending changes, commit, discard)
pvm/calc.py           [calc] formulas: restricted expression parser, Table 3-3 matrices
pvm/device.py         Device: bint, scan, ee_read/ee_write, i2c, VDP reg_read/reg_write, fp_read/fp_write
pvm/link.py           Link: Nano line protocol over serial (READY, OK/ERR), find_port
models/<model>.toml   everything specific to one monitor model
```
- `Link` only knows the Nano protocol. A TCP link would replace only this file.
- `Device` knows the PVM hardware: BINT, the VDP address, the FP handshake (poll 29 until bits 0–2 are
  clear, then 26/27 address, 28 data). It doesn't know any item names.
- `Model` + `Session` turn "V CENT = 0DAD" into VDP writes and EEPROM bytes, using the model file.
- The API is synchronous and blocking. The CLI calls it directly; the UI should call the same objects from a
  worker thread, as the panel does:

  ```python
  from pvm.link import Link
  from pvm.device import Device
  from pvm.model import Model, Session
  model = Model("models/pvm-14n2u.toml")
  s = Session(Device(Link("/dev/cu.usbserial-210"), model.vdp_addr), model)
  item = model.find("V CENT")
  s.get(item)            # EEPROM value (or pending value)
  s.set(item, 0x0DAD)    # live VDP write (BINT asserted), remembered as pending
  s.save()               # write pending items to EEPROM, keep BINT
  s.commit()             # save() + release BINT -> MCU reloads
  data = s.dev.ee_read(0, 0x200)
  model.decode(item, data)   # item value from the full EEPROM image (one read for all items)
  ref = parse_dump("dump.hex")          # pvm.device: format_dump / parse_dump (Nano dump format)
  diff = s.dev.ee_diff(ref)             # [(offset, now, file)]
  s.dev.ee_restore(ref, diff)           # writes the differing bytes, returns offsets that failed to verify
  ```

### BINT: auto and manual mode
- **auto** (default): bus commands assert BINT themselves; `commit`/`discard` release it (MCU reloads).
- **manual** (`--manual` on one-shot commands, `bint manual` in the shell, `bint auto` to go back): the tool
  never touches BINT, and it turns the firmware's BINT gate off (`g 0`) so bus commands run whatever BINT's
  state. You drive it with `bint on` / `bint off`. `commit` then only writes the EEPROM ("release BINT to apply").
  Use it to read the EEPROM while the monitor runs normally (e.g. watching user settings change), or to keep
  full control during an experiment. With BINT released, only bus 1 (EEPROM) is reachable and accesses can
  collide with the MCU's (the AVR backs off on lost arbitration).
- Leaving the tool (end of a one-shot command, `quit` in the shell) always releases BINT if it's asserted, in
  both modes. A Nano reset also turns the gate back on.
- **Jig-mode check (auto mode):** `Device.bint(True)` asserts BINT and then polls `s 45 45` until the VDP answers
  from the header, which only happens once the MCU is in jig mode with the buses joined (normally ~17 ms). Only
  then do EEPROM/VDP commands run. If 0x45 doesn't answer within `JIG_TIMEOUT` (1 s), BINT is released and the
  command fails with "check the BINT wire": a bad BINT contact looks exactly like that (BINT low on the
  analyzer, MCU still running). In manual mode `bint on` doesn't check; use `scan 45 45` yourself.
- **Front panel while BINT is asserted:** in auto mode the shell keeps BINT asserted after the first bus command.
  The MCU is then off the bus, so a front-panel change (e.g. volume) apparently isn't saved to the EEPROM, and the
  next release reloads the old value (observed 10-22, not yet confirmed with a capture). To follow front-panel
  changes, `discard` first (or use manual mode with BINT released), then `get`.
- `get` / `list` show the EEPROM value; a value set in this session but not committed is shown as `pending`.
  Items without a live VDP target (volume, geometry, …) only take effect on `commit` (EEPROM + reload). The reload
  re-applies user settings too (volume confirmed): the value updates, then the screen blanks for a moment.
  Batch several changes into one `commit` to get a single blank.

### One-shot commands vs shell
- Opening the serial port resets the Nano, so every `pvm-jig.py ...` invocation starts with BINT released.
- **One-shot** (auto mode): commands that need the bus assert BINT themselves, and the tool releases it at the end
  (MCU reloads, ~2 s blackout). So one-shot `set` commits straight away, and live VDP writes (`fp write`,
  `reg write`, raw `o`) only last until that release.
- **`shell`:** one connection for the whole session. BINT stays as you leave it, `set` is live and kept as
  pending until `commit` (EEPROM + reload) or `discard` (reload only). Leaving the shell discards pending items
  and releases BINT.
- **Exit code:** 1 if any command failed (in the shell too, e.g. commands piped in), else 0. A piped shell
  carries on after an error, so scripts should prefer one `set` with several items.

### Commands
Items can be named by key (`v_cent`) or label (`"V CENT"`). Item values are shown as hex, decimal and, when the
model knows the encoding, the on-screen value: `4D (77) OSD 27`, `D8 (-40)`, `0CD0 (-816) OSD CD`.

Numbers are **hex by default**. Addresses, offsets and bytes (`ee`, `fp`, `reg`, `i2c`, `scan`, `raw`) are always
hex. Item values in `set` take an optional prefix with a colon (a plain `d` would be ambiguous: `d10` is valid hex):
| Form | Meaning | Example |
|---|---|---|
| `1F`, `0x1F` | raw value, hex | `set v_cent 0DAD` |
| `d:31` | raw value, decimal (negative for signed items) | `set pin_amp d:-90` |
| `osd:28` | on-screen value, converted with the item's `osd` rule (decimal, or hex for hex menus) | `set brightness osd:28`, `set h_size osd:CD` |
| `+N`, `-d:N`, `+osd:N` | relative to the current value | `set h_size +osd:1` (= +0x10) |
| Command | Does |
|---|---|
| `ports`, `info` | serial ports; model, port and BINT state |
| `bint [on\|off\|manual\|auto]` | show or set BINT, or switch mode (see above) |
| `scan [lo hi]` | scan I²C addresses (with BINT asserted: `scan 40 77` shows 45 50 51; avoid 0x30) |
| `raw LINE...` | send protocol lines to the Nano as they are, e.g. `raw "b 1" "i 45 29"` |
| `ee read OFF [N]`, `ee write OFF BYTE...` | EEPROM bytes; offset 0FF (valid marker) needs `--force` |
| `ee dump [FILE]`, `ee diff FILE`, `ee restore FILE` | full 512 bytes in the Nano's dump format (same as `dumps/pvm-14n2u/eeprom_2026-10-21.txt`); restore writes only the differing bytes and verifies |
| `fp read ADDR...`, `fp write ADDR VALUE` | VDP FP RAM with the handshake built in |
| `reg read REG`, `reg write REG VALUE` (`-s 1\|2`) | VDP direct registers (many are write-only) |
| `i2c read DEV REG [N]`, `i2c write DEV BYTE...` | any device on the bus |
| `list [GROUP] [-l]` | items with EEPROM values; `-l` also reads the VDP values |
| `get ITEM` | one item: EEPROM value, VDP target and live value, description |
| `set ITEM VALUE [ITEM VALUE ...] [-c]` | VALUE as in the table above. Live write + pending (shell), or write EEPROM + reload (one-shot or `-c`). With several items every value is parsed and range-checked before any is written, then one commit (one reload) |
| `pending`, `commit`, `discard` | session control (shell) |
| `shell` | interactive session, with history |

Example session:
```
$ .venv/bin/python host/pvm-jig.py shell
pvm-jig> ee dump dumps/eeprom_before.hex
pvm-jig> set v_cent +10          picture moves down at once
pvm-jig> set v_cent -8
pvm-jig> pending
pvm-jig> commit                  writes 0x4C, MCU reloads
```

### Panel (`host/pvm-jig-panel.py`)
```bash
.venv/bin/python host/pvm-jig-panel.py [-p /dev/cu.usbserial-210] [-m pvm-14n2u]
```
Needs tkinter. On macOS with Homebrew, Python 3.14 has it; for 3.13 run:

```
brew install python-tk@3.13
```
then create the venv with that Python.

- **Model** selector (top bar): every `host/models/*.toml`, shown by its `[model] name`; `-m` picks the initial
  one. Switching rebuilds the tabs (release BINT first).
- **Tabs** are generated from the model file's groups (User, Picture, Deflection, Geometry, …); another model
  file gives its own panel. Each row: label (red/orange "!" if risky), spinner, raw hex, Set, state, and the
  EEPROM location → VDP target. **Hover the name** for a tooltip (description, risk, safe range, location, live
  or on release); **right-click** (Ctrl-click) the name → "Copy description" copies it to the clipboard.
- **Dump EEPROM… / Restore EEPROM…** (with BINT on): dump writes the Nano's dump format
  (`dumps/eeprom_YYYYMMDD_HHMM.hex`, same as `ee dump`); restore reads a dump, shows the differing bytes,
  writes only those after confirmation, verifies them, and re-reads the tabs. Release BINT to apply.
- **Spinner units:** OSD value where the model has an `osd` rule (decimal, or hex for hex menus like H SIZE),
  decimal for signed items and decimal menus, raw hex otherwise. Typed values use the same units.
- **Auto-set spinners** (as in ttl2dvi): arrows send at once; Enter or **Set** always sends.
- **live calc** checkbox on calculated items whose calcs are all live (geometry now); its tooltip lists each calc
  the item feeds (targets, formula, note). Ticked
  (default) = our calculation writes the VDP at once; unticked = the change only goes to the EEPROM ("on release
  (CPU)") and the MCU calculates it when BINT is released. Useful to compare our maths with the MCU's. Turning it
  back on catches the VDP up with every pending change of that group. Kept per item until the model changes
  (`Session.no_live` in the library).
- **Session:**
  | Control | Does |
  |---|---|
  | **BINT** checkbox on | asserts BINT, waits for jig mode (VDP answers), reads the whole EEPROM, enables the spinners |
  | spinner change | live items write the VDP at once (no blank); EEPROM-only items are kept as pending ("on release") |
  | **Save to EEPROM** | writes the changed items; BINT stays on, keep tuning |
  | **BINT** off | releases BINT: the MCU reloads from the EEPROM (short blank) and saved changes become real; asks first if there are unsaved changes |
  | **Revert** | releases without saving (back to the EEPROM values) |
  | **Refresh** | re-reads the EEPROM |
- **Raw** row + console at the bottom: Nano protocol lines (`s 40 77`, `i 45 29`, …), with history (↑/↓).
- Library calls run on one worker thread; results and log lines come back through a queue that the Tk thread
  drains every 50 ms (Tk is never called from the worker).
- Calculated items whose calc is `live = false` (brightness, contrast, phase, sub-brightness, chroma) have no
  checkbox and stay EEPROM-only, like in the CLI; the location column names the calcs they feed.

### Model files (`host/models/*.toml`)
One file per monitor model, data only:
```toml
[model]
name = "Sony PVM-14N2U"
mcu = "CXP85220A-027S"   # MCU mask ROM = firmware; shown by `info`
firmware = "1.x"         # service-menu version the numbering and formulas belong to
vdp = 0x45               # VDP I2C address
protect = [0xFF]         # EEPROM offsets that need --force

[items.v_cent]           # key = name used on the command line
label = "V CENT"         # name in the service menu
menu = 12                # optional: service-menu number, shown as "V CENT (12)" in the panel and lists
group = "deflection"
eeprom = { at = 0x4C, size = 2 }      # little-endian; bits = [hi, lo] for packed items
vdp = { fp = 0x080 }                  # or { reg = 0x7E, size = 2, readable = true, bits = [hi, lo] }
when = "input=rgb"                    # optional: VDP target only valid in that context
refresh = false                       # optional: the MCU writes this VDP value only on change (validate.py skips it if absent)
signed = true                         # optional: show decimal as two's complement (true = item width, or bits: 12)
osd = { offset = 50 }                 # optional: on-screen value = (raw >> shift) - offset, hex = true for hex menus;
                                      # { offset = 0 } when the raw value is the on-screen value (enables osd:)
min = 51                              # optional: safe range in the item's decimal units (signed for signed items)
max = 115
danger = { level = "high", text = "..." }   # optional: high | medium | low, shown before risky writes
help = "..."                          # what the item does: tooltip in the panel, shown by `get`
```
- **Raw** items: `eeprom` + `vdp`, the value goes over unchanged.
- **Packed** items: `bits` on both sides. Items sharing a register are written together (the other fields come
  from their EEPROM or pending values), so writing CPKV keeps CCOR.
- **Context** items (`when = "input=rgb"`, `"field=60"`, …): live only when the context matches. `[context]` in
  the model file says how to find it (input: EEPROM 0x1E; line rate: FP 0EB lines per field below/above 287);
  the session reads it once per BINT session (`Session.context()`, reset on commit/discard). E.g. on the RGB
  input the RGB white drives and RGB CLAMP are live, the VIDEO ones are EEPROM-only.
- **Latch:** `vdp = { fp = 0x0F0, latch = "b_drive_rgb" }` rewrites that item after this one. The VDP only applies
  new white drive values after WDB (blue, FP 0F2) is written, so R and G latch through B.
- **Calculated** values (`[calc.<name>]`): VDP values the MCU computes from several EEPROM items. The formulas
  are data in the model file, evaluated by `host/pvm/calc.py` (a small restricted parser, no `eval`); the
  library (live writes) and `tools/validate.py` (checks against captures) use the same code, so a formula lives
  in one place only.
  ```toml
  [calc.abr]
  fp = 0x0FA                            # target: fp = addr | reg = sub (size, readable); a list for kind = "matrix"
  expr = "rgb_sbrt + 4 * (brightness - 50)"   # item keys = their EEPROM values (signed items as two's complement)
  live = false                          # optional: not written live yet (the MCU applies it on release)

  [calc.act]
  fp = 0x0FB
  let.lo = "round(rgb_cont * 81 / 256)" # helper values, evaluated in order before expr
  expr = "lo + (rgb_cont - lo) * contrast // 100"

  [calc.vertical]                       # datasheet Table 3-3: target[i] = sum(weights[field][i][j] * terms[j])
  kind = "matrix"
  fp = [0x08B, 0x08C, 0x08D, 0x08E]
  bits = 12                             # result width (masked), default 16
  round = "each"                        # round every product (halves away from zero) before summing, like the MCU
  terms = ["v_po + 1", "v_size / 128", "v_lin_down / 128", "v_line_up / 128"]
  weights."60" = [[...], ...]           # one table per line rate (context "field")
  mirror = { "0x08B" = 0x08F }          # also write that FP (DSCV = A0)
  save_a0 = 0x6E                        # SAVE also writes the computed A0 here, like the MCU

  [calc.matrix]                         # formula not known: `uses` lists the items, no expr
  reg = [0x74, 0x6C, 0x64, 0x5C]
  uses = ["contrast", "chroma", "chroma_center"]
  live = false
  ```
  - The items a calc depends on are read from its formulas (plus `uses`); an item feeds every calc that names it.
    Items carry no `calc` key.
  - An item is live only if every calc it feeds is live, has a formula and (for a matrix) has weights for the
    active line rate. Otherwise `set` only changes the EEPROM and the MCU computes it on release. Calcs with a
    `when` that doesn't match the context are skipped.
  - On a change the session recomputes the calc, writes only the targets that changed (highest first, like the
    MCU) and the `mirror`. The first change per BINT session calibrates: it reads the current targets from the VDP
    (FP always, reg only if `readable`) and keeps the difference to the formula (normally 0) as an offset;
    **if any difference is larger than ±4 it refuses to write** (formula doesn't match this set or mode).
  - Live now: geometry. Formulas present but `live = false` until tested live: IBR, ABR, ACT, CTM, TINT. Not
    known yet: the colour matrix (contrast, chroma).
- **Other firmware:** a model file can start with `base = "pvm-14n2u.toml"` (path relative to the file) and list
  only what differs: `[model]` fields, new items, or single fields of an item or calc (merged field by field,
  e.g. `[calc.act] expr = "..."` replaces only the formula). Each file appears in the panel's model selector,
  so choosing a model chooses a firmware.
- To add a model: copy the file, change addresses and items, and map it as in "Mapping a new monitor" below.
  Select it with `-m <name>` or `-m path/to/file.toml`.

**Descriptions and safety notes** are in the model file too: `help`, `danger`, `min`/`max` on each item, and
`[danger_regs]` for raw writes. One file holds everything the tool uses; there is no separate help file and no
`desc` (what the data already says, such as the encoding, target or formula, isn't repeated in prose).
Calcs have their own `note` (about the formula), shown on the "live calc" checkbox and by `get`.
```toml
[danger_regs]                         # raw writes to these need --force (CLI) / a confirmation (panel)
"reg 15" = "Horizontal drive control ..."
"fp 08C" = "Vertical sawtooth A1 ..."
```
- `get ITEM` shows help, the calcs it feeds (formula, note), danger and safe range; `set` prints the danger notice and refuses values outside
  the safe range unless `-f`/`--force`; `reg write`, `fp write`, `i2c write` and `raw` refuse danger registers
  unless `--force`.
- The panel colours risky items (red = high, orange = medium, "!"), shows everything in the name's tooltip, and
  asks before writing a value outside the safe range or a raw line to a danger register.

## Mapping a new monitor
The goal is a map: **menu item → EEPROM location and encoding → VDP register(s) → datasheet name**, proven by
captures, written into the model file. This was done for the PVM-14N2U (`register_map.md`, which also walks
through one capture in "How to read a capture"); the same method works for other models.

### What the method relies on (PVM-14N2U findings, check them on a new model)
- Two I²C buses: **bus 1** = service header + EEPROM (0x50/0x51), **bus 0** = MCU ↔ VDP (0x45).
- **BINT** (header pin, active low) puts the MCU in jig mode: it stops driving the VDP and joins both buses, so the
  header reaches the VDP. Releasing BINT makes the MCU reload everything from the EEPROM (~2 s blank).
- The MCU keeps settings in RAM, sends them to the VDP at once, rewrites **every** VDP value every ~1.3 s, saves
  user settings to the EEPROM by itself (~185 ms later) and service settings only on SAVE.
- VDP values arrive in three ways: **raw** (EEPROM value = register value), **packed** (two items share a register
  as bit fields), **calculated** (e.g. geometry coefficients → accumulator values). Expect all three.

### Setup
- Logic analyzer (sigrok, fx2lafw) on both buses and BINT, GND on the header:
  `D0=SCL1 (bus 1), D1=SDA1, D2=BINT, D3=SCL0 (bus 0), D4=SDA0`. Use one tool consistently: after sigrok, the
  Saleae app can't see the clone until it is power-cycled with the probes unplugged (or use
  [fx2-reset](https://github.com/charlysan/fx2-reset)).
- The Nano on the header, the host tool run from the repo root with `.venv/bin/python`, captures and dumps in
  `dumps/<model>/`.
- Capture command (10 s for tests; 5 s is enough for idle captures):
  ```bash
  sigrok-cli -d fx2lafw --config samplerate=4m --time 10000 \
    --channels D0=SCL1,D1=SDA1,D2=BINT,D3=SCL0,D4=SDA0 -o dumps/<model>/<name>.sr
  ```

### 1. Discover (new model only)
1. Scan from the header with BINT released, then asserted (`pvm-jig.py shell` → `bint on`, `scan 40 77`):
   an EEPROM at 0x50–0x57 and, with BINT low, the video processor (0x45 on the VDP3108). Avoid probing unknown
   jig-mode addresses (0x30 on the 14N2U held SCL low).
   **If only the EEPROM answers** (e.g. analog PVMs with no VDP): there is nothing to write live, but settings may
   still be changed through the EEPROM: write it with BINT low, then release BINT so the MCU reloads and applies
   it. This is a hypothesis, untested on such a set: first check that a release reloads the EEPROM (bus 1 reads
   after the release) and that a harmless one-byte change shows up on screen. Map items from SAVE captures and
   dump diffs (bus 1) instead of VDP writes.
2. Capture bus 0 idle and decode: `python3 tools/sr_i2c.py dumps/<model>/<idle>.sr --summary` (addresses,
   refresh period).
3. Get the video processor's datasheet; find its register tables and any indirect RAM interface (VDP3108: FP RAM
   via 26 read address / 27 write address / 28 data / 29 status).

### 2. Baseline (start of every session)
1. **Access check with BINT low**: proves the wiring, jig mode and VDP access before anything else.
   ```
   $ .venv/bin/python host/pvm-jig.py shell
   pvm-jig> bint on          (auto mode waits until the VDP answers; "check the BINT wire" = bad contact)
   pvm-jig> scan 40 77       expect: 45 50 51  (VDP through the joined buses + EEPROM)
   pvm-jig> fp read 80       optional: V CENT from the VDP, must equal the EEPROM word at 0x4C
   pvm-jig> bint off         MCU reloads (~2 s blank)
   pvm-jig> quit
   ```
   With BINT released the same scan shows only `50 51`. If 0x45 never answers, fix the BINT contact (or find the
   right jig line on a new model) before going on.
2. **Full EEPROM dump**, without a blank:
   `.venv/bin/python host/pvm-jig.py --manual ee dump dumps/<model>/eeprom_<date>_<time>.hex`
3. **Idle capture**, 5 s, nothing touched, **BINT released** (`pvm-jig.py --manual info` must say `released`; a
   shell still holding BINT makes bus 0 silent).
4. **Validate**: `python3 tools/validate.py dumps/<model>/<baseline>.sr dumps/<model>/<dump>.hex`. Every value
   the model predicts must match (14N2U: 60/60 on RGB, 62/62 on composite). A BAD line means the map or the
   model file is wrong; fix that first.
5. Diff the dump against the previous one: every changed byte needs an explanation (or the last restore failed).

### 3. One-item test (repeat per item)
1. Open the item in the menu, **note the current value**.
2. Start the capture, **wait ~2 s** (the 1.3 s refresh then records every "before" value).
3. Change the item **one step** (**4 steps** if the predicted register change per step is below ~2, e.g.
   high-order geometry terms), wait ~2 s (derived values like DSCV follow up to 1.3 s later).
4. **SAVE** (PVM-14N2U: MENU twice). User settings need no SAVE: one step up, wait, one step down.
5. Stop, then **set the item back and SAVE again**, so the next capture starts from the original values. Note any
   value deliberately kept changed.
6. Name the file `dumps/<model>/<menu no>_<item>_<from>_<to>[_save].sr` (e.g. `50_h_size_cc_cd_save.sr`); user
   settings have no number (`brightness_27_28_27.sr`). One item per capture, always.
7. Note the input (RGB/LINE) and line rate (50/60 Hz) if they changed: they select different EEPROM blocks and
   formulas.

When the EEPROM location is already known or suspected, the item can also be mapped without the front panel:
see "Automatic mapping" below.

### 4. Decode
1. **VDP side**, what changed:
   ```bash
   python3 tools/sr_i2c.py dumps/<model>/<file>.sr --vdp | grep -v "reg 25"
   ```
   `reg 25` toggling 0E↔0F is the MCU's periodic tube measurement. FP writes are already resolved
   (`45 FP 080 0D9D -> 0DAD`). If nothing changed, the step happened before the first refresh: use `--state` and
   diff it against an earlier capture in which only this item differed.
2. **Storage side**, which EEPROM bytes the SAVE (or a user-setting save) changed, against the dump taken before
   the test:
   ```bash
   python3 tools/sr_i2c.py dumps/<model>/<file>.sr --ee-diff dumps/<model>/<dump>.hex
   ```
   It expands the page writes into bytes and prints only the offsets that differ (`060: 39 -> 3A`). Only the tested
   item may differ, plus derived values the MCU also stores (14N2U: the computed A0 at 0x6E and CTM at 0x30).
   Raw page writes: `--bus 1 | grep W@50` (`S W@50 20 55 00 D0 …` = start offset 0x20, then one byte per address).
3. **Encoding**: compare step sizes. OSD +1 and byte +1 → raw byte; byte +0x10 → probably a word × 16 (read with
   the next byte, little-endian); same delta on the VDP → raw; different or several registers → packed or
   calculated. Check signed values (0xEF = −17).
4. **Datasheet**: look up every changed register; the datasheet name usually matches the menu item. For calculated
   items, the set of registers that move identifies the parameter. VDP3108 geometry: coefficient `a` → A0 only,
   `b` → A0–A1, `c` → A0–A2, `d` → A0–A3, `e` → A0–A4 (Table 3–3; the MCU uses integer weights and rounds each
   product, halves away from zero); vertical A0 is mirrored into DSCV (FP 08F).
5. **Predict before confirming**: write down the expected deltas first; a match is much stronger evidence than a
   value coincidence. Small values (0000, 0001, 0007, 0014) match many registers by chance: trust changes, not
   equal values.

**Worked example (PVM-14N2U, runnable from the repo root).** Service item 49 L/C DELAY, 00 → 01 and SAVE, on
composite video:
```bash
python3 tools/sr_i2c.py dumps/pvm-14n2u/49_video_lcdelay_00_01_save.sr --vdp | grep -v "reg 25"
python3 tools/sr_i2c.py dumps/pvm-14n2u/49_video_lcdelay_00_01_save.sr \
  --ee-diff dumps/pvm-14n2u/eeprom_20261024_ntsc.hex
```
```
bus 0: 104 registers written, 16 value changes
  2.994469  45 reg 31    00 -> 01
bus 1: 124 EEPROM byte writes to 124 offsets, 2 differ from dumps/pvm-14n2u/eeprom_20261024_ntsc.hex
  6.634004  030: 14 -> 27
  6.913924  0D0: 00 -> 01
```
- `reg 31 00 -> 01` at 2.99 s is the menu step reaching the VDP (reg 31 = LDF, luma/chroma delay).
- `0D0: 00 -> 01` at 6.91 s is where SAVE stores it: same value, same step, so a raw item, EEPROM 0xD0 → reg 31.
- `030: 14 -> 27` is not the test: it's the CTM the MCU also stores on SAVE (contrast had changed since the dump).

A word item, service item 34 TINT (OFF → 00 → 01), same commands with
`dumps/pvm-14n2u/34_video_tint_off_00_01_save.sr`: the VDP shows `FP 01C 0FFF -> 0000 -> 0001`, the EEPROM
`07E: FF -> 01` and `07F: FF -> F0`, i.e. the word F001 at 0x7E: a 12-bit value in bits 11:0 (FFF = OFF = −1).

### 5. Record
1. **Register map** (`register_map.md` for the PVM-14N2U): the item's row (EEPROM location and encoding, current
   value, VDP target, datasheet name) with evidence (capture file, deltas) and a mark: ✔ tested, ~ value match
   only, ? unknown. Update every other table that mentions the bytes.
2. **Model file** `host/models/<model>.toml`: add or fix the item (see "Model files" above). Calculated values go
   in a `[calc.<name>]` table with `live = false` until tested live; a calc with an unknown formula gets
   `uses = [...]` and no `expr`. Give the item a `help` text (the manual's ITEM column and the datasheet's
   function text) and, if a wrong value can stress the tube or the deflection, a `danger` level and a `min`/`max`
   around the factory value.
3. **Check**: re-run `tools/validate.py` on the baseline (it reads everything from the model file); for a formula,
   also `tools/calcheck.py <calc>`.
4. Mark guesses as guesses; don't write up conclusions that rest on assumptions as facts.

### Rules
- One item per capture; restore the value after every test, so each capture starts from known values.
- Back up the EEPROM before any write; at the end of a session the EEPROM diff against the backup must be 0
  (apart from values deliberately kept).
- Unknown EEPROM bytes are written last, one at a time, by a small step, and only after an explicit decision:
  nobody knows what they drive.
- Never write the MODEL byte, the "valid" marker (0xFF) or danger registers directly.

### Pitfalls (seen on the 14N2U)
- **No bus-0 traffic in a capture** → BINT was held (a `pvm-jig.py shell` in auto mode keeps it asserted).
- **BINT low on the analyzer but the MCU keeps running** (bus 0 still busy, no reload) → bad BINT contact. The
  CLI's jig-mode check (`s 45 45` must answer) reports "check the BINT wire".
- **Values changed between captures** that you didn't touch → a restore was forgotten, or a user setting changed;
  the dump diff tells.
- **awk on a non-English-locale Mac** parses `1.4268` as 1; filter decoder output with Python, not awk.
- **Per-context blocks**: input (VIDEO 0x90 / RGB 0xA0), line rate (60 Hz 0xC0 / 50 Hz 0xB0) and colour standard
  (L/C DELAY 0xD0 NTSC / 0xD4 / 0xD8 SECAM) select different EEPROM blocks; the MCU reads them on switching.
- **A reload that shows no change** isn't always a wrong mapping: some bytes are read only at power-on, or only in
  their own context (14N2U: the 60 Hz block at power-on; TINT and the VIDEO drives only on the video input). Use
  the power-cycle test, test in the right context, and use the candidate search to find which byte a value comes
  from (sections below).
- **Live writes vs commit**: a direct VDP write lasts until the next BINT release; only EEPROM + reload persists.
- **One setting, several registers**: user contrast drives ACT, CTM and the four colour-matrix coefficients; a
  menu step that moves "too much" is usually a calculated item, not noise.
- **Register widths**: some values are 9 bits; RGB CONT 0x200 made ACT 0 (bit 9 dropped).
- **zsh loops**: `$spec` isn't word-split; use `${=spec}` when passing "11=64 52=2D,01" to automap.py.
- **Bus 0 pauses ~65 ms during user-setting saves**: the MCU's single I²C unit is busy writing the EEPROM.

## Automatic mapping (EEPROM → VDP, no front panel)
The menu-driven method (`register_map.md`, "How to read a capture") needs someone at the monitor. The reverse
method runs from the computer alone, with the Nano and the logic analyzer connected:

1. **Baseline:** full EEPROM dump, then a short idle capture with BINT released → `--state` = every value the
   MCU sends to the VDP.
2. **Change one EEPROM byte** (BINT asserted, jig mode checked), usually by +1.
3. **Release BINT:** the MCU reloads the EEPROM and re-sends everything to the VDP (~2 s blank).
4. **Capture 2–3 s** and diff its `--state` against the baseline: the VDP values that moved are driven by that
   byte (raw, packed or calculated: the size of the change tells which). FP values can also be read directly
   with the Nano (`fp read`); direct registers are write-only, hence the capture.
5. **Restore the byte** (and reload) before the next test.

Tool: `tools/automap.py` (project venv; Nano and analyzer free):
```bash
.venv/bin/python tools/automap.py baseline phaseA                 # dump + idle capture -> dumps/automap/
.venv/bin/python tools/automap.py test phaseA 11 32               # byte 0x11 = 0x32, reload, capture, diff, restore
.venv/bin/python tools/automap.py test phaseA 11=64 52=2D,01      # several writes in one test
.venv/bin/python tools/automap.py fp phaseA 11 32 -- 0FB          # read FP values with the Nano instead of capturing
.venv/bin/python tools/automap.py restore backup_before_automap   # write back everything that differs
```
In zsh loops use `${=spec}` to split a spec string into words.

Why it works: the reload applies service *and* user settings (volume confirmed 10-22), so the EEPROM alone
determines what the MCU sends. It can't see settings the MCU applies with its own pins (volume, input select);
those show no VDP change.

**Phases** used for the PVM-14N2U (lowest risk first; stop after each phase to commit and look at the monitor):
| Phase | Content |
|---|---|
| A | contrast formula (contrast byte at several values → ACT), RGB CONT's role |
| B | storage confirmations of items mapped only by value (PKUN/PKOV, CLPGI/GP/MD, SLCLVL, IF1/IF2, VIDEO S-BRT) |
| C | VIDEO-input items: input byte 0x1E → LINE A + reload, test, then back to RGB |
| D | unknown EEPROM words, in small groups, only with explicit OK |

**Safety rules:**
- Dump before starting; at the end restore and verify the EEPROM diff is 0.
- One byte at a time, small changes (±1), restored right after each test.
- Never write 0x00 (MODEL?), 0xFF (valid marker) or danger registers directly.
- After every reload, check that reg 15 (H drive) and the vertical accumulators (FP 08B–08E) haven't changed
  (`! WATCHED` in the output); if a byte turns out to drive something dangerous, restore it immediately and mark
  it (`danger`) in the model file.
- Each test blanks the screen ~2 s (reload): know how many tests a phase has before starting it.
- For formulas, sweep several values (contrast 0…100 gave ACT = lo + (max − lo) × c // 100 exactly) and try an
  integer search when a float fit is off by 1: the MCU uses fixed-point maths (e.g. × 81 / 256).

## Checking the formulas (`tools/calcheck.py`)
`validate.py` checks each formula at one point (today's EEPROM). `calcheck.py` sweeps the inputs and lets the MCU
compute: for each input item of a calc, one at a time (the others unchanged), it writes the EEPROM byte, releases
BINT (the MCU reloads and recomputes, short blank), asserts BINT, reads the calc's targets and compares them with
the formula from the model file (exact; ±1 only for a matrix that rounds the sum instead of each term). Nano only; `--capture` adds the analyzer
for write-only registers (CTM, the colour matrix) and stops the run if reg 15 (H drive) changes.
```
.venv/bin/python tools/calcheck.py vertical --item v_size        # one calc, one input
.venv/bin/python tools/calcheck.py ibr abr                       # every input of both calcs
.venv/bin/python tools/calcheck.py act --item rgb_cont --points rgb_cont=1FF,200
.venv/bin/python tools/calcheck.py ctm --capture                 # reg 61 is write-only
.venv/bin/python tools/calcheck.py --all                         # later, once the single runs pass
```
- **Points:** +1, −1, +2, −2 menu steps from the current value (H SIZE in OSD steps = 0x10 raw), plus the safe
  min/max for non-geometry calcs. `--points KEY=V,...` replaces them (hex, `d:`, `osd:`, `+N`); explicit points may
  be outside the safe range and are flagged in the plan.
- **Geometry safety:** only small steps around the current (centred) picture, alternating +/− so it moves back and
  forth; a point is skipped if it would move any accumulator by more than 40, or leaves the safe range.
- **Plan first:** every point is listed (with skips and the number of reloads) and needs `y` (or `--yes`).
- **Only the item's byte is written** (not 0x6E): the test changes a setting like the menu does and compares what
  the MCU computes. If A0 alone stays put while A1.. move, the report notes that the MCU may use the stored A0.
- **Restore:** after each item its byte is written back and the targets must read back exactly as before (else it
  stops). On exit, error or Ctrl-C, every byte that differs from the backup is restored and the EEPROM diff is
  reported; then BINT is released.
- **Context:** only the current input and line rate; calcs with a non-matching `when`, without weights for the
  line rate, or with only write-only targets (without `--capture`) are skipped with a message.
- **Output:** the report and an EEPROM backup in `dumps/calcheck/<date>_<time>_*`.
- Someone near the monitor can switch it off if it does something unexpected. Note that the changed byte is
  still in the EEPROM and will be loaded again at the next power-on, so restore it (`ee restore <dump>`)
  before or right after powering back on.

## The colour matrix (worked example of a calculated value)
The VDP decodes video into luma (Y) and two colour-difference signals (Cb = B−Y, Cr = R−Y), and its display stage
turns them back into RGB with a matrix (VDP3108 datasheet p26):
```
R − Y = MR1M·Cb + MR2M·Cr        G − Y = MG1M·Cb + MG2M·Cr        B − Y = MB1M·Cb + MB2M·Cr
```
For standard decoding four terms are non-zero; on the PVM-14N2U they are reg 74 MR2M, reg 6C MG1M, reg 64 MG2M and
reg 5C MB1M, 9-bit signed /128, with datasheet defaults 86 / −22 / −44 / 113 for CTM = 32. Scaling all four
together is saturation. Contrast (CTM) scales luma, so the matrix must scale with it or colour would wash out as
contrast rises. The MCU therefore derives one common gain from user chroma, CTM and service CHROMA, and applies it
to the four bases.

How it was found from captures:
1. **Which registers move.** `sr_i2c.py --vdp` on a contrast step listed ACT, CTM and four more registers; the
   datasheet table named them as the matrix. User chroma and service CHROMA moved the same four (not CTM): one set
   of registers fed by three settings is a calculated value.
2. **Decode.** Read as 9-bit two's complement (`01E6` = −26), the four values kept the datasheet's proportions
   86 : −22 : −44 : 113 within rounding: one common gain, not four independent values.
3. **Grid** (`dumps/pvm-14n2u/matrix/grid_60_rgb.json`). The registers are write-only, so every point is EEPROM
   write, BINT release and a capture of what the MCU sends. Contrast × chroma (5 × 5) plus service CHROMA values
   showed: chroma 0 gives 0 everywhere; service CHROMA scales all four (0x80 gives no colour); above a limit the
   matrix sticks at 192 / −48 / −96 / 253, a fixed cap used when MB1M would exceed 253.
4. **Don't trust scattered fits.** A blind search over many integer formulas "fitted" each coefficient with a
   different, unrelated form: with thousands of candidates and few points, chance matches. The real arithmetic
   explains all four coefficients with one structure.
5. **Fine sweep** (`fine_60_rgb.json`, steps of 1). Where the values step reveals the structure: the matrix changes
   only where CTM changes (so the MCU uses CTM, not the contrast byte), and at fixed CTM every chroma point fits
   `coef = trunc(M × chroma / 100)` exactly, M being the value at chroma 100. Each point bounds the possible M;
   if the bounds of all points don't overlap, the formula is wrong (the same constraint method that found the
   geometry rounding).

Still open: **M(CTM, service CHROMA)**. It is nearly proportional to CTM × service CHROMA but with a small offset,
so there is one more rounding step inside it. The 61 measured points in `dumps/pvm-14n2u/matrix/` are enough to
solve it offline; then contrast and chroma can be written live like geometry. Details and values are in
`register_map.md`, colour-matrix TODO.

## Finding which byte a value comes from (candidate search)
When a capture shows a VDP value that the model doesn't explain (e.g. `validate.py` reports BAD for an item that
is right in another context), look for the EEPROM byte the MCU used, one candidate at a time. Worked example
(10-24): at 50 Hz, reg 31 (L/C DELAY) = 03 while the mapped byte 0xD0 = 00.

1. **Shortlist candidates** from a dump: unmapped bytes near the mapped one whose value equals what the VDP shows.
   Here 0xD0–0xDF = `00 0F 03 0F 03 0F 03 0F 17 1F 03 0F 00 11 03 0F`, so the 03 entries D2, D4, D6, DA, DE.
2. **Ask before writing**: unmapped bytes are only changed with an explicit OK, one at a time, by a small step,
   on something that can't stress the tube or the deflection (a luma/chroma delay is harmless).
3. **Baseline in the same context** (input, line rate) as the miss:
   `.venv/bin/python tools/automap.py baseline lcd50` (EEPROM dump + idle capture). Check the context in the
   state (FP 0E7 = 0270 is 50 Hz) and that the value to explain is there (reg 31 = 03).
4. **Test each candidate alone**: `.venv/bin/python tools/automap.py test lcd50 D4=04`. It writes the byte, reloads,
   captures, prints every VDP value that differs from the baseline (filter `reg 25`), then writes the old value
   back and reloads. The right byte moves the value by the step and nothing else; the others show no change.
   Test all candidates, not just until the first hit, so a second source would show up too.
5. **Verify the restore**: `.venv/bin/python tools/automap.py restore lcd50` must report `0 byte(s) differed`
   (it writes back anything that does differ).
6. **Record it**: add or split the model item (here `lc_delay` with `when = "field=60"` at 0xD0, `lc_delay_50`
   with `when = "field=50"` at 0xD4), re-run `validate.py` on the baselines of both contexts, and note the test,
   the result and what is still unknown in the model's register map.

## Power-cycle test (bytes the MCU reads only at power-on)
Some EEPROM bytes are read by the MCU only at power-on, not on a BINT reload: changing them and releasing BINT
shows no change on the VDP (`automap test` reports `0 change(s)`), which looks like a wrong mapping but isn't.
This test tells the two apart. Worked example (10-24, 60 Hz): PMST at 0xC0 → reg 7B.

1. **Back up:** `.venv/bin/python host/pvm-jig.py ee dump dumps/automap/before_powercycle_<byte>.hex`.
2. **Write the byte** by a small, harmless step: `.venv/bin/python host/pvm-jig.py ee write C0 15`
   (the CLI asserts BINT, writes, verifies and releases).
3. **Capture with BINT released** (analyzer on bus 0, 4 s is enough since the MCU rewrites everything every 1.3 s):
   ```
   sigrok-cli -d fx2lafw --config samplerate=4m --time 4000 \
     --channels D0=SCL1,D1=SDA1,D2=BINT,D3=SCL0,D4=SDA0 -o dumps/powercycle_<byte>_1_before.sr
   ```
   The register should still show the old value (reg 7B = 0014): the reload didn't read the byte.
4. **Power-cycle the monitor** (off, a few seconds, on, wait for the picture), then capture again
   (`_2_after.sr`). Compare both states with `validate.vdp_state`: if the register now shows the new value and
   nothing else changed (reg 7B = 0015), the byte is mapped right and is **boot-only**.
5. **Restore:** `ee write C0 14`, check `ee diff dumps/automap/before_powercycle_<byte>.hex` = 0 bytes,
   power-cycle again and capture (`_3_restored.sr`): the whole VDP state must equal the first capture (reg 25
   excluded).

Notes:
- Each byte costs two power cycles, so test one representative byte per block rather than every byte.
- The Nano can stay connected and powered (10-24: no trouble through two cycles). Its `Wire.begin()` enables the
  AVR's internal pull-ups, so a small current reaches the unpowered bus; if a power-on ever looks odd, repeat
  with the Nano's USB unplugged.
- Never leave a test value in the EEPROM: the next power-on would load it.
- Effect on the host tool: for boot-only bytes, Save + BINT release changes the EEPROM but not the picture until
  the next power-on; live writes during the session still work.
