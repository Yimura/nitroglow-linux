import os
import struct
import tempfile
import unittest

from nitroglow import regs


class TestRegFile(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.write(fd, b"\x00" * 0x20000)
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def test_round_trip_allowed_offset(self):
        with regs.RegFile(self.path) as rf:
            rf.write32(regs.MASK, 0xCF401000)
            self.assertEqual(rf.read32(regs.MASK), 0xCF401000)

    def test_write_outside_allowlist_raises(self):
        with regs.RegFile(self.path) as rf:
            with self.assertRaises(regs.RegError):
                rf.write32(0x17000, 0x1)

    def test_read_outside_allowlist_raises(self):
        with regs.RegFile(self.path) as rf:
            with self.assertRaises(regs.RegError):
                rf.read32(0x17000)

    def test_dry_run_does_not_write(self):
        with regs.RegFile(self.path, dry_run=True) as rf:
            rf.write32(regs.A, 0xDEADBEEF)
        with open(self.path, "rb") as fh:
            fh.seek(regs.A)
            self.assertEqual(struct.unpack("<I", fh.read(4))[0], 0)

    def test_allowlist_contents(self):
        self.assertEqual(
            regs.ALLOWED_OFFSETS,
            frozenset({0x176A0, 0x176A4, 0x176A8, 0x176AC}),
        )


if __name__ == "__main__":
    unittest.main()
