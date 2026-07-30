"""DC GPIO pad control for the DC_GPIO_DDCVGA pin pair.

Conventions mirror DC's own gpio code:
  MASK bit set  -> pad is claimed by software
  A             -> value driven onto the pad (kept 0; open-drain)
  EN            -> drive enable, written INVERTED relative to the line level:
                   EN bit set   => actively pulling the line LOW
                   EN bit clear => released, external pull-up takes it HIGH
  Y             -> reads the line back, but only meaningfully once claimed

Verified against ref/linux-7.1.5 rather than assumed, because the kernel's own
comment is misleading. hw_gpio.c:109-114 reads:

    case GPIO_MODE_FAST_OUTPUT:
        /* ... output is driven by (EN = 0) to pull the line down (output == 0)
         * and (EN=1) then output is tri-state */
        REG_UPDATE(EN_reg, EN, ~value);

Taken literally that comment says EN=0 pulls the line low, which is the
opposite of what the code does. The call chain settles it: dce_i2c_sw.c:53-64
write_bit_to_ddc(SCL, true) -> dal_gpio_set_value(pin, 1) -> EN = ~1 = 0, and
write_byte_sw (dce_i2c_sw.c:115-117) then waits for SCL to read *high*. So
writing true releases the line: EN clear = released high, EN set = driven low.
The comment describes the pre-inversion signal, not the register field.

MASK bit 12 (DATA_PD_EN) is the internal SDA pull-down that DC asserts on every
open of this line and never clears; clearing it is the point of this tool.
"""

from nitroglow import regs

CLK_BIT = 1 << 0
DATA_BIT = 1 << 8
DATA_PD_EN = 1 << 12


class PadError(Exception):
    """Pad state was used incorrectly."""


class Pad:
    def __init__(self, regfile):
        self.regs = regfile
        self._saved = None

    def save(self):
        self._saved = {
            off: self.regs.read32(off)
            for off in (regs.MASK, regs.A, regs.EN, regs.Y)
        }

    def restore(self):
        if self._saved is None:
            raise PadError("restore() called before save()")
        # Y is read-only in practice; restoring the three writable ones is enough.
        for off in (regs.EN, regs.A, regs.MASK):
            self.regs.write32(off, self._saved[off])

    def claim(self):
        """Take both pads for software use and clear the stuck pull-down."""
        mask = self.regs.read32(regs.MASK)
        mask &= ~DATA_PD_EN
        mask |= CLK_BIT | DATA_BIT
        self.regs.write32(regs.MASK, mask)
        # Drive nothing: A low, EN cleared => both lines released.
        self.regs.write32(regs.A, 0)
        self.regs.write32(regs.EN, 0)

    def release(self):
        """Release both lines and hand the pads back."""
        self.regs.write32(regs.EN, 0)
        mask = self.regs.read32(regs.MASK)
        mask &= ~(CLK_BIT | DATA_BIT)
        self.regs.write32(regs.MASK, mask)

    def _set_line(self, bit, high):
        en = self.regs.read32(regs.EN)
        if high:
            en &= ~bit          # release; pull-up takes it high
        else:
            en |= bit           # drive low
        self.regs.write32(regs.EN, en)

    def set_scl(self, high):
        self._set_line(CLK_BIT, high)

    def set_sda(self, high):
        self._set_line(DATA_BIT, high)

    def read_y(self):
        return self.regs.read32(regs.Y)

    def read_scl(self):
        return bool(self.read_y() & CLK_BIT)

    def read_sda(self):
        return bool(self.read_y() & DATA_BIT)
