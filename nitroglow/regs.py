"""Allowlisted 32-bit register access via amdgpu's debugfs register file.

amdgpu_regs is addressed by byte offset, where byte offset == dword register
index << 2. Only the four DC_GPIO_DDCVGA registers may be touched; every other
offset is refused, which is what bounds the blast radius of this tool.
"""

import os
import struct

DEBUGFS_REGS = "/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs"

# DC_GPIO_DDCVGA_{MASK,A,EN,Y}: dwords 0x5DA8..0x5DAB, byte offsets << 2.
MASK = 0x176A0
A = 0x176A4
EN = 0x176A8
Y = 0x176AC

ALLOWED_OFFSETS = frozenset({MASK, A, EN, Y})


class RegError(Exception):
    """A register access was refused or failed."""


class RegFile:
    def __init__(self, path=DEBUGFS_REGS, dry_run=False):
        self.path = path
        self.dry_run = dry_run
        try:
            self._fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            raise RegError("cannot open %s: %s" % (path, exc)) from exc

    def _check(self, offset):
        if offset not in ALLOWED_OFFSETS:
            raise RegError("offset 0x%x is not in the allowlist" % offset)

    def read32(self, offset):
        self._check(offset)
        try:
            data = os.pread(self._fd, 4, offset)
        except OSError as exc:
            raise RegError("read at 0x%x failed: %s" % (offset, exc)) from exc
        if len(data) != 4:
            raise RegError("short read at 0x%x" % offset)
        return struct.unpack("<I", data)[0]

    def write32(self, offset, value):
        self._check(offset)
        if self.dry_run:
            print("dry-run: write 0x%08x -> 0x%05x" % (value, offset))
            return
        try:
            written = os.pwrite(self._fd, struct.pack("<I", value), offset)
        except OSError as exc:
            raise RegError("write at 0x%x failed: %s" % (offset, exc)) from exc
        if written != 4:
            raise RegError("short write at 0x%x" % offset)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False
