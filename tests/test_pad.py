import unittest

from nitroglow import pad, regs
from tests.fakes import FakeRegs


class TestPad(unittest.TestCase):
    def setUp(self):
        # Mirror the real observed power-on state: pull-down stuck on.
        self.regs = FakeRegs({regs.MASK: 0xCF401000})
        self.pad = pad.Pad(self.regs)

    def test_claim_clears_the_stuck_pulldown(self):
        self.pad.claim()
        self.assertEqual(self.regs.values[regs.MASK] & pad.DATA_PD_EN, 0)

    def test_claim_sets_both_pad_claim_bits(self):
        self.pad.claim()
        mask = self.regs.values[regs.MASK]
        self.assertEqual(mask & pad.CLK_BIT, pad.CLK_BIT)
        self.assertEqual(mask & pad.DATA_BIT, pad.DATA_BIT)

    def test_driving_low_sets_en_bit(self):
        self.pad.claim()
        self.pad.set_sda(False)
        self.assertEqual(self.regs.values[regs.EN] & pad.DATA_BIT, pad.DATA_BIT)

    def test_releasing_clears_en_bit(self):
        self.pad.claim()
        self.pad.set_sda(False)
        self.pad.set_sda(True)
        self.assertEqual(self.regs.values[regs.EN] & pad.DATA_BIT, 0)

    def test_scl_and_sda_are_independent(self):
        self.pad.claim()
        self.pad.set_scl(False)
        self.pad.set_sda(True)
        en = self.regs.values[regs.EN]
        self.assertEqual(en & pad.CLK_BIT, pad.CLK_BIT)
        self.assertEqual(en & pad.DATA_BIT, 0)

    def test_read_sda_reflects_y_register(self):
        self.regs.values[regs.Y] = pad.DATA_BIT
        self.assertTrue(self.pad.read_sda())
        self.regs.values[regs.Y] = 0
        self.assertFalse(self.pad.read_sda())

    def test_save_restore_round_trip(self):
        self.pad.save()
        self.pad.claim()
        self.pad.set_sda(False)
        self.pad.restore()
        self.assertEqual(self.regs.values[regs.MASK], 0xCF401000)
        self.assertEqual(self.regs.values[regs.EN], 0)

    def test_restore_without_save_raises(self):
        with self.assertRaises(pad.PadError):
            self.pad.restore()


if __name__ == "__main__":
    unittest.main()
