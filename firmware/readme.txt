PVM-JIG I2C BRIDGE FIRMWARE (pvm_jig.ino)
=========================================

Arduino Nano (classic, 5 V) between a host (serial) and a Sony PVM service header
(e.g. CN052 on the PVM-14N2U): reads/writes the EEPROM and the video processor (VDP3108),
and asserts BINT (jig mode).


WIRING
------
The Nano runs at 5 V like the PVM bus, so it connects directly (no level shifter).

  Nano      CN052
  A4 SDA    3 SDA
  A5 SCL    2 SCL
  D2        4 BINT
  GND       1 GND
            5 AGING: not connected

- Use only a 5 V Nano (classic or Every). 3.3 V boards (Nano 33, ESP32, RP2040) must not be used.
- Wire enables the AVR internal pull-ups (~30-50 k to 5 V), in parallel with the PVM's 4.7 k.
- BINT is open drain in software: asserted = OUTPUT low, released = INPUT (the PVM's 10 k pull-up
  brings it back to 5 V). The pin is never driven high. A Nano reset always releases BINT.
- The USB ground ties the PVM ground to the computer. Check header GND <-> BNC shell continuity
  first: the service manual warns that the chassis can be connected to the AC line.


BUILD AND UPLOAD
----------------
Arduino IDE: board "Arduino Nano"; most clones need "ATmega328P (Old Bootloader)".
With arduino-cli:

  arduino-cli core install arduino:avr
  arduino-cli compile -b arduino:avr:nano:cpu=atmega328old firmware/pvm_jig
  arduino-cli upload  -b arduino:avr:nano:cpu=atmega328old -p /dev/tty.usbserial-* firmware/pvm_jig

Use cpu=atmega328 for boards with the new bootloader. Needs AVR core >= 1.8.3 (Wire timeout).


FLASHING THE RELEASE HEX
------------------------
Each release has pvm-jig-firmware-<version>.zip with pvm_jig-nano.hex, built for the classic Nano
(ATmega328P). The same hex fits both bootloaders; only the upload speed differs. With arduino-cli:

  arduino-cli core install arduino:avr
  arduino-cli upload -b arduino:avr:nano:cpu=atmega328old -p /dev/tty.usbserial-* --input-file pvm_jig-nano.hex

(cpu=atmega328 for the new bootloader). Or with avrdude alone:

  avrdude -p atmega328p -c arduino -P /dev/tty.usbserial-* -b 57600 -D -U flash:w:pvm_jig-nano.hex:i

(-b 57600 = old bootloader, -b 115200 = new). On Windows the port is COMx, on Linux /dev/ttyUSB0.
If the upload times out, try the other bootloader setting. The zip also has the sketch (pvm_jig/), to
build and upload it from the Arduino IDE as above.


CONNECT BY HAND
---------------
115200 baud. The Nano doesn't echo what you type, so use a terminal with local echo:

  picocom -b 115200 --echo --omap crlf /dev/tty.usbserial-*

Ctrl-A Ctrl-X quits.

Opening the port resets the Nano (DTR), which releases BINT: the MCU reloads the EEPROM.
Don't reconnect in the middle of a write session.


PROTOCOL
--------
ASCII, one command per line (CR, LF or both). Case-insensitive. All numbers are hex.
No echo, no prompt. Every command gets exactly one final line, "OK [data]" or "ERR <reason>";
longer output comes before it. On boot the Nano prints "READY pvm 1" (name, protocol version).

EEPROM offsets 000-1FF: 000-0FF -> I2C 0x50, 100-1FF -> 0x51.

  b                 OK 0|1     BINT state (1 = asserted, low)
  b 1               OK 1       assert BINT: the MCU leaves both buses, the VDP becomes reachable
  b 0               OK 0       release BINT: the MCU reloads the EEPROM (picture blanks ~2 s)
  g [0|1]           OK 0|1     BINT gate: 1 (default) = r/w/i/o need "b 1"; 0 = always allowed
                               (manual mode, collisions with the MCU possible)
  s [lo hi]         OK 50 51   scan 08-77 (or lo-hi, max 7F), list addresses that ACK
  r <off> [n]       OK 9D 0D   read n bytes (default 1), data on the OK line for n <= 16
  r <off> <n>       <off>: ..  n > 16: one line per 16-byte block, then "OK <16-bit sum>"
  w <off> <b> ...   OK         single-byte writes (10 ms each, up to 32), read back and verified
  i <dev> <reg> [n] OK 0D 9D   register read: write reg, repeated START, read n (1-16) bytes
  o <dev> <b> ...   OK         raw write: START, dev, bytes, STOP (up to 32). Any device, no verify

Errors:

  ERR cmd                 unknown command
  ERR args                bad/missing arguments, out of range, or line too long (111 chars max)
  ERR bint                BINT not asserted (gate on)
  ERR i2c <addr> <code>   2 address NACK, 3 data NACK, 4 other / short read, 5 timeout
  ERR verify <off> <got>  read-back after a write didn't match

A multi-line read can end in ERR after some data lines; the data before it is valid.


VDP3108 (0x45, BINT asserted)
-----------------------------
  direct register:  i 45 <reg> 2      (16-bit, high byte first) or i 45 <reg> (8-bit)
  FP RAM read:      i 45 29 (expect 00 = ready), o 45 26 <hi> <lo>, i 45 28 2
  FP RAM write:     i 45 29, o 45 27 <hi> <lo>, o 45 28 <hi> <lo>   (takes effect at once)

Example, V CENT (FP 080):

  o 45 26 00 80, i 45 28 2       -> OK 0D 9D
  o 45 27 00 80, o 45 28 0D AD   moves the picture down

"b 0" reloads the stored values. With BINT asserted, avoid probing 0x30 (it held SCL low once):
scan with "s 40 77".


EXAMPLE SESSION
---------------
  b 1          -> OK 1
  r 000 200    -> 000: ... (32 lines)  -> OK 1A2B      backup: run twice, compare the sums
  r 04C 2      -> OK 9D 0D                             V CENT word, little-endian
  w 04C 9E     -> OK
  b 0          -> OK 0                                 picture blanks, comes back with the new value


CAUTIONS
--------
- Byte writes only (10 ms each); the firmware doesn't use EEPROM page writes.
- Don't touch 0xFF (= 0x55): probably the "EEPROM valid" marker.
- Write both bytes of a 16-bit word before releasing BINT; a half-written word is loaded as is.
- No BINT timeout on purpose: if the host dies, BINT stays asserted until "b 0" or a Nano reset.
- Releasing BINT discards unsaved service-menu edits (the MCU's RAM copy is reloaded).
- Keep an EEPROM dump before any write: it's the only way back.
