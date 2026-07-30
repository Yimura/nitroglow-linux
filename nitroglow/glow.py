"""Sapphire Nitro Glow V3 register map.

Register numbers and mode values are taken from OpenRGB's
SapphireNitroGlowV3Controller.h, independently corroborated by the decompiled
TriXX control (see notes/trixx-re.md).
"""

ADDR = 0x28

REG_EXTERNAL_CONTROL = 0x0F
REG_MODE = 0x10
REG_RUNWAY_SPEED = 0x11
REG_RUNWAY_REPEAT = 0x12
REG_COLOR_CYCLE_SPEED = 0x13
REG_RAINBOW_SPEED = 0x15
REG_SERIAL_SPEED = 0x16
REG_RED = 0x1A
REG_GREEN = 0x1B
REG_BLUE = 0x1C
REG_MUSIC_VOLUME = 0x29
REG_BRIGHTNESS = 0x3E

MODES = {
    "rainbow": 0x00,
    "runway": 0x01,
    "color_cycle": 0x02,
    "serial": 0x03,
    "sapphire_blue": 0x04,
    "audio": 0x05,
    "custom": 0x06,
    "off": 0x07,
    "external": 0xFF,
}


class GlowError(Exception):
    """The device did not respond as expected."""


def _byte(name, value):
    if not 0 <= value <= 0xFF:
        raise ValueError("%s 0x%x out of range" % (name, value))
    return value


class Glow:
    def __init__(self, sm, addr=ADDR):
        self.sm = sm
        self.addr = addr

    def _write(self, reg, value):
        if not self.sm.write_byte_data(self.addr, reg, value):
            raise GlowError("no ACK writing 0x%02x to register 0x%02x"
                            % (value, reg))

    def _read(self, reg):
        value = self.sm.read_byte_data(self.addr, reg)
        if value is None:
            raise GlowError("no ACK reading register 0x%02x" % reg)
        return value

    def present(self):
        """True if the device ACKs a read of its mode register."""
        return self.sm.read_byte_data(self.addr, REG_MODE) is not None

    def set_color(self, red, green, blue):
        self._write(REG_RED, _byte("red", red))
        self._write(REG_GREEN, _byte("green", green))
        self._write(REG_BLUE, _byte("blue", blue))

    def get_color(self):
        return (self._read(REG_RED), self._read(REG_GREEN), self._read(REG_BLUE))

    def set_mode(self, mode):
        if mode not in MODES.values():
            raise ValueError("unknown mode 0x%x" % mode)
        self._write(REG_MODE, mode)

    def get_mode(self):
        return self._read(REG_MODE)

    def set_brightness(self, value):
        self._write(REG_BRIGHTNESS, _byte("brightness", value))

    def get_brightness(self):
        return self._read(REG_BRIGHTNESS)

    def set_external_control(self, enabled):
        self._write(REG_EXTERNAL_CONTROL, 1 if enabled else 0)

    def off(self):
        self.set_mode(MODES["off"])
