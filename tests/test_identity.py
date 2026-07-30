import os
import tempfile
import unittest

from nitroglow import identity


def _make_sysfs(tmp, vendor="0x1002", device="0x731f",
                subsystem_vendor="0x1da2", subsystem_device="0xe409",
                revision="0xc1"):
    for name, value in [
        ("vendor", vendor), ("device", device),
        ("subsystem_vendor", subsystem_vendor),
        ("subsystem_device", subsystem_device),
        ("revision", revision),
    ]:
        with open(os.path.join(tmp, name), "w") as fh:
            fh.write(value + "\n")
    return tmp


class TestIdentity(unittest.TestCase):
    def test_accepts_the_expected_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity.check_identity(_make_sysfs(tmp))

    def test_rejects_wrong_subsystem_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sysfs(tmp, subsystem_device="0xe410")
            with self.assertRaises(identity.IdentityError):
                identity.check_identity(tmp)

    def test_rejects_wrong_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sysfs(tmp, revision="0xc4")
            with self.assertRaises(identity.IdentityError):
                identity.check_identity(tmp)

    def test_rejects_missing_sysfs(self):
        with self.assertRaises(identity.IdentityError):
            identity.check_identity("/nonexistent/path")


if __name__ == "__main__":
    unittest.main()
