import unittest

from nitroglow import bitbang, glow, smbus
from tests.fakes import FakeBus, FakeMcu, FakePad


def _rig(registers=None, addr=0x28):
    mcu = FakeMcu(addr=addr, registers=registers)
    bus = FakeBus(mcu=mcu)
    sm = smbus.SMBus(bitbang.BitBang(FakePad(bus), delay=0))
    return mcu, glow.Glow(sm)


class TestGlow(unittest.TestCase):
    def test_set_color_writes_three_registers(self):
        mcu, g = _rig()
        g.set_color(0x11, 0x22, 0x33)
        self.assertEqual(mcu.registers[glow.REG_RED], 0x11)
        self.assertEqual(mcu.registers[glow.REG_GREEN], 0x22)
        self.assertEqual(mcu.registers[glow.REG_BLUE], 0x33)

    def test_get_color_round_trips(self):
        _, g = _rig()
        g.set_color(1, 2, 3)
        self.assertEqual(g.get_color(), (1, 2, 3))

    def test_set_mode_and_get_mode(self):
        _, g = _rig()
        g.set_mode(glow.MODES["custom"])
        self.assertEqual(g.get_mode(), 0x06)

    def test_off_uses_the_off_mode(self):
        mcu, g = _rig()
        g.off()
        self.assertEqual(mcu.registers[glow.REG_MODE], 0x07)

    def test_brightness_round_trips(self):
        _, g = _rig()
        g.set_brightness(0x50)
        self.assertEqual(g.get_brightness(), 0x50)

    def test_external_control_writes_boolean(self):
        mcu, g = _rig()
        g.set_external_control(True)
        self.assertEqual(mcu.registers[glow.REG_EXTERNAL_CONTROL], 1)
        g.set_external_control(False)
        self.assertEqual(mcu.registers[glow.REG_EXTERNAL_CONTROL], 0)

    def test_present_is_true_when_device_answers(self):
        _, g = _rig(registers={glow.REG_MODE: 0x06})
        self.assertTrue(g.present())

    def test_present_is_false_when_no_device(self):
        _, g = _rig(addr=0x29)
        self.assertFalse(g.present())

    def test_rejects_out_of_range_colour(self):
        _, g = _rig()
        with self.assertRaises(ValueError):
            g.set_color(256, 0, 0)

    def test_rejects_unknown_mode_value(self):
        _, g = _rig()
        with self.assertRaises(ValueError):
            g.set_mode(0x42)


if __name__ == "__main__":
    unittest.main()
