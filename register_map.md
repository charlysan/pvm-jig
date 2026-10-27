# PVM-14N2U register map

Service menu item ↔ EEPROM word ↔ VDP3108 register ↔ datasheet name, and how each link was found.

Sources:
- **Manual** `docs/pvm14n2u.pdf` p13–14: "Initial Setting of Service Data ROM" (items 00–65, 14" defaults).
- **Datasheet** `docs/VDP3108.pdf` (Micronas, Advance Information 1994/95): p25–31 Table 3–1 (direct
  registers), p31–33 Table 3–2 (FP registers).
- **EEPROM** `dumps/pvm-14n2u/eeprom_2026-10-21.txt` (full dump from the Nano) and `dumps/pvm-14n2u/save.txt` (SAVE capture, 10-20).
- **Bus 0** `dumps/pvm-14n2u/bus0_idle.sr` (10 s, 10-20 23:04): one full MCU refresh cycle (1.287 s), so every value the
  MCU writes to the VDP appears at least once.
- **Live tests** from the Nano on CN052 with BINT asserted (see `architecture.md`).

## How the correlation was done

1. **EEPROM layout from SAVE.** SAVE always writes the same fixed list of ranges (`dumps/pvm-14n2u/save.txt`), so the
   service data lives at 0x20–0x7F, 0x90–0xAF, 0xC0–0xCF, 0xD0–0xF6. Two SAVEs differing only in GAIN changed
   only byte 0x60, and 0x60 held 0x39 = the GAIN shown on screen. With the neighbouring values this showed the
   data is **16-bit little-endian words**.
2. **Menu order.** Reading the words from 0x44 upward gives 0190, 0014, 0017, 02DC, 0D9D, 0100, 0100, 010D…,
   which is the manual's default column for items 07 WDRV, 09 VBSO, 10 AVST, 11 AGCREF, 12 V CENT, 13, 14, 15…
   in order (08 EWDM is skipped: it's not stored). So 0x42–0x74 is items 06–31, one word each.
3. **Bus 0 values.** The MCU refreshes the VDP from its RAM copy round-robin, every 1.287 s. Every write in
   `bus0_idle.sr` was decoded into direct register writes (`W@45 <reg> <hi> <lo>`) and FP writes
   (`27 ← addr`, `28 ← data`). Then every EEPROM word was searched for among those values. A unique match
   (e.g. 0D9D appears only at EEPROM 0x4C and only in FP 080) links the item to the VDP register.
4. **Datasheet names.** Each matched register was looked up in the datasheet. Where the datasheet function
   matches the manual's item description (e.g. EEPROM 0x4C "V center" → FP h'80 OFS "DC offset of the vertical
   sawtooth"), the link is confirmed. The datasheet also explains packed items, e.g. reg 72 holds CPKV in
   bits 4:0 and CCOR in bits 8:5, and 0x0063 decodes to exactly the defaults CPKV = 3, CCOR = 3.
5. **Protocol.** The FP access pattern (poll `29`, `27 ← addr`, `28 ← data`; reads via `26`) was inferred from
   the bus, then confirmed by datasheet p25 (FPRD/FPWR/FPDAT/FPSTA).
6. **Live test.** From the Nano with BINT asserted: `o 45 26 00 80`, `i 45 28 2` → `0D 9D` (reading V CENT
   back), then `o 45 27 00 80`, `o 45 28 0D AD` → the picture moved down. That proves item → FP address → effect.

Weak spots: values like 0000, 0001, 0006, 0007, 0014 appear in many registers, so a value match alone can be a
coincidence. Those rows are marked `~` until one item is changed and the bus traffic is diffed.

## VDP access recap
- I²C address **0x45**. Subaddress, then 1 or 2 data bytes (16-bit = high byte first). Read = write subaddress,
  repeated START, read.
- **Direct registers**: written in one transaction. Nano: `o 45 7E 00 6A` / `i 45 1C`.
- **FP registers** (RAM of the VDP's internal "Fast Processor"): through the FP interface.
  | Sub | Name | Use |
  |---|---|---|
  | 26 | FPRD | write FP address to read |
  | 27 | FPWR | write FP address to write |
  | 28 | FPDAT | 16-bit data, read or write |
  | 29 | FPSTA | bit0 write request, bit1 read request, bit2 busy (00 = idle) |

  Write: `i 45 29` (00), `o 45 27 <addr>`, `o 45 28 <value>`. Read: `o 45 26 <addr>`, `i 45 28 2`.
- Datasheet register mode `v` = latched at vertical sync.

## Data flow: RAM is the master copy
The MCU keeps every setting in its internal RAM and computes the VDP values from there. The EEPROM is only
persistent storage, read at power-on and on BINT release.
```
button → MCU RAM ──→ VDP (next field, ~17 ms; raw, packed or calculated, see below)
                 └─→ EEPROM: user settings automatically ~185 ms later; service items only on SAVE
power-on / BINT release: EEPROM → MCU RAM → VDP (full reload)
```
Measured with brightness (`dumps/pvm-14n2u/brightness_27_28_27.sr`): VDP IBR/ABR written at 3.174 / 3.190 s, EEPROM 0x12
at 3.358 s (+185 ms); the step back: 6.250 / 6.267 s, EEPROM 6.435 s (+185 ms).

- The menus run in the MCU, not over I²C. I²C is only how the MCU drives the VDP and stores copies in the EEPROM.
  So **a setting can be made live over I²C only if the MCU applies it through the VDP**. Settings the MCU applies
  with its own pins can't: volume (pin 60, O-VOL, a DC level probably from a PWM output), input select
  (O-LINE/RGB, O-LINE-A/B pins). Confirmed with `dumps/pvm-14n2u/volume_16_17_18.sr` (10-23): two volume steps change
  nothing on bus 0; bus 0 only pauses 65 ms while the MCU writes the two EEPROM pages (one shared I²C unit).
  Writing the EEPROM ourselves doesn't change the volume (the EEPROM is passive; the MCU only reads it at
  power-on and on BINT release).
- A direct VDP write from the Nano (BINT low) changes only the picture: MCU RAM and EEPROM keep the old value,
  and the next reload (BINT release, power-on) restores it.
- An EEPROM write + BINT release goes through the normal path (EEPROM → RAM → VDP).
- With BINT high, the MCU's 1.3 s round-robin refresh rewrites every VDP value from RAM.

## Dangerous registers and settings (review, 2026-10-23)
What a wrong value can do to the monitor or the tube, from the VDP3108 datasheet and general CRT behaviour.
The service menu itself doesn't stop extreme values, and the MCU applies whatever is in the EEPROM at every
power-on, so a dangerous value **stored in the EEPROM** is worse than a live VDP write (which the next BINT
release or power cycle undoes). Keep an EEPROM dump before any write (`pvm --manual ee dump`).

Levels: **HIGH** = can destroy hardware within seconds; **MEDIUM** = tube wear / burn or component overload if
left or extreme; **LOW** = bad picture only, fixed by a reload; **DATA** = EEPROM content.

| Level | Register / item | What it controls | What can go wrong | Reachable from |
|---|---|---|---|---|
| **HIGH** | **reg 15**, horizontal drive control (MCU writes the constant 0x4768; datasheet p26–27) | bits 5:0 = **H drive pulse duration** (40 µs); bit 6 = H PLL loops enable (1); bit 13 = **vertical protection** (0 = enabled); bit 7 HOUT gating; bits 9–12 blanking/clamping/CSIO | wrong pulse width or a free-running H frequency (bit 6 = 0) overstresses the **horizontal output transistor and flyback** (classic way to kill a CRT set); bit 13 = 1 turns off the RGB blanking that protects the tube when the vertical deflection fails | raw writes only (`reg write 15`, `o 45 15 …`); not in the EEPROM or the menu |
| **HIGH** | vertical deflection: **V SIZE** (0x24), V PO / V LIN / V LINE UP (0x20, 0x25, 0x26), FP 08B–08E (A0–A3), FP 08F DSCV, FP 080 OFS | vertical sawtooth amplitude and shape | a collapsed or nearly collapsed vertical (e.g. V SIZE or A1 near 0) concentrates the beam into a **thin horizontal line that burns the phosphor** within seconds to minutes. The VDP's vertical protection watches for the flyback pulse; whether it catches a collapsed-but-running sawtooth is unverified | service menu, EEPROM, live FP writes |
| MEDIUM | beam current: **R/G/B DRIVE** (FP 0F0–0F2, EEPROM 0x90/0xA0 blocks), cutoff (reg 66/6A/6E), brightness IBR/ABR (FP 0F9/0FA), contrast ACT (FP 0FB) and reg 61, **DRIVE LIMIT** (FP 0FC), **BCL** (FP 0D2–0D5: BCLTHR, BCLTM, BCLG, BCLMIN), soft limiter (reg 41/45/49/4D) | how much beam current the tube draws, and the limiters that cap it | excessive beam current: **phosphor burn, cathode wear, overload of the high-voltage supply / flyback**. The BCL is the protection: BCLG bit 11 must stay 1 (datasheet), and raising BCLTHR or zeroing BCLG removes the limit | service menu, user menu (brightness/contrast), EEPROM, live writes |
| MEDIUM | horizontal PLL: IF1/IF2 (FP 04B), PLL2/3 filter coefficients (reg 57/5B/5F), line lock XLG (FP 026), DVCO (FP 058) | horizontal synchronisation and frequency | unstable or wrong horizontal frequency: picture tearing, extra **stress on the horizontal output stage** | service menu (IF1/IF2), raw |
| MEDIUM | E-W parabola: H SIZE, PIN PHASE/AMP, H SEXY/COR PIN (0x22–0x2A), FP 09B–09F | horizontal width modulation along the field | extreme modulation can overstress the E-W / horizontal output stage, if this chassis drives an E-W stage (not verified on the PVM-14N2U) | service menu, EEPROM, live FP writes |
| LOW–MED | measurement / cutoff loop: reg 25 (PMC), reg 13 (WDRV, EWDM), PMST/PMSO/TML (7B/77/7F) | the tube measurement the MCU uses for automatic cutoff | the cutoff loop works on wrong data: black level drifts (too high = more beam current) until the next reload | service menu, raw |
| LOW–MED | vertical standard: FP 01B SDT, FP 0E7 VSDT, reg 34 | 50/60 Hz setup of the decoder and vertical timing | vertical deflection run with an unexpected setup; normally just a rolling/wrong picture | system select (safe), raw |
| LOW | blanking and timing: VBST/VBSO (6F/73), H blanking (17/16), AVST (6B), HPOS (7E), POFS2/3 (67/63), V CENT (FP 080), INTLC (FP 073), decoder (AGC, clamp, killer, peaking, notch, standard) | picture position, blanking, decoding | bad picture, visible retrace lines; a reload fixes it | service menu, raw |
| **DATA** | EEPROM 0xFF (= 0x55, "valid" marker), 0x00 (MODEL?), whole service area | what the MCU loads at power-on | a cleared marker may make the MCU load factory defaults or misbehave at boot; any dangerous value above stored here is re-applied at **every power-on**, before the tool can fix it | `ee write` / `restore` (0xFF needs `--force`), SAVE |
| ? | jig-mode slave **0x30** | unknown | probing it once held SCL low (bus hang, fixed by a power cycle) | raw |

**Not reachable over I²C:** G2 (screen) and focus are potentiometers on the flyback; the high voltage itself is
set by the hardware.

**What protects you today:** live VDP writes don't persist (BINT release or power cycle restores the EEPROM
values), 0xFF needs `--force`, and the jig-mode check stops writes when BINT isn't really asserted. Nothing yet
stops an extreme value for the items above, and raw commands can write anything.

**Danger notices (implemented 10-23):** the model file `host/models/pvm-14n2u.toml` holds, per item, a `danger` level + text
and a safe `min`/`max` (in the item's decimal units), plus `[danger_regs]` for raw writes (reg 15, FP 08B/08C/08F,
FP 0D3). The CLI prints the notice on `set` and refuses values outside the safe range or writes to danger
registers without `--force`; the panel marks risky rows (red = high, orange = medium, with "!"), and asks before
writing such values or raw lines. All values in the 10-23 EEPROM are inside their safe ranges. The limits are
the factory values with a margin (e.g. V SIZE 51–115 around the default 0x53), not measured tube limits.

## Validation baseline (2026-10-23)
`dumps/pvm-14n2u/eeprom_20261023_1905.hex` (full EEPROM, read with `pvm --manual ee dump`) + `dumps/pvm-14n2u/baseline_2026-10-23.sr`
(5 s idle capture, BINT released, RGB input, 60 Hz). Every VDP value predicted from the dump matches the capture:
**54 of 54 checks** — 30 raw/packed items from the model file, RGB white drive + RGB CLAMP (input 0x1E = 02 →
0xA0 block), the 60 Hz block, IBR/ABR = S-BRT + 4 × brightness, ACT = 84 + 2 × contrast, all nine geometry
accumulators via Table 3–3 (7 exact, 2 within ±1; all exact with the 10-24 integer model), DSCV = A0 and A0 = saved word 0x6E.
10-24: `tools/validate.py` now takes every check from the model file (items whose `when` matches, and every
`[calc]` with a formula): **60 of 60**, now including SYNC FB (RGB), CTM and TINT; H CENT is video-only and skipped
on RGB.

**50 Hz baseline (10-24):** `dumps/pvm-14n2u/eeprom_20261024_50hz.hex` + `dumps/pvm-14n2u/baseline_2026-10-24_50hz.sr` (8 s idle, RGB input,
50 Hz source): **59 of 60**. The 50 Hz block (0xB0–0xB8: PMST, PMSO, TML, H BLK, VBST), the 50 Hz geometry (exact)
and all other calcs match. The one miss is **L/C DELAY**: reg 31 = 03 while 0xD0 = 00. 0xD0–0xDF reads
`00 0F 03 0F 03 0F 03 0F 17 1F 03 0F 00 11 03 0F`: eight pairs whose first bytes look like L/C delays (03 and 17
are the manual's NTSC/PAL and SECAM defaults).
**Found (10-24, `tools/automap.py`, baseline `lcd50`):** each 03 entry was set to 04 alone, with a reload, whole-state
diff and restore: **0xD4 → reg 31 03 → 04, nothing else**; D2, D6, DA, DE: no change. EEPROM diff 0 afterwards.
So on RGB, L/C DELAY comes from **0xD0 at 60 Hz** and **0xD4 at 50 Hz** (model items `lc_delay` / `lc_delay_50`;
both baselines now 60/60). Probably one entry per colour standard (D0 NTSC, D4 PAL?); D2, D6, D8 (17 = SECAM?),
DA, DC, DE and the second byte of each pair are still unknown (other standards, or video-input only).

**Colour-standard items on NTSC composite (10-24, front panel, saved and restored):**
- (49) L/C DELAY 00 → 01 (`dumps/pvm-14n2u/49_video_lcdelay_00_01_save.sr`): reg 31 00 → 01; SAVE wrote only `D0 ← 01` of the
  0xD0 block. So NTSC composite (trap) and RGB 60 Hz share 0xD0.
- (34) TINT OFF → 00 → 01 (`dumps/pvm-14n2u/34_video_tint_off_00_01_save.sr`): FP 01C 0FFF → 0000 → 0001; SAVE wrote
  `7E ← 01 F0` (bits 11:0, upper nibble F kept). **The menu shows FFF as "OFF".**
- Both in trap mode: the comb copies (TINT A8, comb L/C DELAY) weren't written; where TINT comb is stored is
  still unknown (0x80–0x83 are a guess).

**Colour select (user menu: AUTO, NTSC COMB, NTSC, NTSC443, PAL, SECAM), 10-24, NTSC composite:**
`dumps/pvm-14n2u/color_select_auto_comb.sr`, `dumps/pvm-14n2u/color_select_auto_secam.sr`; set back to AUTO afterwards (0x15 = 00).
- **Stored at EEPROM 0x15**, saved by the MCU ~200 ms after the change: AUTO 00, NTSC COMB 01, SECAM 05
  (02 NTSC, 03 NTSC443, 04 PAL by menu order, not seen). Model item `color_select`.
- AUTO on this NTSC source = plain NTSC (reg 34 = 02, trap). **NTSC COMB: only reg 34 02 → 03** (VDP comb filter
  mode); TINT stayed 0FFF (0x7E) and L/C DELAY 00 (0xD0). So the comb mode has no separate TINT or L/C DELAY
  copy here; the manual's comb TINT "A8" is probably just the factory value for a set used in comb mode (to
  confirm: service item 34 in NTSC COMB should still show the 0x7E value).
- **SECAM** (on the NTSC signal): reg 34 → 00, **reg 31 → 17 = 0xD8** (the manual's SECAM L/C DELAY), reg 30
  39 → 99 (notch), reg 22 48 → 0B, and the MCU switches to 625/50 timing (VSDT 0270, 50 Hz block, 50 Hz
  geometry tables) regardless of the signal.
- L/C DELAY table so far: 0xD0 = NTSC (trap and comb), 0xD4 = used at 50 Hz on RGB (PAL?), 0xD8 = SECAM (17).

**NTSC composite baseline (10-24):** `dumps/pvm-14n2u/eeprom_20261024_ntsc.hex` + `dumps/pvm-14n2u/baseline_2026-10-24_ntsc.sr` (8 s idle,
LINE A = 0x1E 00, NTSC 60 Hz composite): **62 of 62**, the first check of the video-input items on a live signal:
H CENT (reg 7E), SYNC FB VIDEO (reg 63), R/G/B DRIVE VIDEO (FP 0F0–0F2), RGB CLAMP VIDEO (reg 67), L/C DELAY from
0xD0 (reg 31 = 00), COMB (FP 043), IBR, CTM, TINT. Compared with RGB: FP 01B SDT = 0001 (NTSC), reg 32 01 → 00,
reg 33 08 → 09 (input selector), the drive set and reg 63 switch to the VIDEO copies. TINT on composite = 0FFF
(the 0x7E value): the manual's comb-mode base (A8) didn't appear. One matrix point (CTM 39, chroma 50: 5C = 121)
equals the RGB fine sweep; six more points confirmed it (see the colour-matrix TODO).

**50 Hz block storage (10-24, `automap` baseline `lcd50`):** PMST, TML and VBST hold the same values in both
blocks, so the baseline couldn't tell which block the MCU read. Stepping each 50 Hz byte by +1 (0xB0, 0xB4, 0xB8)
moved only its register by +1 (reg 7B, 7F, 6F); EEPROM diff 0 afterwards. The 60 Hz copies of these three
(0xC0, 0xC4, 0xC8) can't be tested that way: **the 60 Hz block isn't re-read on a BINT reload.**

**Boot-only bytes: power-cycle test (10-24, 60 Hz, RGB):** `automap test blk60 C0=15` gave no change. Then, by hand:
C0 = 15 written and BINT released → reg 7B still 0014 (`dumps/pvm-14n2u/powercycle_c0_1_before.sr`); monitor off/on →
**reg 7B = 0015, the only change** (`_2_after.sr`); C0 = 14 written back (EEPROM diff 0 against
`dumps/pvm-14n2u/automap/before_powercycle_c0.hex`), off/on → reg 7B = 0014, whole VDP state identical to the first capture
(`_3_restored.sr`). So **0xC0 is the 60 Hz PMST, and the MCU reads it only at power-on**; on a BINT reload it keeps
its RAM copy. At 50 Hz the 0xB0 block *is* applied on reload (fetched on demand, see the standard switch above).
This matches the power-on capture, which reads `F0, C0, C7, 90, 9C, D0, 7E`, none of which the reload reads.
Probably boot-only too (not tested): the rest of the 60 Hz block (0xC2–0xC9), L/C DELAY 60 Hz (0xD0), service
TINT (0x7E), C/O REF (0xF0), maybe 0x90/0x9C. For these, Save + BINT release in the host tool changes the EEPROM
but the picture only after a power cycle (live writes during the session still work). The Nano stayed connected
and powered through both power cycles without trouble.

**To check later (formulas):**
- **CHROMA / colour matrix** (reg 74/6C/64/5C): scaled by contrast × chroma with a cap (MB1M ≤ 253); the integer
  form is known only to ±1. Until it's exact, contrast, chroma and service CHROMA stay EEPROM-only
  (`[calc.matrix]` has no formula).
  **Grid data (10-24, 60 Hz, RGB): `dumps/pvm-14n2u/matrix/grid_60_rgb.json`** (30 points: contrast {0,25,50,75,100} ×
  chroma {0,25,50,75,100} at service CHROMA 0x70, plus service CHROMA 60/68/78/80 at contrast 50 / chroma 50, and
  the current setting; each with the full VDP state; EEPROM restored, diff 0). Findings:
  - coefficient ≈ base × service CHROMA × chroma × CTM / ~203 000 with the datasheet bases 86 / −22 / −44 / 113
    (74 / 6C / 64 / 5C): within ±1 at every uncapped point; chroma 0 gives 0 everywhere.
  - **cap**: when MB1M would exceed 253 the whole matrix is held at 192 / −48 / −96 / 253 (same proportions), so
    the MCU caps the common gain, not each coefficient.
  - **service CHROMA (0x2B) scales all four coefficients**, not just B−Y; 0x80 gives all zeros (signed byte,
    negative → 0?).
  - No single product with one rounding fits, and no two-stage integer form fits all four coefficients
    (searched 10-24); the per-coefficient "fits" were scattered, i.e. chance.
  **Fine sweep (10-24): `dumps/pvm-14n2u/matrix/fine_60_rgb.json`** (chroma 40–60 step 1 at contrast 50; contrast 40–60 at
  chroma 50; EEPROM restored, diff 0). Solved so far, exact at all 61 uncapped points of both files:
  - **contrast enters only through CTM** (reg 61): the matrix changes exactly where CTM changes, nowhere else.
  - **chroma is the last step:** `coef = trunc(M × chroma / 100)` (toward zero), M = the value at chroma 100.
    At CTM 41, service CHROMA 0x70: M = 194 / −48 / −100 / 256 (74 / 6C / 64 / 5C).
  - **cap:** if 5C would exceed 253, the MCU writes the fixed set 192 / −48 / −96 / 253 (identical at every CTM).
  - Still open: **M(CTM, service CHROMA)**. Roughly M ≈ base × service CHROMA × CTM / 18 000 minus a small offset
    (5C: 124 at CTM 20, 186 at 30, 256 at 41, 324–325 at 52, 394–395 at 63, all at 0x70), not proportional, so
    one more rounding step sits inside M. The data is enough to solve it offline (no analyzer needed).
  - **Same on video:** `dumps/pvm-14n2u/matrix/video_60_ntsc.json` (10-24, NTSC composite on LINE A: contrast 25/75 × chroma
    25/50/75) is identical, register for register, to the RGB grid; one formula serves both inputs.
- **RGB CONT is 9 bits** (0x200 gave ACT 0): find how the MCU masks it (`bits = [8, 0]` on the item?) and check
  the ACT formula at 0x1FF / 0x200.

**TODO (host tool):** boot-only bytes (see "Boot-only bytes: power-cycle test"). Mark those items in the model
(e.g. `boot_only = true`):
- confirmed: PMST 60 Hz (43, 0xC0, `pmst_60`);
- probable, same 60 Hz block: PMSO (44, 0xC2, `pmso_60`), TML (45, 0xC4, `tml_60`), H BLK START (47, 0xC6,
  `hbst_60`), H BLK STOP (46, 0xC7, `hbso_60`), VBST (48, 0xC8, `vbst_60`);
- probable: L/C DELAY 60 Hz (49, 0xD0, `lc_delay`);
  B/G/R C/O REF (60–62, 0xF0–0xF2, `b_co_ref`, `g_co_ref`, `r_co_ref`);
- not boot-only: TINT (34, 0x7E): on RGB a reload ignored it, but on the composite input `calcheck tint` passed
  10/10 (10-24): the MCU reads it on reload when the input is video.
- not boot-only: R/G/B DRIVE VIDEO (35–37, 0x90–0x94): on the composite input, 0x90 +1 → FP 0F0 0254 → 0255 after a
  plain BINT reload (10-24, `automap fp ntsc 90=55`; restored, diff 0). They're simply not read while the input is
  RGB. 0x9C (RGB CLAMP VIDEO) is inside the reload's 0x98–0x9F read, so not boot-only either. (FP 0F2 read back as
  09B6 while the MCU writes 01B6: bit 11 set on read, meaning unknown.)

With the mark, the panel would say "applies after power-off/on" instead of "on release", and calcheck would skip
these items with a message.
Until then, after saving one of these, power-cycle the monitor to see the stored value.

Compared with the 10-21 dump, only these bytes changed: 0x10 volume, 0x12 brightness, 0x13 chroma,
0x1F previous input, 0x22 H SIZE (kept), 0x24 V SIZE (kept), 0x6E saved A0. All tested service values were restored.

Note: capture with BINT released. A first attempt recorded nothing because a shell session still held BINT
(MCU in jig mode = silent bus 0).

## How values reach the VDP: three cases
Comparing EEPROM values with what the MCU writes on bus 0, every item so far falls into one of three cases.
This decides how a host tool can set it live.

**1. Raw (1:1).** The stored value is written unchanged to one register. Most service items.
| Item | EEPROM | VDP |
|---|---|---|
| V CENT | 0x4C = 0D9D | FP 080 ← 0D9D |
| H CENT | 0x42 = 006A | reg 7E ← 00 6A |
| AGCREF / GAIN / SGAIN | 0x4A / 0x60 / 0x62 | FP 0B2 / 020 / 0BE |
| DRIVE LIMIT, BCL, EHT, KILVL/KILHY, CLP*, SLCLVL | 0x54–0x74 | FP 0FC, 0D2–0D5, 07A/07B, 0A8/0A9, 029/02C/02D, 0CC |
| R/G/B DRIVE | 0x90 (VIDEO) / 0xA0 (RGB) | FP 0F0–0F2 (write WDB last) |
| PMST / PMSO / TML / VBST / H BLK | 0xB0 (50 Hz) / 0xC0 (60 Hz) blocks | reg 7B / 77 / 7F / 6F / 17 / 16 |

Live control: write the EEPROM value straight to the register.

**2. Packed.** Two items share one register as bit fields. Values are still raw.
| Items | EEPROM | VDP |
|---|---|---|
| CPKV + CCOR | 0x32 = 0063 | reg 72 (bits 4:0 + 8:5) |
| CPKU + CFS | 0x34 = 0103 | reg 7A (bits 4:0 + bit 8) |
| PKUN + PKOV | 0x36 = 0007 | reg 69 (bits 3:0 + 7:4) |
| WDRV + EWDM | 0x44 = 0190 (EWDM not stored) | reg 13 (bits 9:0 + bit 11) |
| IF1 + IF2 | 0x76 = 017E | FP 04B (bits 4:0 + 8:5) |

Live control: read-modify-write the bit field, keep the other item's bits.

**3. Calculated.** The stored value is a parameter; the MCU computes the register values from it, so the
menu value never appears on the bus. So far: all geometry items (H SIZE, V SIZE, pin, linearity). They are
polynomial coefficients that the MCU converts into accumulator values with the datasheet's Table 3–3, and
one coefficient can change several registers (see "Geometry"). Example: H SIZE CC → CD is stored as
0x0CC0 → 0x0CD0 but sent as FP 09B 09EC → 09FC.

Live control: the host must do the same calculation (or step the result: H SIZE = ±0x10 on FP 09B per step).

**User brightness is also case 3** (`dumps/pvm-14n2u/brightness_27_28_27.sr`, 10-22, RGB input):
- EEPROM 0x12 = OSD + 50 (27 → 0x4D, 28 → 0x4E). The OSD is decimal, centred on 50 like chroma.
- One step changes **two** FP registers by 4: FP 0F9 IBR (internal brightness, picture) and FP 0FA ABR
  (analog brightness for external RGB), both 0x16C → 0x170 and back.
- IBR = ABR = **256 + 4 × OSD** (27 → 364 = 0x16C ✓). Cross-check: on 10-20 IBR was 012C = 300 → OSD 11 →
  raw 61 = 0x3D, and the 10-20 capture shows 0x12 = 3D ✓.
- The 256 is the sub-brightness: VIDEO S-BRT (0x4E) and RGB S-BRT (0x50) were both 0x100 = 256, the
  datasheet's centre. **ABR = RGB S-BRT + 4 × OSD is confirmed** (10-23, `dumps/pvm-14n2u/14_rgbsbrt_100_101_save.sr`:
  RGB S-BRT 100 → 101 moved only FP 0FA, 011C → 011D, with brightness OSD 7: 0x101 + 28 = 0x11D). And **IBR = VIDEO
  S-BRT + 4 × OSD is confirmed** too (`dumps/pvm-14n2u/13_videosbrt_100_101_save.sr`: VIDEO S-BRT 100 → 101 moved only
  FP 0F9, 011C → 011D, also on the RGB input).

