"""Open-drain i2c bit-banging over a Pad.

Lines are never driven high: "high" means released so the external pull-up
raises it. Every phase is bounded — a wedged bus must raise, never hang.
"""

import time

STRETCH_RETRIES = 100


class BitBangError(Exception):
    """The bus did not behave as required."""


class BitBang:
    def __init__(self, pad, delay=1e-5):
        self.pad = pad
        self.delay = delay

    def _wait(self):
        if self.delay:
            time.sleep(self.delay)

    def _scl_high(self):
        """Release SCL and wait for it to actually rise (clock stretching)."""
        self.pad.set_scl(True)
        for _ in range(STRETCH_RETRIES):
            if self.pad.read_scl():
                self._wait()
                return
            self._wait()
        raise BitBangError("SCL stayed low: clock stretched past the limit")

    def _scl_low(self):
        self.pad.set_scl(False)
        self._wait()

    def bus_idle(self):
        """True when both lines float high, i.e. pull-ups exist and nobody drives."""
        self.pad.set_scl(True)
        self.pad.set_sda(True)
        self._wait()
        return self.pad.read_scl() and self.pad.read_sda()

    def start(self):
        self.pad.set_sda(True)
        self._scl_high()
        if not self.pad.read_sda():
            raise BitBangError("SDA stayed low; cannot issue START")
        self.pad.set_sda(False)
        self._wait()
        self._scl_low()

    def restart(self):
        self.pad.set_sda(True)
        self._scl_high()
        self.pad.set_sda(False)
        self._wait()
        self._scl_low()

    def stop(self):
        self.pad.set_sda(False)
        self._scl_high()
        self.pad.set_sda(True)
        self._wait()

    def write_byte(self, value):
        """Clock out 8 bits MSB first; return True if the slave ACKed."""
        for i in range(8):
            self.pad.set_sda(bool((value >> (7 - i)) & 1))
            self._wait()
            self._scl_high()
            self._scl_low()
        self.pad.set_sda(True)          # release for the ACK bit
        self._wait()
        self._scl_high()
        acked = not self.pad.read_sda()
        self._scl_low()
        return acked

    def read_byte(self, ack):
        """Clock in 8 bits MSB first, then send ACK (ack=True) or NACK."""
        self.pad.set_sda(True)
        value = 0
        for _ in range(8):
            self._scl_high()
            value = (value << 1) | (1 if self.pad.read_sda() else 0)
            self._scl_low()
        self.pad.set_sda(not ack)
        self._wait()
        self._scl_high()
        self._scl_low()
        self.pad.set_sda(True)
        return value
