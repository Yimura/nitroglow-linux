"""Test doubles: a register file, an open-drain bus, a pad, and an i2c slave."""

from nitroglow import regs


class FakeRegs:
    """Dict-backed stand-in for RegFile, enforcing the same allowlist."""

    def __init__(self, initial=None):
        self.values = {regs.MASK: 0, regs.A: 0, regs.EN: 0, regs.Y: 0}
        if initial:
            self.values.update(initial)
        self.writes = []

    def _check(self, offset):
        if offset not in regs.ALLOWED_OFFSETS:
            raise regs.RegError("offset 0x%x is not in the allowlist" % offset)

    def read32(self, offset):
        self._check(offset)
        return self.values[offset]

    def write32(self, offset, value):
        self._check(offset)
        self.values[offset] = value
        self.writes.append((offset, value))

    def close(self):
        pass


class FakeMcu:
    """A minimal i2c slave supporting SMBus write_byte_data / read_byte_data.

    Driven by bit-level callbacks from FakeBus so that the bit-bang layer is
    genuinely exercised, framing and all.
    """

    def __init__(self, addr=0x28, registers=None):
        self.addr = addr
        self.registers = dict(registers or {})
        self.sda_low = False
        self._reset()

    def _reset(self):
        # idle | addr | ack_addr | reg | ack_reg | data_w | ack_data
        #      | data_r | ack_master
        self.phase = "idle"
        self.shift = 0
        self.nbits = 0
        self.reading = False
        self.cur_reg = None
        self.out_byte = None
        self.out_index = 0

    def on_start(self):
        # Both a START and a repeated START land here. cur_reg deliberately
        # survives, because SMBus read_byte_data sets it before the restart.
        self.phase = "addr"
        self.shift = 0
        self.nbits = 0
        self.sda_low = False

    def on_stop(self):
        self._reset()

    def on_scl_rising(self, sda_high):
        """Master has clocked a bit in; sample it.

        Only sampled during data-carrying phases: the ACK pulses have their own
        phases so that an ACK bit is never mistaken for a data bit.
        """
        if self.phase in ("addr", "reg", "data_w"):
            self.shift = ((self.shift << 1) | (1 if sda_high else 0)) & 0xFF
            self.nbits += 1

    def _drive_next_read_bit(self):
        if self.out_index < 8:
            bit = (self.out_byte >> (7 - self.out_index)) & 1
            self.sda_low = (bit == 0)
            self.out_index += 1
        else:
            self.sda_low = False        # release for the master's ACK/NACK
            self.phase = "ack_master"

    def on_scl_falling(self):
        """Decide what to drive during the following low period."""
        phase = self.phase

        if phase == "addr":
            if self.nbits == 8:
                self.reading = bool(self.shift & 1)
                if (self.shift >> 1) != self.addr:
                    self._reset()           # not us: stay off the bus
                    return
                self.sda_low = True         # ACK
                self.phase = "ack_addr"
            else:
                self.sda_low = False
            return

        if phase == "ack_addr":
            self.nbits = 0
            self.shift = 0
            if self.reading:
                self.phase = "data_r"
                self.out_byte = self.registers.get(self.cur_reg, 0)
                self.out_index = 0
                self._drive_next_read_bit()
            else:
                self.phase = "reg"
                self.sda_low = False
            return

        if phase == "reg":
            if self.nbits == 8:
                self.cur_reg = self.shift
                self.sda_low = True         # ACK
                self.phase = "ack_reg"
            else:
                self.sda_low = False
            return

        if phase == "ack_reg":
            self.nbits = 0
            self.shift = 0
            self.phase = "data_w"
            self.sda_low = False
            return

        if phase == "data_w":
            if self.nbits == 8:
                self.registers[self.cur_reg] = self.shift
                self.sda_low = True         # ACK
                self.phase = "ack_data"
            else:
                self.sda_low = False
            return

        if phase == "ack_data":
            self.nbits = 0
            self.shift = 0
            self.phase = "data_w"           # further bytes may follow
            self.sda_low = False
            return

        if phase == "data_r":
            self._drive_next_read_bit()
            return

        self.sda_low = False


class FakeBus:
    """Open-drain electrical model: the line is high only if nobody pulls low."""

    def __init__(self, mcu=None, pullups=True):
        self.mcu = mcu
        self.pullups = pullups
        self.master_scl_low = False
        self.master_sda_low = False
        self._scl_was_high = True

    @property
    def scl(self):
        return (not self.master_scl_low) and self.pullups

    @property
    def sda(self):
        pulled = self.master_sda_low or (self.mcu.sda_low if self.mcu else False)
        return (not pulled) and self.pullups

    def set_master_scl(self, high):
        was = self._scl_was_high
        self.master_scl_low = not high
        now = self.scl
        if self.mcu:
            if now and not was:
                self.mcu.on_scl_rising(self.sda)
            elif was and not now:
                self.mcu.on_scl_falling()
        self._scl_was_high = now

    def set_master_sda(self, high):
        before = self.sda
        self.master_sda_low = not high
        after = self.sda
        if self.mcu and self.scl and before != after:
            if before and not after:
                self.mcu.on_start()
            else:
                self.mcu.on_stop()


class FakePad:
    """Pad-shaped adapter over FakeBus, for testing bitbang without hardware."""

    def __init__(self, bus):
        self.bus = bus

    def set_scl(self, high):
        self.bus.set_master_scl(high)

    def set_sda(self, high):
        self.bus.set_master_sda(high)

    def read_scl(self):
        return self.bus.scl

    def read_sda(self):
        return self.bus.sda

    def read_y(self):
        from nitroglow import pad as _pad
        value = 0
        if self.bus.scl:
            value |= _pad.CLK_BIT
        if self.bus.sda:
            value |= _pad.DATA_BIT
        return value