**User contrast** (`dumps/pvm-14n2u/contrast_01_02_01.sr`, 10-22, RGB input):
- EEPROM 0x11 = OSD, no offset (01 → 02 → 01). Written ~185 ms after the VDP, same flow as brightness.
- One step changes only **FP 0FB ACT** (analog contrast for external RGB, 0…511, default 350) by +2:
  0x56 → 0x58 → 0x56. Reg 61 CTM (main picture contrast) stayed 0014: on the RGB input only the RGB path moves.
- Formula not settled: ACT = 84 + 2 × OSD fits, and so would a product with RGB CONT (0x52 = 0x10D). Read ACT at
  a few contrast values (`o 45 26 00 FB`, `i 45 28 2` with BINT low) to decide. On LINE A/B, contrast probably
  drives reg 61.

**Contrast and RGB CONT, solved by automatic mapping (Phase A, 10-23, `tools/automap.py`):** the contrast byte
(0x11, 0–100) and RGB CONT (0x52) were written with BINT asserted, the MCU reloaded, and the VDP values read
back / captured; every test restored its byte (final EEPROM diff against the backup: 0).
- **ACT (FP 0FB)**, measured at 17 combinations (contrast 0…100, RGB CONT 237/269/301), all exact:
  ```
  lo  = round(RGB_CONT × 81 / 256)            (≈ 31.6 % of RGB CONT)
  ACT = lo + (RGB_CONT − lo) × contrast // 100
  ```
  So contrast 100 = RGB CONT exactly ("RGB maximum contrast"), contrast 0 ≈ 0.316 × RGB CONT.
