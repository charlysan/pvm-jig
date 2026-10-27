---
name: pvm-mapping
description: Step-by-step procedure for mapping the service-menu settings of a Sony PVM (or another CRT with a Micronas VDP/VPC video processor) over I2C - which EEPROM bytes store each item and which video-processor registers it drives, either with front-panel tests and captures or automatically (EEPROM change + BINT reload, tools/automap.py). Use it whenever the user wants to map, find, decode or verify a service or user setting, plans or analyzes a logic-analyzer capture of the PVM buses (sigrok .sr files in dumps/), takes or compares EEPROM dumps, runs automatic mapping phases, adds items to host/models/*.toml or register_map.md, or starts on a different PVM model - even if they only say "next item", "next phase", "I captured X", or paste sr_i2c.py output.
---

# Mapping PVM service settings over I2C

The procedure lives in `architecture.md` (repo root). Read the section that matches the task:

| Task | Section |
|---|---|
| New monitor, session baseline, one-item test, decoding a capture, recording the result | "Mapping a new monitor" |
| Mapping from the computer alone (EEPROM change + reload) | "Automatic mapping" |
| Checking a formula against the MCU | "Checking the formulas" |
| A calculated value spread over several registers | "The colour matrix (worked example of a calculated value)" |
| A VDP value the model doesn't explain | "Finding which byte a value comes from" |
| A change that doesn't apply on BINT release | "Power-cycle test" |
| Model file format (items, calc, safe ranges) | "Model files" |

Results for the PVM-14N2U are in `register_map.md`; captures and dumps in `dumps/<model>/`.

Rules that are never skipped:
- Back up the EEPROM before any write, and restore every test value; the session ends with an EEPROM diff of 0
  against the backup (apart from values the user decided to keep).
- One item per capture, one byte per automatic test, small steps.
- Unknown EEPROM bytes only with the user's explicit OK, and last.
- Never write the MODEL byte, the "valid" marker (0xFF) or danger registers directly.
- Stop after each phase so the user can commit and look at the monitor.
- Mark guesses as guesses; ask before writing up conclusions that rest on assumptions.
