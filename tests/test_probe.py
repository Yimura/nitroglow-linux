import unittest

from nitroglow import pad, probe
from tests.fakes import FakeBus, FakeMcu, FakePad


class TestProbe(unittest.TestCase):
    def test_device_present_gives_exit_ok(self):
        bus = FakeBus(mcu=FakeMcu(addr=0x28))
        result = probe.run_probe(FakePad(bus), delay=0)
        self.assertEqual(result.code, probe.EXIT_OK)
        self.assertTrue(result.acked)

    def test_lines_held_low_gives_exit_lines_low(self):
        bus = FakeBus(mcu=FakeMcu(addr=0x28), pullups=False)
        result = probe.run_probe(FakePad(bus), delay=0)
        self.assertEqual(result.code, probe.EXIT_LINES_LOW)
        self.assertFalse(result.acked)

    def test_idle_bus_but_no_device_gives_exit_nack(self):
        bus = FakeBus(mcu=FakeMcu(addr=0x29))
        result = probe.run_probe(FakePad(bus), delay=0)
        self.assertEqual(result.code, probe.EXIT_NACK)
        self.assertFalse(result.acked)

    def test_reports_y_value_when_bus_is_idle(self):
        bus = FakeBus(mcu=FakeMcu(addr=0x28))
        result = probe.run_probe(FakePad(bus), delay=0)
        self.assertEqual(result.y, pad.CLK_BIT | pad.DATA_BIT)

    def test_measures_per_byte_time_when_device_present(self):
        bus = FakeBus(mcu=FakeMcu(addr=0x28))
        result = probe.run_probe(FakePad(bus), delay=0)
        self.assertIsNotNone(result.per_byte_seconds)
        self.assertGreaterEqual(result.per_byte_seconds, 0.0)

    def test_message_is_populated_for_every_outcome(self):
        for pullups, addr, expect in [
            (True, 0x28, probe.EXIT_OK),
            (False, 0x28, probe.EXIT_LINES_LOW),
            (True, 0x29, probe.EXIT_NACK),
        ]:
            bus = FakeBus(mcu=FakeMcu(addr=addr), pullups=pullups)
            result = probe.run_probe(FakePad(bus), delay=0)
            self.assertEqual(result.code, expect)
            self.assertTrue(result.message)


if __name__ == "__main__":
    unittest.main()
