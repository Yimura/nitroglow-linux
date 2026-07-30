import unittest

from nitroglow import bitbang
from tests.fakes import FakeBus, FakeMcu, FakePad


def _rig(registers=None, pullups=True, addr=0x28):
    mcu = FakeMcu(addr=addr, registers=registers)
    bus = FakeBus(mcu=mcu, pullups=pullups)
    return mcu, bus, bitbang.BitBang(FakePad(bus), delay=0)


class TestBitBang(unittest.TestCase):
    def test_bus_idle_when_pullups_present(self):
        _, _, bb = _rig()
        self.assertTrue(bb.bus_idle())

    def test_bus_not_idle_without_pullups(self):
        _, _, bb = _rig(pullups=False)
        self.assertFalse(bb.bus_idle())

    def test_address_write_is_acked(self):
        _, _, bb = _rig()
        bb.start()
        self.assertTrue(bb.write_byte(0x28 << 1))
        bb.stop()

    def test_wrong_address_is_not_acked(self):
        _, _, bb = _rig(addr=0x29)
        bb.start()
        self.assertFalse(bb.write_byte(0x28 << 1))
        bb.stop()

    def test_write_byte_data_sequence_lands_in_registers(self):
        mcu, _, bb = _rig()
        bb.start()
        self.assertTrue(bb.write_byte(0x28 << 1))
        self.assertTrue(bb.write_byte(0x1A))
        self.assertTrue(bb.write_byte(0x7F))
        bb.stop()
        self.assertEqual(mcu.registers[0x1A], 0x7F)

    def test_read_byte_returns_register_contents(self):
        _, _, bb = _rig(registers={0x1B: 0x42})
        bb.start()
        self.assertTrue(bb.write_byte(0x28 << 1))
        self.assertTrue(bb.write_byte(0x1B))
        bb.restart()
        self.assertTrue(bb.write_byte((0x28 << 1) | 1))
        self.assertEqual(bb.read_byte(ack=False), 0x42)
        bb.stop()

    def test_start_raises_when_sda_stuck_low(self):
        _, bus, bb = _rig()
        bus.pullups = False          # nothing can pull the line high
        with self.assertRaises(bitbang.BitBangError):
            bb.start()


if __name__ == "__main__":
    unittest.main()
