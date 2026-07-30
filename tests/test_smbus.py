import unittest

from nitroglow import bitbang, smbus
from tests.fakes import FakeBus, FakeMcu, FakePad


def _rig(registers=None, addr=0x28):
    mcu = FakeMcu(addr=addr, registers=registers)
    bus = FakeBus(mcu=mcu)
    return mcu, smbus.SMBus(bitbang.BitBang(FakePad(bus), delay=0))


class TestSMBus(unittest.TestCase):
    def test_write_byte_data_stores_value(self):
        mcu, sm = _rig()
        self.assertTrue(sm.write_byte_data(0x28, 0x1A, 0xAB))
        self.assertEqual(mcu.registers[0x1A], 0xAB)

    def test_read_byte_data_returns_value(self):
        _, sm = _rig(registers={0x3E: 0x64})
        self.assertEqual(sm.read_byte_data(0x28, 0x3E), 0x64)

    def test_write_to_absent_device_returns_false(self):
        _, sm = _rig(addr=0x29)
        self.assertFalse(sm.write_byte_data(0x28, 0x1A, 0x01))

    def test_read_from_absent_device_returns_none(self):
        _, sm = _rig(addr=0x29)
        self.assertIsNone(sm.read_byte_data(0x28, 0x1A))

    def test_round_trip_through_both_operations(self):
        _, sm = _rig()
        sm.write_byte_data(0x28, 0x1C, 0x21)
        self.assertEqual(sm.read_byte_data(0x28, 0x1C), 0x21)

    def test_rejects_out_of_range_values(self):
        _, sm = _rig()
        with self.assertRaises(ValueError):
            sm.write_byte_data(0x28, 0x1A, 0x100)
        with self.assertRaises(ValueError):
            sm.write_byte_data(0x80, 0x1A, 0x01)


if __name__ == "__main__":
    unittest.main()