- **RGB CONT is 9 bits**: 0x200 gave ACT = 0 (the MCU drops bit 9). Keep it ≤ 0x1FF.
- **Contrast also drives** (on the RGB input too):
  - **reg 61 CTM** (main picture contrast, 0..63): same rule with max 63: lo = round(63 × 81/256) = 20,
    CTM = 20 + 43 × contrast // 100 (0 → 20, 50 → 41, 100 → 63).
  - **the colour matrix** (datasheet p26, "Display processor – matrix", 9-bit signed /128): reg 74 MR2M (R−Y from
    Cr), 6C MG1M (G−Y from Cb), 64 MG2M (G−Y from Cr), 5C MB1M (B−Y from Cb). The datasheet's defaults are
    for CTM = 32 ("matrix values are scaled by a factor of 64"); the MCU scales them with CTM so colour follows
    contrast. Measured (contrast 0 / 50 / 100, chroma OSD 5): 74 = 51/106/165, 6C = −13/−26/−41,
    64 = −26/−55/−84, 5C = 68/140/216 (defaults 86, −22, −44, 113). The exact formula needs chroma too:
    chroma (saturation) very likely scales the same matrix (Phase C).
- RGB CONT changes only ACT.

**Storage confirmations (Phase B, 10-23, `tools/automap.py`):** each EEPROM value changed by one step moved exactly
one VDP value by the predicted amount, nothing else (9 tests, final EEPROM diff 0):
PKUN (0x36 bits 3:0) and PKOV (bits 7:4) → reg 69; CLPGI 0x54 → FP 02C; CLPGP 0x56 → FP 02D; CLPMD 0x58 → FP 029;
SLCLVL 0x74 → FP 0CC; IF1 (0x76 bits 4:0) and IF2 (bits 8:5) → FP 04B; VIDEO S-BRT 0x4E → FP 0F9 (IBR) only.
All raw/packed as mapped; these items are no longer "value match only".

