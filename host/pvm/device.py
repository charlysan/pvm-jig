import time

from .link import LinkError

FPRD, FPWR, FPDAT, FPSTA = 0x26, 0x27, 0x28, 0x29
EEPROM_SIZE = 0x200
JIG_TIMEOUT = 1.0


def hexbytes(data):
    return " ".join("%02X" % b for b in data)


def format_dump(data, base=0):
    lines = ["%03X: %s" % (base + i, hexbytes(data[i:i + 16])) for i in range(0, len(data), 16)]
    return "\n".join(lines + ["OK %04X" % (sum(data) & 0xFFFF)])


def parse_dump(path):
    data = bytearray()
    with open(path) as f:
        for line in f:
            if ":" in line:
                data += bytes(int(x, 16) for x in line.split(":", 1)[1].split())
    return bytes(data)


class Device:
    def __init__(self, link, vdp=0x45):
        self.link = link
        self.vdp = vdp
        self.manual = False

    def set_manual(self, on):
        self.link.cmd("g %d" % (not on))
        self.manual = on

    def raw(self, line):
        payload, lines = self.link.cmd(line)
        return "\n".join(lines + [("OK " + payload).strip()])

    def bint(self, on=None):
        payload, _ = self.link.cmd("b" if on is None else "b %d" % bool(on))
        if on is True and not self.manual and not self.wait_jig():
            self.bint(False)
            raise LinkError("MCU did not enter jig mode (no VDP at %02X after %.0f s), check the BINT wire"
                            % (self.vdp, JIG_TIMEOUT))
        return payload == "1"

    def wait_jig(self, timeout=JIG_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.vdp in self.scan(self.vdp, self.vdp):
                return True
            time.sleep(0.05)
        return False

    def hold(self):
        if not self.manual and not self.bint():
            self.bint(True)

    def scan(self, lo=0x08, hi=0x77):
        payload, _ = self.link.cmd("s %x %x" % (lo, hi))
        return [int(x, 16) for x in payload.split()]

    def ee_read(self, off, n):
        payload, lines = self.link.cmd("r %x %x" % (off, n))
        text = " ".join(l.split(":", 1)[1] for l in lines) if lines else payload
        data = bytes(int(x, 16) for x in text.split())
        if len(data) != n:
            raise LinkError("eeprom read %03X: got %d of %d bytes" % (off, len(data), n))
        return data

    def ee_write(self, off, data):
        for i in range(0, len(data), 16):
            self.link.cmd("w %x %s" % (off + i, hexbytes(data[i:i + 16])))

    def ee_diff(self, ref):
        now = self.ee_read(0, len(ref))
        return [(i, now[i], ref[i]) for i in range(len(ref)) if now[i] != ref[i]]

    def ee_restore(self, ref, diff):
        for i, _now, new in diff:
            self.ee_write(i, bytes([new]))
        return [i for i, _now, new in diff if self.ee_read(i, 1)[0] != new]

    def i2c_read(self, addr, reg, n=1):
        payload, _ = self.link.cmd("i %x %x %x" % (addr, reg, n))
        return bytes(int(x, 16) for x in payload.split())

    def i2c_write(self, addr, data):
        self.link.cmd("o %x %s" % (addr, hexbytes(data)))

    def reg_read(self, reg, size=2):
        return int.from_bytes(self.i2c_read(self.vdp, reg, size), "big")

    def reg_write(self, reg, value, size=2):
        self.i2c_write(self.vdp, bytes([reg]) + value.to_bytes(size, "big"))

    def fp_wait(self, tries=20):
        for _ in range(tries):
            if not self.i2c_read(self.vdp, FPSTA)[0] & 0x07:
                return
        raise LinkError("FP busy")

    def fp_read(self, addr):
        self.fp_wait()
        self.i2c_write(self.vdp, bytes([FPRD]) + addr.to_bytes(2, "big"))
        self.fp_wait()
        return int.from_bytes(self.i2c_read(self.vdp, FPDAT, 2), "big")

    def fp_write(self, addr, value):
        self.fp_wait()
        self.i2c_write(self.vdp, bytes([FPWR]) + addr.to_bytes(2, "big"))
        self.i2c_write(self.vdp, bytes([FPDAT]) + value.to_bytes(2, "big"))
