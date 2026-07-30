"""The probe: does anything actually answer on the DDCVGA pads?

The whole point is to distinguish outcomes that the kernel path collapses into
a single -EIO. Each outcome gets its own exit code and its own message.
"""

import time

from nitroglow import bitbang, glow, smbus

EXIT_OK = 0
EXIT_ACCESS = 2
EXIT_IDENTITY = 3
EXIT_LINES_LOW = 4
EXIT_NACK = 5


class ProbeResult:
    def __init__(self, code, message, y=None, acked=False, per_byte_seconds=None):
        self.code = code
        self.message = message
        self.y = y
        self.acked = acked
        self.per_byte_seconds = per_byte_seconds

    def __repr__(self):
        return ("ProbeResult(code=%d, y=%s, acked=%s, per_byte_seconds=%s)"
                % (self.code, self.y, self.acked, self.per_byte_seconds))


def run_probe(pad_obj, delay=1e-5):
    """Claim the pads, read the line state, and try to address the MCU.

    The caller is responsible for save()/restore() around this; run_probe only
    drives the bus.
    """
    bb = bitbang.BitBang(pad_obj, delay=delay)

    idle = bb.bus_idle()
    y = pad_obj.read_y()

    if not idle:
        return ProbeResult(
            EXIT_LINES_LOW,
            "SCL/SDA do not float high with the pull-down cleared (Y=0x%03x): "
            "no external pull-ups, so most likely nothing is wired to these pads."
            % y,
            y=y,
        )

    sm = smbus.SMBus(bb)
    started = time.monotonic()
    acked = sm.read_byte_data(glow.ADDR, glow.REG_MODE) is not None
    elapsed = time.monotonic() - started
    # A read_byte_data is 4 byte-times: addr+W, reg, addr+R, data.
    per_byte = elapsed / 4.0

    if not acked:
        return ProbeResult(
            EXIT_NACK,
            "Bus is idle-high (Y=0x%03x) but 0x%02x did not ACK: "
            "the lines are alive, yet no device answers at that address."
            % (y, glow.ADDR),
            y=y,
            per_byte_seconds=per_byte,
        )

    return ProbeResult(
        EXIT_OK,
        "Device ACKed at 0x%02x (Y=0x%03x, %.1f ms per byte)."
        % (glow.ADDR, y, per_byte * 1000.0),
        y=y,
        acked=True,
        per_byte_seconds=per_byte,
    )