**VIDEO-input items, chroma, phase (Phase C, 10-23, `tools/automap.py`):** the input byte 0x1E was set to 00
(LINE A, no signal connected, "no sync" on screen) and the MCU reloaded; the input switch itself is applied by the
reload. Switching RGB → LINE A changed: FP 0F0–0F2 → the VIDEO drive set (0254 021A 01B6), reg 33 (input selector)
08 → 09, reg 32 01 → 00, **reg 63 01F1 → 0007**. Then, on LINE A:
- **SYNC FB = reg 63 POFS3** ("delay from fly back to PLL2"): 0x9E (VIDEO) +1 → reg 63 +1. The RGB set's copy is
  **0xAE** (01F1). ✔
- R/G/B DRIVE (VIDEO, 0x90/0x92/0x94) +1 → FP 0F0/0F1/0F2 +1 ✔; RGB CLAMP (VIDEO, 0x9C) +1 → reg 67 +1 ✔.
- **Phase (0x14) = NTSC tint, FP 01C TINT**, exact for OSD −50, −25, 0, +1, +25, +50:
  `TINT = −1 + 4 × (phase − 50)` (12-bit signed; −1 = 0xFFF is the service TINT value, storage still unknown).
- **Chroma (0x13) scales the colour matrix** (reg 5C MB1M, 64 MG2M, 6C MG1M, 74 MR2M; 9-bit signed):
  | contrast | chroma raw | 5C | 64 | 6C | 74 |
  |---|---|---|---|---|---|
  | 100 | 0 / 25 / 50 | 0 / 98 / 197 | 0 / −38 / −77 | 0 / −19 / −38 | 0 / 75 / 150 |
  | 100 | 75 and 100 | 253 | −96 | −48 | 192 (capped: same for both) |
  | 50 | 50 | 128 | −50 | −24 | 97 |
  | 0 | 0 / 55 / 100 | 0 / 68 / 124 | 0 / −26 / −48 | 0 / −13 / −24 | 0 / 51 / 94 |
  Each coefficient ≈ base × CTM × chroma / constant (datasheet proportions 113 : −44 : −22 : 86), with the MCU
  capping the product so MB1M stays ≤ 253. Exact integer form not found yet (model within about ±1).
- Contrast on LINE A drives the same six values as on RGB; brightness +1 → IBR **and** ABR +4 on either input.
- Back on RGB at the end: EEPROM diff 0, VDP state identical to the Phase B baseline.

**Not classified yet:** user chroma / phase (VIDEO input only).

## Confidence legend
- ✔ confirmed: value match and the datasheet function agrees with the manual (or tested live).
- `~` value match only, or ambiguous (same value in several registers).
- ? not found yet.

## Service items

`Now` = value in the 10-21 dump. Defaults are the manual's 14" column; `(x)` = Ver 1.x firmware, `<x>` = Ver 2.00.
Numbers like (35)<36> mean "item 35 in Ver 1.x, 36 in Ver 2.00". This unit stores SLCLVL right after EHTTM, which
fits the Ver 1.x list, so the (x) numbering probably applies.

### Common items
| No. | Item | Default | EEPROM | Now | VDP | Datasheet name / function | |
|---|---|---|---|---|---|---|---|
| 00 | PKUN | 0 | 0x36 bits 3:0 | 0007 | reg 69 bits 3:0 | PKUN, luma peaking undershoot | ✔ automap B (0x36 = 06 → reg 69 0006) |
| 01 | PKOV | 0 | 0x36 bits 7:4 | 0007 | reg 69 bits 7:4 | PKOV, luma peaking overshoot | ✔ automap B (0x36 = 17 → reg 69 0017) |
| 02 | CPKV | 3 | 0x32 bits 4:0 | 0063 | reg 72 bits 4:0 | CPKV, transient gain Cr | ✔ |
| 03 | CCOR | 3 | 0x32 bits 8:5 | 0063 | reg 72 bits 8:5 | CCOR, coring Cr/Cb | ✔ |
| 04 | CPKU | 3 | 0x34 bits 4:0 | 0103 | reg 7A bits 4:0 | CPKU, transient gain Cb | ✔ |
| 05 | CFS | 1 | 0x34 bit 8 | 0103 | reg 7A bit 8 | filter broad/narrow | ✔ |
| 06 | H CENT | 6E | 0x42 | 006A | reg 7E | HPOS, horizontal position of the digital (video) picture | ✔ no effect on RGB (10-24, from the menu too): use SYNC F B (RGB). ✔ moves the picture horizontally on composite video (10-24) |
| 07 | WDRV | 190 | 0x44 | 0190 | reg 13 bits 9:0 | WDRV, white drive for measurement | ✔ |
| 08 | EWDM | 0 | not stored | — | reg 13 bit 11 | white drive measurement enable | ✔ (datasheet) |
| 09 | VBSO | 14 | 0x46 | 0014 | reg 73 | VBSO, vertical blanking stop | ✔ |
| 10 | AVST | 17 | 0x48 | 0017 | reg 6B | AVST, start of active video | ✔ |
| 11 | AGCREF | 2DC | 0x4A | 02DC | FP 0B2 | AGCREF, sync amplitude reference | ✔ |
| 12 | V CENT | D9D | 0x4C | 0D9D | FP 080 | OFS, DC offset of vertical sawtooth | ✔ live |
| 13 | VIDEO S-BRT | 100 | 0x4E | 0100 | FP 0F9 IBR (base) | IBR = VIDEO S-BRT + 4 × user brightness | ✔ VDP (10-23, `dumps/pvm-14n2u/13_videosbrt_100_101_save.sr`: IBR 011C → 011D, ABR unchanged); storage ✔ automap B (0x4E +1 → IBR +1 only) |
| 14 | RGB S-BRT | 100 | 0x50 | 0100 | FP 0FA ABR (base) | ABR = RGB S-BRT + 4 × user brightness | ✔ tested (10-23: 100 → 101, `dumps/pvm-14n2u/14_rgbsbrt_100_101_save.sr`: ABR +1, IBR unchanged) |
| 15 | RGB CONT | 10D | 0x52 | 010D | FP 0FB ACT (top of the contrast range) | ACT = lo + (RGB CONT − lo) × contrast // 100, lo = round(RGB CONT × 81/256); 9 bits | ✔ automap 10-23 |
| 16 | CLPGI | 1 | 0x54 | 0001 | FP 02C | not in this datasheet edition | ✔ automap B (+1 → FP 02C +1) |
| 17 | CLPGP | 6 | 0x56 | 0006 | FP 02D | not in this datasheet edition | ✔ automap B (+1 → FP 02D +1) |
| 18 | CLPMD | (D)<5> | 0x58 | 00D5 | FP 029 | not in this datasheet edition | ✔ automap B (+1 → FP 029 +1) |
| 19 | DRIVE LIMIT | 1D0 | 0x5A | 01D0 | FP 0FC | IBRM, internal brightness for measurement | ✔ |
| 20 | KILHY | 2 | 0x5C | 0002 | FP 0A9 | KILHY, color killer hysteresis | ✔ |
| 21 | KILVL | 9 | 0x5E | 0009 | FP 0A8 | KILVL, color killer level | ✔ |
| 22 | GAIN | 2D | 0x60 | 0039 | FP 020 | GAIN, AGC gain (read only while AGC on) | ✔ |
| 23 | SGAIN | 2D | 0x62 | 002D | FP 0BE | SGAIN, AGC start gain | ✔ |
| 24 | BCLTHR | (BE) | 0x64 | 00BE | FP 0D4 | BCLTHR, beam current limiter threshold | ✔ |
| 25 | BCLTM | (8) | 0x66 | 0008 | FP 0D2 | BCLTM, BCL time constant | ✔ |
| 26 | BCLG | (B00) | 0x68 | 0B00 | FP 0D3 | BCLG, BCL loop gain (bit 11 must be 1) | ✔ |
| 27 | BCLMIN | 0 | 0x6A | 0000 | FP 0D5 | BCLMIN, BCL minimum contrast | ✔ |
| 28 | INTLC | 0 | 0x6C | 0000 | FP 073 | INTLC, interlace offset | ✔ |
| — | (not a menu item) | — | 0x6E | 0D77 | FP 08B, FP 08F | A0 sawtooth accu0 init / DSCV discharge value | ~ see below |
| 29 | EHT | 2A | 0x70 | 002A | FP 07B | EHT, EHT compensation coefficient | ✔ |
| 30 | EHTTM | (6) | 0x72 | 0006 | FP 07A | EHTTM, EHT time constant | ✔ |
| (31) | SLCLVL | 89C | 0x74 | 089C | FP 0CC | not in this datasheet edition | ✔ automap B (+1 → FP 0CC +1) |
| (32) | IF1 | 1E | 0x76 bits 4:0 | 017E | FP 04B bits 4:0 | not in this datasheet edition (H-PLL proportional gain) | ✔ automap B (IF1 −1 → FP 04B 017D) |
| (33) | IF2 | B | 0x76 bits 8:5 | 017E | FP 04B bits 8:5 | (H-PLL integral gain) | ✔ automap B (IF2 +1 → FP 04B 019E) |
| — | (not a menu item) | — | 0x78 | 020C | FP 0E7 | VSDT, vertical standard select (60 Hz: 525) | ✔ |
Geometry items (50)–(58): the EEPROM holds polynomial coefficients, the MCU converts them into accumulator
values for the VDP. Full explanation in **"Geometry: how it is computed and stored"** below.
| No. | Item | Default | EEPROM | Now | Coefficient | VDP (computed) | |
|---|---|---|---|---|---|---|---|
| (50) | H SIZE | EE | 0x22 word, = OSD << 4 | 0CD0 (OSD CD) | E-W a (DC) | FP 09B (E-W A0) | ✔ tested |
| (51) | PIN PHASE | F4 | 0x27 byte, = OSD (hex) | D8 | E-W b (linear) = signed byte / 128 | FP 09B–09C | ✔ tested (10-23: D8 → DC in 4 steps, `dumps/pvm-14n2u/51_pinphase_d8_dc_save.sr`: 09B −11, 09C +4) |
| (52) | PIN AMP | AE | 0x28 byte, = OSD (hex) | 9F | E-W c (square) = signed byte / 128 | FP 09B–09D | ✔ tested (10-23: 9F → A0, `dumps/pvm-14n2u/52_pinamp_9f_a0_save.sr`: 09B +10, 09C −8, 09D +6) |
| (53) | H SEXY PIN | FE | 0x29 byte, = OSD (hex) | E8 | E-W d (cube) = signed byte / 128 | FP 09B–09E | ✔ tested (10-23: E8 → EC in 4 steps, `dumps/pvm-14n2u/53_hsexy_e8_ec_save.sr`: 09B −3, 09C +3, 09D −5, 09E +4) |
| (54) | H COR PIN | 48 | 0x2A byte, = OSD (hex) | 46 | E-W e (4th power) = signed byte / 128 | FP 09B–09F | ✔ tested (10-23: 46 → 47, `dumps/pvm-14n2u/54_hcor_46_47_save.sr`: 09B +3, 09C −5, 09D +10, 09E −16, 09F +12) |
| (55) | V PO | 0 | 0x20 byte, = OSD (hex) | 55 | vertical a (DC) = byte + 1 (integer model; + 2 with decimal weights) | FP 08B (A0) +1 per step, FP 08F = A0 | ✔ tested (10-23: 55 → 56, `dumps/pvm-14n2u/55_vpo_55_56_save.sr`) |
| (56) | V SIZE | 53 | 0x24 byte | 50 | vertical b (linear) = byte / 128 | FP 08B, 08C, FP 08F = A0 | ✔ tested |
| (57) | V LIN DOWN | FF | 0x25 byte, = OSD (hex) | 03 | vertical c (square) = signed byte / 128 | FP 08B–08D, FP 08F = A0 | ✔ tested (10-23: 03 → 04, `dumps/pvm-14n2u/57_vlindown_03_04_save.sr`: A0 +5, A1 −8, A2 +3, A3 0) |
| (58) | V LINE UP | EE | 0x26 byte, = OSD (hex) | EF | vertical d (cube) = signed byte / 128 | FP 08B–08E, FP 08F = A0 | ✔ tested (10-23: EF → F0, `dumps/pvm-14n2u/58_vlinup_ef_f0_save.sr`: A0 −6, A1 +13, A2 −10, A3 +8) |

