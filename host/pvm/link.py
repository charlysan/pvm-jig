import time

import serial
from serial.tools import list_ports

BAUD = 115200
READY = "READY pvm"


class LinkError(Exception):
    pass


def find_port():
    for p in list_ports.comports():
        if "usbserial" in p.device or "wchusbserial" in p.device:
            return p.device.replace("/dev/tty.", "/dev/cu.")
        if p.device.startswith(("/dev/ttyUSB", "/dev/ttyACM")):
            return p.device
    return None


class Link:
    def __init__(self, port=None, ser=None, timeout=3.0):
        self.timeout = timeout
        opened = ser is None
        if opened:
            port = port or find_port()
            if not port:
                raise LinkError("no serial port found, use -p")
            ser = serial.Serial(port, BAUD, timeout=0.1)
        self.ser = ser
        self.port = port
        try:
            self.banner = self._sync()
        except LinkError:
            if opened:  # not a pvm-jig Nano: don't leave the port open
                ser.close()
            raise

    def _readline(self, deadline):
        while time.time() < deadline:
            line = self.ser.readline().decode("ascii", "replace").strip()
            if line:
                return line
        return None

    def _sync(self):
        deadline = time.time() + 3.0
        while (line := self._readline(deadline)) is not None:
            if line.startswith(READY):
                return line
        self.cmd("b")
        return "no banner (Nano did not reset)"

    def cmd(self, line):
        self.ser.write((line + "\n").encode("ascii"))
        lines = []
        deadline = time.time() + self.timeout
        while (reply := self._readline(deadline)) is not None:
            if reply.startswith("OK"):
                return reply[2:].strip(), lines
            if reply.startswith("ERR"):
                raise LinkError(f"{line}: {reply}")
            if reply.startswith(READY):
                raise LinkError(f"{line}: Nano reset")
            lines.append(reply)
        raise LinkError(f"{line}: timeout")

    def close(self):
        self.ser.close()
