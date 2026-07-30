"""PCI identity guard.

The register offsets this tool writes are specific to one ASIC and one board.
Running against anything else would poke unrelated hardware, so every path that
touches registers checks identity first.
"""

import os

DEFAULT_SYSFS = "/sys/bus/pci/devices/0000:0e:00.0"

EXPECTED = {
    "vendor": 0x1002,
    "device": 0x731F,
    "subsystem_vendor": 0x1DA2,
    "subsystem_device": 0xE409,
    "revision": 0xC1,
}


class IdentityError(Exception):
    """The PCI device is not the card this tool supports."""


def _read_hex(sysfs_root, name):
    path = os.path.join(sysfs_root, name)
    try:
        with open(path) as fh:
            return int(fh.read().strip(), 16)
    except (OSError, ValueError) as exc:
        raise IdentityError("cannot read %s: %s" % (path, exc)) from exc


def check_identity(sysfs_root=DEFAULT_SYSFS):
    """Raise IdentityError unless sysfs_root is the supported card."""
    for name, want in EXPECTED.items():
        got = _read_hex(sysfs_root, name)
        if got != want:
            raise IdentityError(
                "%s: expected 0x%x, got 0x%x" % (name, want, got)
            )