**Test convention:** after each one-item test the value is set back and saved again, so "Now" is the value the
monitor runs with, not the test value. Exceptions kept on purpose: V SIZE 50 (was 4F) and H SIZE CD (was CC).
Current 0x20–0x2A = 55 00 D0 0C 50 03 EF D8 9F E8 46.

Every vertical item changes A0, so **FP 08F (DSCV) changes with all of them**: the MCU keeps DSCV = A0 (see
"Geometry", "DSCV must follow A0"). E-W has no such register.
| (59) | CHROMA (chroma center) | 55 | **0x2B** (byte) | 70 | colour matrix: base of **reg 5C MB1M** (B−Y from Cb), scaled by user contrast × chroma | blue colour-difference gain | ✔ (10-24, `dumps/pvm-14n2u/59_chroma_70_71_save.sr`: SAVE 0x2B 70 → 71, reg 5C 0044 → 0045, nothing else) |
| (63) | MODEL | 1 with RGB / 0 without | **0x00** (byte) | 01 | — (MCU only, no VDP change) | read alone (1 byte) at boot | ✔ (10-24, `dumps/pvm-14n2u/63_model_01_00_save.sr`: SAVE 0x00 01 → 00, nothing on bus 0). Keep 01 on this unit (RGB input) |
| (64) | COMB | (C3) | **0x40** (word) | 00C3 | **FP 043** (written on change, not in the 1.3 s refresh) | timing for the NTSC comb filter (not in this datasheet edition) | ✔ (10-24, `dumps/pvm-14n2u/64_comb_00c3_00c4_save.sr`: SAVE 0x40 C3 → C4, FP 043 = 00C4) |
| (65) | DA TRIM | <200> | ? | | ? | Ver 2.00 only | ? |

### Settings per input (VIDEO / ANALOG-RGB)
| No. | Item | Default | EEPROM | Now | VDP | Datasheet | |
|---|---|---|---|---|---|---|---|
| (35)–(37) | R/G/B DRIVE | 254 / 21A / 1B6 | 0x90 / 0x92 / 0x94 | 0254 / 021A / 01B6 | — (not sent in this capture) | | ~ exact defaults |
| ? | second R/G/B drive set | | 0xA0 / 0xA2 / 0xA4 | 0339 / 030E / 0335 | FP 0F0 / 0F1 / 0F2 | WDR / WDG / WDB white drive. **Latched only after writing WDB** | ✔ |
| (38)–(40) | R/G/B C/O | (D / 14 / 6) | **0x96 / 0x98 / 0x9A** (words, 9 bits; common to both inputs) | 01FF ×3 | **reg 6E / 6A / 66** | CR / CG / CB cutoff | R ✔ (10-24, `dumps/pvm-14n2u/38_rco_1ff_000_001_save.sr`: SAVE 0x96 01FF → 0001, reg 6E 01FF → 0000 → 0001); G ✔ (`dumps/pvm-14n2u/39_gco_1ff_000_001_save.sr`: SAVE 0x98 01FF → 0001, reg 6A 01FF → 0000 → 0001); B by elimination |
| (41) | RGB CLAMP | 180 | 0x9C, 0xAC | 0180 | reg 67 | POFS2, delay front sync → PLL2 for RGB | ~ |
| (42) | SYNC F B | 7 | 0x9E (VIDEO), 0xAE (RGB) | 0007 / 01F1 | reg 63 | POFS3, delay from fly back to PLL2 | ✔ automap C (0x9E +1 → reg 63 +1 on LINE A). On RGB it moves the picture (0xAE); on composite video no visible change (10-24): there H CENT positions the picture |
| ? | | | 0xA6 / 0xA8 / 0xAA | 00AD / 00C7 / 00BF | — | | ? |
| (42) | SYNC F B (RGB set) | | 0xAE | 01F1 | reg 63 | POFS3 | ✔ (active on RGB; see SYNC F B) |
| (60)–(62) | R/G/B C/O REF | A0 / 70 / 50 | 0xF2 / 0xF1 / 0xF0 (bytes, reversed) | A0 70 50 | — (not sent in this capture) | | ~ exact defaults |

**Live white drive (host tool, 10-23):** the drives are raw items, so they can be changed live, but only the set
of the active input is on the VDP, and the VDP applies new R/G values only after WDB (FP 0F2) is written
(datasheet p32 note). The tool reads the input (EEPROM 0x1E) and writes R or G followed by the current B value.

Two drive sets: 0x90–0x94 holds the manual's defaults exactly, while the VDP receives the 0xA0–0xA4 values.
The monitor was on the **RGB input** in all captures so far, which supports 0x90 = VIDEO set, 0xA0 = RGB set.

**Input switch RGB → LINE B (10-22, bus 1):** before saving 0x1E, the MCU reads 0x90–0x97, 0x9C–0xA3 and
0xF0–0xF7 (8-byte reads). So **0x90–0x9F = VIDEO-input set** (R/G/B DRIVE 0254/021A/01B6, 0x96 = 01FF,
RGB CLAMP 0x9C = 0180, **SYNC FB 0x9E = 0007**), and the C/O REF bytes at 0xF0 are re-read too. 0x98/0x9A were
not read. LINE B → LINE A re-reads the same three blocks, so LINE A and LINE B share the VIDEO set.
0xA0–0xAF is almost certainly the RGB-input set. Bus 0 not checked yet (expect FP 0F0–0F2 ← 0254/021A/01B6).
Either these are the VIDEO and RGB sets and the active one depends on the input, or 0x90 is something else.
Test: switch input and capture bus 0.

### Settings per line frequency (525/60 block at 0xC0, 625/50 block at 0xB0)
Confirmed 10-22: forcing 525/60 in the system select menu makes the MCU read 0xC0–0xC9; forcing 625/50 reads
0xB0–0xB9 (two 8-byte reads at +0 and +7), then it saves 0x1C. Switching to auto reads nothing. The MCU fetches
these blocks from the EEPROM on demand.
| No. | Item | Default 60 / 50 | EEPROM 60 / 50 | Now 60 / 50 | VDP | Datasheet | |
|---|---|---|---|---|---|---|---|
| (43) | PMST | 14 / 14 | 0xC0 / 0xB0 | 0014 / 0014 | reg 7B | PMST, picture measurement start | ✔ (50 Hz: 0xB0 +1 → reg 7B +1 on reload; 60 Hz: 0xC0 +1 → reg 7B +1 only after a power cycle, 10-24) |
| (44) | PMSO | F9 / 132 | 0xC2 / 0xB2 | 00F9 / 0132 | reg 77 | PMSO, picture measurement stop | ✔ |
| (45) | TML | B / B | 0xC4 / 0xB4 | 000B / 000B | reg 7F | TML, tube measurement line | ✔ (50 Hz: 0xB4 +1 → reg 7F +1, 10-24) |
| (47) | H BLK2 (H blanking start) | 0 / 1 | 0xC6 / 0xB6 (byte) | FA / 00 | reg 17 | HBST, start of H blanking | ✔ storage (switch test); item name ~ |
| (46) | H BLK1 (H blanking stop) | 2E / 2C | 0xC7 / 0xB7 (byte) | 30 / 32 | reg 16 | end of H blanking | ✔ storage (switch test); item name ~ |

**Standard switch on bus 0** (`dumps/pvm-14n2u/system_select_50_60_auto.sr`, 10-22: → 625/50 at 2.49 s, → 525/60 at 4.91 s, → auto
at 7.39 s). The MCU runs an "apply standard" burst (~15 back-to-back writes, no field gaps). Values that differ:
| VDP | 525/60 | 625/50 | Datasheet | Source |
|---|---|---|---|---|
| FP 01B | 0001 | 0000 | SDT standard select (1 NTSC M, 0 PAL B/G/H/I) | MCU |
| reg 34 | 02 | 01 | standard select (010 NTSC, 001 PAL) | MCU |
| reg 30 | 39 | 19 | luma notch; datasheet defaults NTSC 57 = 0x39, PAL 25 = 0x19 | MCU |
| FP 0E7 | 020C | 0270 | VSDT (524 / 624) | 60 Hz = EEPROM 0x78 |
| reg 77 | 00F9 | 0132 | PMSO | EEPROM 0xC2 / 0xB2 |
| reg 17 | FA | 00 | HBST | EEPROM 0xC6 / 0xB6 |
| reg 16 | 30 | 32 | end of H blanking | EEPROM 0xC7 / 0xB7 |
| FP 08C–08E | 01A8 00B7 0F78 | 0161 007E 0FB2 | V sawtooth A1–A3 | computed |
| FP 09C–09F | 01CC 0065 0B89 0363 | 017E 0043 0D71 019E | E-W parabola A1–A4 | computed |

