"""SMBus byte-data transactions on top of the bit-bang layer.

The Nitro Glow V3 protocol is exactly these two operations, matching
OpenRGB's SapphireNitroGlowV3Controller.
"""


class SMBusError(Exception):
    """A transaction failed in a way the caller cannot act on."""


def _check(addr, reg, value=None):
    if not 0 <= addr <= 0x7F:
        raise ValueError("address 0x%x out of 7-bit range" % addr)
    if not 0 <= reg <= 0xFF:
        raise ValueError("register 0x%x out of range" % reg)
    if value is not None and not 0 <= value <= 0xFF:
        raise ValueError("value 0x%x out of range" % value)


class SMBus:
    def __init__(self, bb):
        self.bb = bb

    def write_byte_data(self, addr, reg, value):
        """START, addr+W, reg, value, STOP. False if any phase was NACKed."""
        _check(addr, reg, value)
        self.bb.start()
        try:
            if not self.bb.write_byte(addr << 1):
                return False
            if not self.bb.write_byte(reg):
                return False
            return self.bb.write_byte(value)
        finally:
            self.bb.stop()

    def read_byte_data(self, addr, reg):
        """START, addr+W, reg, RESTART, addr+R, byte, NACK, STOP."""
        _check(addr, reg)
        self.bb.start()
        try:
            if not self.bb.write_byte(addr << 1):
                return None
            if not self.bb.write_byte(reg):
                return None
            self.bb.restart()
            if not self.bb.write_byte((addr << 1) | 1):
                return None
            return self.bb.read_byte(ack=False)
        finally:
            self.bb.stop()