**V SIZE 4F → 50** (`dumps/pvm-14n2u/56_v_size_4f_50.sr`, 10-22, not saved): FP 08B A0 0D77 → 0D6C (−11), FP 08C A1
01A8 → 01B1 (+9), FP 08F DSCV follows A0 (~0.9 s later, next refresh). A2/A3 unchanged. So V SIZE moves the
sawtooth start and slope together. EEPROM 0x6E (0D77) = the computed A0, not a menu item. OSD V SIZE was 4F
while EEPROM 0x20 = 55, so **0x20 is not V SIZE**. SAVE at 50 (`dumps/pvm-14n2u/56_v_size_50_save.sr`) changed only
**0x24: 4F → 50 (= V SIZE)** and 0x6E → 0D6C (= the new A0).

FP 08B / 09B (A0) don't change with the standard because row 0 of the 50 and 60 Hz tables is the same; A1–A4
use the line-rate table. The MCU computes all of them from the geometry items on a reload (10-24, calcheck: a
V PO step moves A0 with 0x6E untouched); 0x6E is only where SAVE stores the computed A0. "Auto" replays the burst with the detected
standard. Unchanged registers in the burst: 6E/6A/66 cutoff, 67, 63, 32, FP 01C TINT, 31, 33, 22.
Reg 25 toggles 0E ↔ 0F every ~1.3 s at all times: bit 0 = enable tube measurement (cutoff loop). See
"Periodic MCU activity".
| (48) | VBST | FA / 133 | 0xC8 / 0xB8 | 0133 / 0133 | reg 6F | VBST, vertical blanking start | ✔ 50 Hz (0xB8 +1 → reg 6F +1, 10-24); 60 Hz copy ~ (default would be FA) |

### Settings per color standard
| No. | Item | Default | EEPROM | VDP | Datasheet | |
|---|---|---|---|---|---|---|
| (34) | TINT | FFF (trap) / A8 (comb) | **0x7E word, bits 11:0** (12-bit signed; bits 15:12 = F, unchanged) | FP 01C = TINT + 4 × phase (phase OSD) | TINT, NTSC tint angle (512 = π/4) | ✔ (10-23, `dumps/pvm-14n2u/34_tint_fff_000_001_save.sr`: SAVE 0x7E FF FF → 01 F0; FP 01C 0FFF → 0000 → 0001) |
| (49) | L/C DELAY | 3 (NTSC/PAL) / 17 (SECAM) | **0xD0** at 60 Hz, **0xD4** at 50 Hz (bytes; on RGB, 10-24; probably one per colour standard in 0xD0–0xDF; SAVE at 60 Hz wrote only 0xD0 of this block) | **reg 31** = 00 | LDF, luma / chroma matching delay (bits 4:0, clocks; datasheet p25) | ✔ (10-24, `dumps/pvm-14n2u/49_lcdelay_00_01_save.sr`: SAVE 0xD0 00 → 01, reg 31 00 → 01) |

## Geometry: how it is computed and stored

### What the VDP does (datasheet p22 §2.6.3, p33, p34 Table 3–3)
The VDP3108 doesn't have "size" or "pincushion" registers. Its Fast Processor (FP) generates two waveforms
every field:
- the **vertical sawtooth**, which drives the vertical deflection (picture height and vertical position), and
- the **east–west (E-W) parabola**, which modulates the horizontal deflection along the field (picture width
  and pincushion shape).

Each waveform is a polynomial in `x`, where `x` runs from 0 to 1 over one field (top to bottom of the picture):
```
P(x) = a + b·(x−½) + c·(x−½)² + d·(x−½)³ [+ e·(x−½)⁴]      vertical: a..d, E-W: a..e
```
The FP evaluates it with a **chain of accumulators** that are reloaded at the start of every field. The
datasheet says the control processor (here the Sony MCU) must compute the accumulator start values from the
coefficients and write them once:
- vertical: FP **08B A0, 08C A1, 08D A2, 08E A3**
- E-W: FP **09B A0, 09C A1, 09D A2, 09E A3, 09F A4**

All are **12-bit two's complement** (−2048…2047; a negative value `v` is sent as `v + 4096`, e.g. −660 → 0xD6C).

Table 3–3 gives the conversion. Each accumulator is a weighted sum of the coefficients, and the weights depend
on the field rate:

Vertical, 60 Hz:
| | a | b | c | d |
|---|---|---|---|---|
| A0 | 1 | −1365.3 | 682.7 | −682.7 |
| A1 | | 1083.5 | −1090.2 | 1645.5 |
| A2 | | | 429.9 | −1305.8 |
| A3 | | | | 1023.5 |

Vertical, 50 Hz: A0 same as 60 Hz; A1 = 899.6·b − 904.3·c + 1363.4·d; A2 = 296.4·c − 898.4·d; A3 = 585.9·d.

E-W, 60 Hz:
| | a | b | c | d | e |
|---|---|---|---|---|---|
| A0 | 1 | −341.3 | 1365.3 | −85.3 | 341.3 |
| A1 | | 134.6 | −1083.5 | 102.2 | −548.5 |
| A2 | | | 849.3 | −161.2 | 1305.5 |
| A3 | | | | 125.6 | −2046.6 |
| A4 | | | | | 1584.8 |

E-W, 50 Hz: A0 same as 60 Hz; A1 = 111.9·b − 899.6·c + 84.8·d − 454.5·e; A2 = 586.8·c − 111.1·d + 898.3·e;
A3 = 72.1·d − 1171.7·e; A4 = 756.5·e.

**Sign note:** the datasheet prints the 50 Hz A2 `d` weights as +898.4 (vertical) and +111.1 (E-W). The
captured 50 Hz values only match with **−898.4 and −111.1** (the 60 Hz counterparts are negative too), so
the printed signs look like a misprint. The values above are the corrected ones.

That's why switching 50/60 Hz changes A1..A4 but not A0 (see "Standard switch on bus 0"): same coefficients,
different weights, and the A0 row is identical for both rates.

Related vertical FP registers: **080 OFS** = DC offset added after the sawtooth (this is V CENT, independent
of EHT compensation), **08F DSCV** = output value during retrace ("typically same as A0"; the MCU writes A0
there too), **073 INTLC** = interlace offset, **07B/07A EHT/EHTTM** = height compensation for beam current.

### What each coefficient does
(General CRT deflection behaviour, not from the datasheet; the datasheet only shows the shapes in Fig. 2–29.)
| | Vertical sawtooth | E-W parabola (horizontal width along the field) |
|---|---|---|
| a | DC level → vertical position | DC level → overall width (**H SIZE**) |
| b | slope → picture height (**V SIZE**) | tilt → width differs top vs bottom (trapezoid, **PIN PHASE**) |
| c | square term → top vs bottom linearity balance | parabola → pincushion amount (**PIN AMP**) |
| d | cube term → S-correction (symmetric linearity) | corner asymmetry |
| e | — | 4th-power term → corner correction |

### How the PVM stores it (EEPROM 0x20–0x2A)
The EEPROM holds the coefficients (what the service menu shows), not the accumulator values. On every change,
the MCU recomputes the accumulators with Table 3–3 and writes them to the VDP.
| EEPROM | Now | Coefficient | Scaling | Item | Evidence |
|---|---|---|---|---|---|
| 0x20 | 55 | vertical a | byte + 1 (DAC units; integer model, see "Exact MCU arithmetic") | V PO | ✔ tested: one step → A0 +1 only, model predicts 0xD6D exactly |
| 0x21 | 00 | ? | | | |
| 0x22–0x23 | D0 0C (0CD0) | E-W a | word as 12-bit signed (0xCD0 = −816) = OSD × 16 | H SIZE | ✔ tested |
| 0x24 | 50 | vertical b | byte / 128 (0x50 → 0.625) | V SIZE | ✔ tested |
| 0x25 | 03 | vertical c | signed byte / 128 | V LIN DOWN | ✔ tested: one step → A0 +5, A1 −8, A2 +3, A3 unchanged |
| 0x26 | EF | vertical d | signed byte / 128 (EF = −17) | V LINE UP | ✔ tested: one step → A0 −6, A1 +13, A2 −10, A3 +8 |
| 0x27 | D8 | E-W b | signed byte / 128 (D8 = −40) | PIN PHASE | ✔ tested: 4 steps → 09B −11 (−3/−3/−2/−3), 09C +4 |
| 0x28 | 9F | E-W c | signed byte / 128 (9F = −97) | PIN AMP | ✔ tested: one step → 09B +10, 09C −8, 09D +6 |
| 0x29 | E8 | E-W d | signed byte / 128 (E8 = −24) | H SEXY PIN | ✔ tested: 4 steps → 09B −3, 09C +3, 09D −5, 09E +4, 09F unchanged |
| 0x2A | 46 | E-W e | signed byte / 128 (46 = +70) | H COR PIN | ✔ tested: one step → 09B +3, 09C −5, 09D +10, 09E −16, 09F +12 |
| 0x6E–0x6F | 6C 0D (0D6C) | vertical A0 | the computed result, also saved by SAVE; sent to FP 08B **and** FP 08F | — | ✔ follows A0 |

All nine items were first placed by the model fit and the manual's item order (50)–(58), then **each confirmed
with a one-item SAVE test (10-22/23)**: the EEPROM byte that changed, and the set of accumulators that moved,
matched the predicted coefficient every time (`a` → A0 only, `e` → A0–A4, …). Every stored geometry byte
equals the value shown in the service menu (hex).

This is case 3 of "How values reach the VDP".

### Worked example (current values, 60 Hz)
Vertical: a = 0x55 + 2 = 87, b = 0x50/128 = 0.625, c = 3/128 = 0.0234, d = −17/128 = −0.1328
```
A0 = 87 − 1365.3·0.625 + 682.7·0.0234 − 682.7·(−0.1328) = −659.6 → −660 → 0xD6C   (seen 0D6C)
A1 = 1083.5·0.625 − 1090.2·0.0234 + 1645.5·(−0.1328)   =  433.1 →  433 → 0x1B1   (seen 01B1)
A2 = 429.9·0.0234 − 1305.8·(−0.1328)                    =  183.5 →  183 → 0x0B7   (seen 00B7)
A3 = 1023.5·(−0.1328)                                   = −135.9 → −136 → 0xF78   (seen 0F78)
```
E-W: a = 0xCD0 as 12-bit signed = −816, b = −40/128, c = −97/128, d = −24/128, e = 70/128
```
A0 = −816 − 341.3·b + 1365.3·c − 85.3·d + 341.3·e = −1541.3 → 0x9FB   (seen 09FC)
A1 = 134.6·b − 1083.5·c + 102.2·d − 548.5·e        =   459.9 → 0x1CC   (seen 01CC)
A2 = 849.3·c − 161.2·d + 1305.5·e                  =   100.5 → 0x065   (seen 0065)
A3 = 125.6·d − 2046.6·e                            = −1142.8 → 0xB89   (seen 0B89)
A4 = 1584.8·e                                      =   866.7 → 0x363   (seen 0363)
```
Checked against every captured value (vertical and E-W, 50 and 60 Hz, before and after the V SIZE and H SIZE
changes): all match within ±1 with these decimal weights.

**Exact MCU arithmetic (10-24, `tools/calcheck.py`, 60 Hz):** stepping each geometry item ±1/±2 and reading the
accumulators after each reload gave 45 points (205 values). Decimal weights left A0 off by +1/+2 (E-W) and −1
(vertical). The MCU computes in integers:
- **weights are integers** (the table values rounded, halves away from zero: −1083.5 → −1084, −548.5 → −549);
- **every product is rounded on its own**, halves away from zero, then summed: `A_i = Σ round(W_ij × coef_j)`
  (50 Hz showed it: 72 × −24/128 = −13.5 → −14; "half up" fitted 60 Hz only because no negative tie came up);
- **the DC terms are `v_po + 1` and `h_size + 1`** (the old "byte + 2" for V PO came from fitting the decimal model).

With that, all 205 values are exact, and so is the 10-23 baseline capture (independent EEPROM values), and a
re-run of `calcheck vertical` / `ew` gave 100/100 and 125/125 exact. **50 Hz (10-24, 50 Hz RGB source):** the same
integer treatment of the 50 Hz table (with the two corrected signs) is exact too, vertical 100/100 and E-W 125/125
after the rounding fix: 635 values over both rates, all exact. Still open: E-W A1's first 60 Hz weight (134.6)
fits 134 or 135 equally (the small steps can't tell).
The model file has the integer weights with `round = "each"`.

### What the MCU does on a change (observed)
- **Service-menu step:** recomputes only the accumulators that depend on that coefficient and writes them
  at once, no blackout. V SIZE (b) → A0 and A1 (FP 08B, 08C) immediately, DSCV (08F) on the next 1.3 s refresh.
  V PO (a) → A0 (08B) +1 immediately, DSCV (08F) +1 ~0.9 s later. H SIZE (a) → E-W A0 (09B) only, since `a` only
  appears in the A0 row.
- **50/60 Hz switch:** recomputes A1..A4 with the other table.
- **SAVE:** writes the coefficients (0x20–0x2A) and the computed vertical A0 (0x6E).
- **BINT release / power-on:** reads the coefficients and recomputes.

### Live geometry in the host tool (10-23)
Implemented in `Session.calc_write` (`host/pvm/model.py`) from `[calc.vertical]` / `[calc.ew]` in the model file
(`kind = "matrix"`, evaluated by `host/pvm/calc.py`).
Tested on the simulated bridge seeded with the real ±1 offsets: every one-step change reproduces the MCU's deltas
from the captures within ±1 (V SIZE A0 −11 / A1 +8, MCU −11 / +9; H SIZE 09B +16 exactly; H COR PIN
+3/−4/+10/−16/+12 vs +3/−5/+10/−16/+12). A calibration offset per session absorbs the MCU's rounding; a mismatch
over ±4 stops the write.

### Changing geometry from the Nano
1. **Safe way:** write the coefficient bytes in the EEPROM (BINT asserted), then `b 0`. The MCU recomputes
   everything. ~2 s blackout per apply.
2. **Live way:** compute A0..A4 with Table 3–3 on the host and write the FP registers directly (BINT asserted).
   Instant preview, then commit with way 1. The UI can do this; the formulas above are all it needs.
   **Write the vertical A0 to both FP 08B and FP 08F.** With BINT asserted the MCU doesn't refresh anything, so
   nobody else updates DSCV.

### DSCV must follow A0
FP 08F **DSCV** ("vertical discharge value", datasheet p33) is what the sawtooth output holds **during the
vertical retrace**, while the beam flies back from the bottom to the top. The datasheet says it's "typically same
as A0 init value for sawtooth", and the MCU always keeps it equal to A0 (08B):
- at the end of the retrace the output sits at DSCV, then the next field starts from A0, so equal values mean no
  step at the top of the picture;
- on a menu change the MCU writes A0 at once and DSCV only on its next round-robin refresh (0.9 s later in the
  V PO capture), so for up to ~1.3 s they differ, harmlessly;
- `dumps/pvm-14n2u/55_vpo_55_56_save.sr`: `FP 08B 0D6C -> 0D6D` at 2.901 s, `FP 08F 0D6C -> 0D6D` at 3.821 s.

A tool writing geometry live must keep them equal itself. The E-W parabola has no discharge register.

## How to read a capture (worked example: H SIZE)
A step-by-step method to find where a setting is stored and which VDP register it drives. It doesn't assume
our map, so it should work on other PVM models with the same kind of chassis.

### 0. Know your buses
- Find the two-wire buses on the main board. On the PVM-14N2U: bus 1 = service header CN052 + EEPROM
  (0x50/0x51), bus 0 = MCU ↔ VDP3108 (0x45). Other models may differ: scan or sniff to find the addresses.
  An EEPROM usually sits at 0x50–0x57; Micronas video processors at 0x45 or similar.
- Connect a logic analyzer to both buses and the BINT/jig line if there is one:
  D0 SCL, D1 SDA, D2 BINT, D3 SCL0, D4 SDA0, GND.

### 1. Record one change
- Write down the on-screen value before and after.
- Capture ~10 s: change **one item by one step**, wait 2 s, press SAVE, wait 2 s.
  ```bash
  sigrok-cli -d fx2lafw --config samplerate=4m --time 10000 \
    --channels D0=SCL,D1=SDA,D2=BINT,D3=SCL0,D4=SDA0 -o dumps/pvm-14n2u/50_h_size_cc_cd_save.sr
  ```
- Name the file `<menu number>_<item>_<from>_<to>[_save].sr`, e.g. `50_h_size_cc_cd_save.sr` (item 50 H SIZE,
  CC → CD, then SAVE). User settings have no menu number: `brightness_27_28_27.sr`.
- Wait ~2 s after starting the capture before pressing the button, so the MCU's 1.3 s refresh records every
  "before" value in the capture.

### 2. Bus 0: what changed on the video processor
```bash
python3 tools/sr_i2c.py dumps/pvm-14n2u/50_h_size_cc_cd_save.sr --vdp | grep -v "reg 25"
```
```
3.399961  45 FP 09B     09EC -> 09FC
```
(`reg 25` is filtered out: the MCU toggles it every ~1.3 s for its tube (cutoff) measurement, it's not your
change. See "Periodic MCU activity".)

The raw transactions behind that line (`python3 tools/sr_i2c.py <file> --bus 0`):
```
3.398687  b0  S W@45 29 Sr R@45 00~ P     read FP status (29): 00 = ready
3.399339  b0  S W@45 27 00 9B P           FP write address (27) = 0x09B
3.399961  b0  S W@45 28 09 FC P           FP data (28) = 0x09FC
```
So one step of H SIZE changed exactly one VDP value: **FP 09B, 0x09EC → 0x09FC (+0x10)**.
- Direct registers appear as `W@45 <reg> <data>`; FP values always come as the 29 / 27 / 28 triple.
- Value changes show up within a field (~17 ms) of the button press. Values the MCU derives later (like DSCV)
  can lag by up to one refresh cycle (1.3 s), so keep capturing for 2 s.
- **Missed the step?** (capture started after the button press) `--vdp` then shows nothing. Use `--state`
  instead: it prints the last value written to every register, and since the MCU rewrites all of them every
  1.3 s, any capture longer than that holds the complete VDP state. Compare with an earlier capture:
  ```bash
  python3 tools/sr_i2c.py dumps/before.sr --state > a.txt
  python3 tools/sr_i2c.py dumps/after.sr  --state > b.txt
  diff a.txt b.txt
  ```
  Only valid if exactly one item changed between the two captures.

### 3. Look it up in the datasheet
`docs/VDP3108.pdf` p33, "FP Display Control Register, East–West Parabola": **h'9B = accu0 init value (A0)**.
Section 2.6.3 (p22) explains that A0..A4 are computed by the control processor from polynomial coefficients
(see "Geometry" above). H SIZE only touches A0, and only the `a` coefficient appears in the A0 row of Table 3–3,
so **H SIZE = the E-W `a` coefficient (DC level = width)**.

### 4. Bus 1: where it is stored
```bash
python3 tools/sr_i2c.py dumps/pvm-14n2u/50_h_size_cc_cd_save.sr --bus 1
```
SAVE is a fixed list of 23 writes (always the same order, starting with `FF ← 55`), so compare it against an
earlier SAVE or an EEPROM dump:
```
earlier SAVE:  S W@50 20 55 00 C0 0C 50 03 EF D8 P
this SAVE:     S W@50 20 55 00 D0 0C 50 03 EF D8 P
```
The page write starts at offset 0x20, so the bytes are 0x20, 0x21, 0x22, … Only **0x22** changed: C0 → D0.
No other write in the SAVE list differs.

### 5. Work out the encoding
- The OSD went up by 1, the byte went up by 0x10. So it isn't a plain byte.
- Read it as a little-endian word with the next byte: 0x22/0x23 = `C0 0C` → 0x0CC0, `D0 0C` → 0x0CD0.
  That's **OSD × 16** (CC → 0CC0, CD → 0CD0).
- The VDP value moved by the same +0x10, so the stored word **is** the coefficient in DAC units
  (0xCD0 as 12-bit signed = −816), not a separate index.
- Other items use other encodings (V SIZE: byte, coefficient = byte / 128). Always check the step size in both
  the EEPROM and the VDP before assuming.

### 6. Confirm
- **Model:** plug the stored values into Table 3–3 and compare with the captured FP values (see the worked
  example). If one coefficient is wrong, several A values will be off.
- **Live:** with the Nano, `b 1`, read the bytes (`r 020 0C`), write a new value (`w 022 E0`), `b 0`, and check
  the on-screen value and the picture. Keep a full dump first (`r 000 200`).

### Pitfalls
- **Same value in many places:** small values (0000, 0001, 0006, 0007, 0014) appear in several registers.
  Trust changes, not matching values.
- **Derived values:** one menu item can change several VDP values (V SIZE → 08B and 08C, then 08F), or a VDP
  value can depend on several items.
- **Context-dependent blocks:** line frequency (0xB0/0xC0 blocks), input (0x90 VIDEO / 0xA0 RGB) and colour
  standard select different EEPROM blocks. Note the input and standard in every capture.
- **50/60 Hz:** the same geometry gives different A1..A4 at 50 and 60 Hz.
- **SAVE rewrites everything:** a byte that "changed" may have been edited earlier and saved now. Diff
  against the previous SAVE or dump, not against the manual's defaults.
- **Other PVM models** may use another firmware (different EEPROM layout) or another VDP31xx/VPC chip
  (different FP addresses). The method stays the same: one change, diff both buses, look it up in that
  chip's datasheet.

## EEPROM words with no service item
| EEPROM | Now | Notes |
|---|---|---|
| 0x00 | 01 | **(63) MODEL** (1 = with RGB), read alone at boot; MCU only |
| 0x10–0x1F | user settings | 0x10 volume, 0x11 contrast?, see `claude.md` |
| 0x20–0x2A | 55 00 D0 0C 50 03 EF D8 9F E8 46 | geometry coefficients, see "Geometry" section. 0x21 = 00 unknown (0x20 high byte?) |
| 0x2B | 70 | **(59) CHROMA (service)**: base of the B−Y colour coefficient (reg 5C MB1M), see service items |
| 0x30 | 0014 → 0027 | **saved CTM** (reg 61), written by SAVE like A0 at 0x6E: 10-24 SAVEs at contrast 46 wrote `30 ← 27 00` = CTM 0x27 (`dumps/pvm-14n2u/49_video_lcdelay_00_01_save.sr`, `dumps/pvm-14n2u/34_video_tint_off_00_01_save.sr`); 0014 was the CTM at contrast 1. Whether the MCU uses it on reload is unknown |
| 0x38–0x3E | 0096 01DA 01B3 00C5 | no match on bus 0 (0x40 = 00C3 is COMB, see above) |
| 0x6E | 0D6C | → FP 08B (A0) and FP 08F (DSCV). The computed vertical A0, written by SAVE (0D77 before V SIZE 4F → 50, 0D6C before V PO 55 → 56) |
| 0x80–0x83 | FF FF 00 00 | never read by the MCU in the captures |
| 0xE0–0xED | 2F 64 32 2D 32 04 00 04 … | never read by the MCU in the captures |
| 0xF4–0xF6 | 67 A2 E3 | read at boot and reload |
| 0xFF | 55 | "EEPROM valid" marker, read first at boot |

## VDP writes with no EEPROM source (constants in MCU firmware)
Direct: 07=0000, 14=00, 15=4768 (horizontal drive control), 16=30, 17=FA, 20=03 (IFC), 22=48, 23=9F (PIOV),
24=87, 25=0E/0F, 2A=0800, 30=39 (luma notch, NTSC = 57 = 0x39), 31=00, 32=01, 33=08 (input selector),
34=02 (standard select: NTSC), 41=001F, 44/48/4C=0003 (DRCT/DGCT/DBCT), 45=0000, 49/4D=00FF (SLO/SLDC),
51=0000 (BRM brightness), 53=009F (PBFB), 54=0000, 57/5B/5F=0002/0001/0002 (PLL2/3 coefficients),
59=0006 (black level expander), 5C=003E, 5D=0078 (BTHR = 120), 64=01E8, 6C=01F4, 6D=0004 (COR),
71=0060 (PBERGB), 74=002F, 75=0000, 79=0000, 7C=0000, 7D=0000.

FP: 01D=0060, 026=0000 (XLG line lock), 055=0000, 058=0000 (DVCO), 072=0007 (DSCC), 075=0000 (BCLTST),
08C=01A8, 08D=00B7, 08E=0F78 (sawtooth A1–A3), 09B–09F = 09EC 01CC 0065 0B89 0363 (E-W A0–A4),
0B4=0FFF, 0BA=07FF, 0BD=00C8, 0F9=012C (IBR), 0FA=012C (ABR), 0FB=0056 (ACT).
Some of these are probably computed from service items (geometry, sub-brightness/contrast) or user
settings (brightness, contrast), not constants. Only a capture with one item changed will tell.

## Periodic MCU activity (status reads, tube measurement)
Things the MCU does on bus 0 all the time, independent of any setting. They show up in every capture and are
not caused by your change.

### Reg 25 toggle: tube measurement (filter it out with `grep -v "reg 25"`)
Reg 25 is **PMC, tube and picture measurement control** (datasheet p28):
| Bit | Meaning | Value used |
|---|---|---|
| 0 | disable / enable **tube measurement** | toggled |
| 1 | 80 / 40 kHz bandwidth for picture measurement | 1 (40 kHz) |
| 2 | enable picture measurement start | 1 |
| 3 | large / small picture measurement window | 1 (small) |
| 4 | measure / ADC offset check | 0 |

The MCU writes **0F** (tube measurement on) for one field (~16.7 ms) and then **0E** (off) again, about every
1.3 s (the round-robin refresh period; sometimes sooner):
```
1.559725  45 reg 25    0E -> 0F      tube measurement on
1.576439  45 reg 25    0F -> 0E      off, one field later
2.847027  45 reg 25    0E -> 0F      next cycle, 1.287 s later
```
During that field the VDP measures the tube (cutoff/leakage per gun, white drive) in the vertical blanking
interval. The MCU reads the results from the measurement result registers (reg 1A–1F: MRMIN, MRMAX, MRWDR,
MRCB, MRCG, MRCR; it reads 1B/1C/1D) and closes the cutoff and white-balance loops itself ("all control loops
are closed via the external control microprocessor", datasheet p18). That's the automatic black-level /
cutoff control of the tube, which is why it runs all the time. `tools/sr_i2c.py --vdp` lists every toggle as a
value change, so filter it out when looking for your change.

### Status reads
| What | Datasheet | Seen |
|---|---|---|
| reg 1B | measurement result (one of MRMIN…MRCR, exact mapping not decoded) | 22–24 |
| reg 1C / 1D | measurement results (MRMIN / MRMAX per the datasheet table order) | 0E–10 / 1C–1F |
| FP 053 | ASR, standard recognition status | 002B |
| FP 059 | ADJUST, crystal offset (line lock) | 0000 |
| FP 0EB | NLPF, lines per field | 0105 = 261 |

## User settings
Stored at EEPROM 0x10–0x1F, written immediately on change (no SAVE), two 8-byte pages ~18 ms apart.
| EEPROM | Now | Meaning |
|---|---|---|
| 0x10 | 1E | volume = 30 (confirmed: 28 → 1C, 29 → 1D) |
| 0x11 | 00 | **contrast**, raw = OSD (0–100). Drives FP 0FB ACT (formula with RGB CONT), reg 61 CTM (20 + 43 × c // 100) and the colour matrix (reg 74/6C/64/5C), see "three cases" | 
| 0x12 | 4D | **brightness**, raw = OSD + 50 (OSD 27 → 4D, 28 → 4E). VDP: FP 0F9 IBR = FP 0FA ABR = 256 + 4 × OSD (see "How values reach the VDP", case 3) |
| 0x13 | 32 | **chroma**, raw = OSD + 50. Scales the colour matrix (reg 5C/64/6C/74) together with contrast (automap C) |
| 0x14 | 32 | **phase** (NTSC tint), raw = OSD + 50. VDP: FP 01C TINT = −1 + 4 × OSD (automap C, exact) |
| 0x18–0x1B | 02 00 00 00 | never changed so far |
| **0x1C** | 00 | **system select: 00 auto, 01 525/60, 02 625/50** (10-22 capture) |
| 0x1D | 00 | never changed so far |
| 0x1E | 02 | **selected input: 00 LINE A, 01 LINE B, 02 RGB** (10-22) |
| 0x1F | 01 | **previous input**, same encoding (RGB→B: 1E=01 1F=02; B→A: 1E=00 1F=01; RGB→A: 1E=00 1F=02) |

- **Volume is not on the VDP:** MCU pin 60 is O-VOL (analog output to the audio stage, schematic p27).
  Only reachable through EEPROM + BINT reload.
- **Picture controls are probably VDP registers** (not caught yet, nobody touched them in `bus0_idle.sr`). Candidates:
  contrast → reg 61 CTM (0014); brightness → reg 51 BRM (0000) and/or FP 0F9 IBR (012C); chroma → FP 0A0 ACCREF
  (not in the idle capture); phase → FP 01C TINT (0FFF). VIDEO S-BRT / RGB CONT are probably folded into these.

## Tests for 2026-10-22
Capture all 5 channels (D0 SCL, D1 SDA, D2 BINT, D3 SCL0, D4 SDA0), one capture per test, each export in its
own folder (`dumps/<test>/`) so nothing is overwritten. Note the on-screen value before and after.

**User settings (BINT released, normal operation):**
1. **Contrast** one step up, wait 2 s, one step down. Bus 1 → which EEPROM byte; bus 0 → which VDP register.
2. **Brightness**, same.
3. **Chroma**, same.
4. **Phase**, same.
5. **Volume**, same (bus 1 only expected; confirms nothing goes to the VDP).
   **Result (10-22, bus 1):** 30 → 31 → 30 wrote 0x10 = 1F then 1E, nothing else changed. Each change rewrites
   both pages (0x10–0x17, then 0x18–0x1F ~19–21 ms later), immediately, no SAVE. Bus 0 not checked yet.

**BINT behaviour:**
6. `b 1`, then turn one control (e.g. volume). Does the MCU still write 0x10–0x1F or talk to the VDP while BINT
   is low? Then `b 0`. (Decides whether front-panel use is safe during a write session.)
   Hint (10-22, CLI, no capture): a volume change on the monitor while the shell held BINT didn't show up in
   the EEPROM, so the MCU probably doesn't save while BINT is low. Confirm with a capture.

**Service items (SAVE-and-diff with the Nano, then a ~2 s bus-0 capture):**
`b 1`, `r 000 100` → `b 0` → change one item one step → SAVE → `b 1`, `r 000 100` → diff.
7. ~~V SIZE~~ done (0x24, vertical b). ~~H SIZE~~ done (0x22 word, E-W a).
8. **Geometry model check**, one step + SAVE each: ~~V PO~~ done (0x20, A0 only, 10-23), ~~V LIN DOWN~~ done (0x25, A0–A2, 10-23), ~~V LINE UP~~ done (0x26, A0–A3, 10-23),
   ~~PIN AMP~~ done (0x28, E-W A0–A2, 10-23), ~~H COR PIN~~ done (0x2A, E-W A0–A4, 10-23), ~~PIN PHASE~~ done
   (0x27, E-W A0–A1, 10-23), ~~H SEXY PIN~~ done (0x29, E-W A0–A3, 10-23). **All geometry items confirmed.** The set of FP values that change tells which coefficient it is.
9. **VIDEO S-BRT, RGB S-BRT, RGB CONT**: how FP 0F9/0FA/0FB are computed.
10. **PKUN**: settles 0x36/reg 69 vs SYNC FB.
11. **Switch input** (VIDEO ↔ RGB): which drive set (0x90 vs 0xA0) reaches FP 0F0–0F2.
12. **R C/O**: cutoff storage and registers 66/6A/6E.

**Optional, risky:**
13. **0x30 probe** with BINT low and a capture running: `i 30 00 1`. Last time SCL got stuck after 0x30 was
    addressed. Recovery: `b 0`, else power-cycle the monitor.
