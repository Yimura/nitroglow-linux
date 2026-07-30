# Nitro Glow Userspace i2c Control — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set the Sapphire RX 5700 XT Nitro+ RGB lighting from Linux by bit-banging i2c over the GPU's `DC_GPIO_DDCVGA` pads through amdgpu's debugfs register interface, with no kernel module.

**Architecture:** Six layers, bottom two hardware-specific and quarantined. `regs` does `pread`/`pwrite` against `amdgpu_regs` behind a four-offset allowlist. `pad` encodes DC's pad conventions (claim, drive, read back, clear the stuck pull-down). `bitbang` is ordinary open-drain i2c. `smbus` adds `write_byte_data`/`read_byte_data`. `glow` is the Nitro Glow V3 register map. `cli` wires it together. Everything above `pad` is tested against a software open-drain bus and a fake i2c slave, so only the final task needs hardware.

**Tech Stack:** Python 3 (stdlib only — `os`, `fcntl`, `struct`, `argparse`, `configparser`, `unittest`). No third-party packages, no build step.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3 stdlib only.** No pip installs, no venv, no third-party test runner. Tests use `unittest`, run via `python3 -m unittest`.
- **No git in this project** — user decision, local work only. Every task therefore ends with a full-suite run as its checkpoint instead of a commit. Do not run `git init`.
- **Nothing installed on the host.** No system packages, no global tools.
- **Register allowlist is absolute:** only byte offsets `0x176A0`, `0x176A4`, `0x176A8`, `0x176AC` may ever be written. Every write asserts membership.
- **Identity guard:** PCI `0000:0e:00.0` must report vendor `0x1002`, device `0x731f`, subsystem vendor `0x1da2`, subsystem device `0xe409`, revision `0xc1`. Refuse to write otherwise.
- **Restore on every exit path** — normal, exception, `SIGINT`/`SIGTERM`/`SIGHUP`.
- **Never run `i2cdetect`.** Single-address operations only.
- **Never run OpenRGB or `i2cget` against `i2c-13` while this tool holds the pads.**
- Hardware steps require root. Passwordless `sudo` is available on this host.
- Project root: `<repo>`. All paths below are relative to it unless absolute.

## File Structure

| file | responsibility |
|---|---|
| `nitroglow/__init__.py` | package marker, version string |
| `nitroglow/identity.py` | PCI identity guard — reads sysfs, raises on mismatch |
| `nitroglow/regs.py` | `RegFile`: allowlisted 32-bit read/write against `amdgpu_regs` |
| `nitroglow/pad.py` | `Pad`: claim/release, drive and read SCL/SDA, clear pull-down, save/restore |
| `nitroglow/bitbang.py` | `BitBang`: START, STOP, repeated START, byte write with ACK, byte read with ACK/NACK |
| `nitroglow/smbus.py` | `SMBus`: `write_byte_data`, `read_byte_data` |
| `nitroglow/glow.py` | `Glow`: V3 register map, modes, colour/brightness/mode accessors |
| `nitroglow/probe.py` | probe sequence and exit-code classification |
| `nitroglow/cli.py` | argparse CLI, advisory lock, config file, signal handling |
| `bin/nitroglow` | executable entry point |
| `tests/fakes.py` | `FakeRegs`, `FakeBus`, `FakePad`, `FakeMcu` |
| `tests/test_identity.py` | identity guard tests |
| `tests/test_regs.py` | allowlist and read/write tests |
| `tests/test_pad.py` | pad semantics against `FakeRegs` |
| `tests/test_bitbang.py` | framing, ACK/NACK, stuck-bus detection |
| `tests/test_smbus.py` | SMBus transaction shape |
| `tests/test_glow.py` | Glow register map |
| `tests/test_probe.py` | probe classification and exit codes |
| `nitroglow.conf.example` | sample config |
| `systemd/nitroglow.service` | boot-time oneshot unit |

---

### Task 1: Identity guard and register file

**Files:**
- Create: `nitroglow/__init__.py`
- Create: `nitroglow/identity.py`
- Create: `nitroglow/regs.py`
- Test: `tests/test_identity.py`, `tests/test_regs.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `identity.check_identity(sysfs_root: str = "/sys/bus/pci/devices/0000:0e:00.0") -> None`, raises `identity.IdentityError`
  - `regs.RegFile(path: str = regs.DEBUGFS_REGS, dry_run: bool = False)` with `.read32(off: int) -> int`, `.write32(off: int, value: int) -> None`, `.close() -> None`, context-manager support
  - `regs.RegError`, `regs.ALLOWED_OFFSETS: frozenset[int]`, `regs.MASK/A/EN/Y: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity.py`:

```python
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
```

Create `tests/test_regs.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_identity tests.test_regs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nitroglow'`

- [ ] **Step 3: Write the implementation**

Create `nitroglow/__init__.py`:

```python
"""Userspace Nitro Glow RGB control for the Sapphire RX 5700 XT Nitro+."""

__version__ = "0.1.0"
```

Create `nitroglow/identity.py`:

```python
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
```

Create `nitroglow/regs.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_identity tests.test_regs -v`
Expected: PASS — 9 tests OK

- [ ] **Step 5: Checkpoint**

Run the full suite: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass. (No commit — this project is deliberately not under git.)

---

### Task 2: Pad layer and its fake

**Files:**
- Create: `nitroglow/pad.py`
- Create: `tests/fakes.py` (the `FakeRegs` part; other fakes are added in Task 3)
- Test: `tests/test_pad.py`

**Interfaces:**
- Consumes: `regs.RegFile` (`.read32`, `.write32`), `regs.MASK/A/EN/Y`.
- Produces:
  - `pad.Pad(regfile)` with `.save() -> None`, `.restore() -> None`, `.claim() -> None`, `.release() -> None`, `.set_scl(high: bool) -> None`, `.set_sda(high: bool) -> None`, `.read_scl() -> bool`, `.read_sda() -> bool`, `.read_y() -> int`
  - `pad.CLK_BIT`, `pad.DATA_BIT`, `pad.DATA_PD_EN`
  - `fakes.FakeRegs()` with the same read/write interface as `RegFile`

- [ ] **Step 1: Verify the EN polarity against the kernel source**

This is the one pad convention taken from a single reading, and getting it
backwards would drive the bus wrong while looking correct. Confirm before writing code.

Run: `sed -n '100,120p' <repo>/<linux>/drivers/gpu/drm/amd/display/dc/gpio/hw_gpio.c`
Expected: a `REG_UPDATE(EN_reg, EN, ~value)`-shaped write, confirming `EN` is written inverted — i.e. releasing a line (letting it float high) clears the `EN` bit, and driving it low sets the `EN` bit.

Record what you actually saw in a comment at the top of `nitroglow/pad.py`. If the source contradicts the assumption above, stop and report rather than proceeding.

- [ ] **Step 2: Write the failing tests**

Create `tests/fakes.py`:

```python
"""Test doubles: a register file, an open-drain bus, a pad, and an i2c slave."""

from nitroglow import regs


class FakeRegs:
    """Dict-backed stand-in for RegFile, enforcing the same allowlist."""

    def __init__(self, initial=None):
        self.values = {regs.MASK: 0, regs.A: 0, regs.EN: 0, regs.Y: 0}
        if initial:
            self.values.update(initial)
        self.writes = []

    def _check(self, offset):
        if offset not in regs.ALLOWED_OFFSETS:
            raise regs.RegError("offset 0x%x is not in the allowlist" % offset)

    def read32(self, offset):
        self._check(offset)
        return self.values[offset]

    def write32(self, offset, value):
        self._check(offset)
        self.values[offset] = value
        self.writes.append((offset, value))

    def close(self):
        pass
```

Create `tests/test_pad.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_pad -v`
Expected: FAIL — `ImportError: cannot import name 'pad'`

- [ ] **Step 4: Write the implementation**

Create `nitroglow/pad.py`:

```python
"""DC GPIO pad control for the DC_GPIO_DDCVGA pin pair.

Conventions mirror DC's own gpio code:
  MASK bit set  -> pad is claimed by software
  A             -> value driven onto the pad (kept 0; open-drain)
  EN            -> drive enable, written INVERTED relative to the line level:
                   EN bit set   => actively pulling the line LOW
                   EN bit clear => released, external pull-up takes it HIGH
  Y             -> reads the line back, but only meaningfully once claimed

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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_pad -v`
Expected: PASS — 8 tests OK

- [ ] **Step 6: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Task 1 + Task 2).

---

### Task 3: Bit-bang layer, open-drain bus model, and fake slave

**Files:**
- Modify: `tests/fakes.py` (append `FakeBus`, `FakePad`, `FakeMcu`)
- Create: `nitroglow/bitbang.py`
- Test: `tests/test_bitbang.py`

**Interfaces:**
- Consumes: `pad.Pad` interface (`.set_scl`, `.set_sda`, `.read_scl`, `.read_sda`, `.read_y`), `pad.CLK_BIT`, `pad.DATA_BIT`.
- Produces:
  - `bitbang.BitBang(pad, delay: float = 1e-5)` with `.bus_idle() -> bool`, `.start() -> None`, `.restart() -> None`, `.stop() -> None`, `.write_byte(value: int) -> bool` (True when the slave ACKed), `.read_byte(ack: bool) -> int`
  - `bitbang.BitBangError`
  - `fakes.FakeMcu(addr: int = 0x28, registers: dict | None = None)`, `fakes.FakeBus(mcu=None, pullups: bool = True)`, `fakes.FakePad(bus)`

- [ ] **Step 1: Write the fakes**

Append to `tests/fakes.py`:

```python
class FakeMcu:
    """A minimal i2c slave supporting SMBus write_byte_data / read_byte_data.

    Driven by bit-level callbacks from FakeBus so that the bit-bang layer is
    genuinely exercised, framing and all.
    """

    def __init__(self, addr=0x28, registers=None):
        self.addr = addr
        self.registers = dict(registers or {})
        self.sda_low = False
        self._reset()

    def _reset(self):
        self.phase = "idle"      # idle | addr | reg | data_w | data_r
        self.shift = 0
        self.nbits = 0
        self.reading = False
        self.cur_reg = None
        self.out_byte = None
        self.out_index = 0

    def on_start(self):
        # Both a START and a repeated START land here.
        self.phase = "addr"
        self.shift = 0
        self.nbits = 0
        self.sda_low = False

    def on_stop(self):
        self._reset()

    def on_scl_rising(self, sda_high):
        """Master has clocked a bit in; sample it."""
        if self.phase in ("addr", "reg", "data_w"):
            self.shift = ((self.shift << 1) | (1 if sda_high else 0)) & 0xFF
            self.nbits += 1

    def on_scl_falling(self):
        """Decide what to drive during the following low period."""
        if self.phase == "addr" and self.nbits == 8:
            self.reading = bool(self.shift & 1)
            if (self.shift >> 1) != self.addr:
                self._reset()               # not us: stay off the bus
                return
            self.sda_low = True             # ACK
            self.nbits = 0
            self.shift = 0
            if self.reading:
                self.phase = "data_r"
                self.out_byte = self.registers.get(self.cur_reg, 0)
                self.out_index = 0
            else:
                self.phase = "reg"
            return

        if self.phase == "reg" and self.nbits == 8:
            self.cur_reg = self.shift
            self.sda_low = True             # ACK
            self.nbits = 0
            self.shift = 0
            self.phase = "data_w"
            return

        if self.phase == "data_w" and self.nbits == 8:
            self.registers[self.cur_reg] = self.shift
            self.sda_low = True             # ACK
            self.nbits = 0
            self.shift = 0
            return

        if self.phase == "data_r":
            if self.out_index < 8:
                bit = (self.out_byte >> (7 - self.out_index)) & 1
                self.sda_low = (bit == 0)
                self.out_index += 1
                return
            self.sda_low = False            # release for the master's ACK/NACK
            return

        self.sda_low = False                # release after any ACK bit


class FakeBus:
    """Open-drain electrical model: the line is high only if nobody pulls low."""

    def __init__(self, mcu=None, pullups=True):
        self.mcu = mcu
        self.pullups = pullups
        self.master_scl_low = False
        self.master_sda_low = False
        self._scl_was_high = True

    @property
    def scl(self):
        return (not self.master_scl_low) and self.pullups

    @property
    def sda(self):
        pulled = self.master_sda_low or (self.mcu.sda_low if self.mcu else False)
        return (not pulled) and self.pullups

    def set_master_scl(self, high):
        was = self._scl_was_high
        self.master_scl_low = not high
        now = self.scl
        if self.mcu:
            if now and not was:
                self.mcu.on_scl_rising(self.sda)
            elif was and not now:
                self.mcu.on_scl_falling()
        self._scl_was_high = now

    def set_master_sda(self, high):
        before = self.sda
        self.master_sda_low = not high
        after = self.sda
        if self.mcu and self.scl and before != after:
            if before and not after:
                self.mcu.on_start()
            else:
                self.mcu.on_stop()


class FakePad:
    """Pad-shaped adapter over FakeBus, for testing bitbang without hardware."""

    def __init__(self, bus):
        self.bus = bus

    def set_scl(self, high):
        self.bus.set_master_scl(high)

    def set_sda(self, high):
        self.bus.set_master_sda(high)

    def read_scl(self):
        return self.bus.scl

    def read_sda(self):
        return self.bus.sda

    def read_y(self):
        from nitroglow import pad as _pad
        value = 0
        if self.bus.scl:
            value |= _pad.CLK_BIT
        if self.bus.sda:
            value |= _pad.DATA_BIT
        return value
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_bitbang.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_bitbang -v`
Expected: FAIL — `ImportError: cannot import name 'bitbang'`

- [ ] **Step 4: Write the implementation**

Create `nitroglow/bitbang.py`:

```python
"""Open-drain i2c bit-banging over a Pad.

Lines are never driven high: "high" means released so the external pull-up
raises it. Every phase is bounded — a wedged bus must raise, never hang.
"""

import time

STRETCH_RETRIES = 100


class BitBangError(Exception):
    """The bus did not behave as required."""


class BitBang:
    def __init__(self, pad, delay=1e-5):
        self.pad = pad
        self.delay = delay

    def _wait(self):
        if self.delay:
            time.sleep(self.delay)

    def _scl_high(self):
        """Release SCL and wait for it to actually rise (clock stretching)."""
        self.pad.set_scl(True)
        for _ in range(STRETCH_RETRIES):
            if self.pad.read_scl():
                self._wait()
                return
            self._wait()
        raise BitBangError("SCL stayed low: clock stretched past the limit")

    def _scl_low(self):
        self.pad.set_scl(False)
        self._wait()

    def bus_idle(self):
        """True when both lines float high, i.e. pull-ups exist and nobody drives."""
        self.pad.set_scl(True)
        self.pad.set_sda(True)
        self._wait()
        return self.pad.read_scl() and self.pad.read_sda()

    def start(self):
        self.pad.set_sda(True)
        self._scl_high()
        if not self.pad.read_sda():
            raise BitBangError("SDA stayed low; cannot issue START")
        self.pad.set_sda(False)
        self._wait()
        self._scl_low()

    def restart(self):
        self.pad.set_sda(True)
        self._scl_high()
        self.pad.set_sda(False)
        self._wait()
        self._scl_low()

    def stop(self):
        self.pad.set_sda(False)
        self._scl_high()
        self.pad.set_sda(True)
        self._wait()

    def write_byte(self, value):
        """Clock out 8 bits MSB first; return True if the slave ACKed."""
        for i in range(8):
            self.pad.set_sda(bool((value >> (7 - i)) & 1))
            self._wait()
            self._scl_high()
            self._scl_low()
        self.pad.set_sda(True)          # release for the ACK bit
        self._wait()
        self._scl_high()
        acked = not self.pad.read_sda()
        self._scl_low()
        return acked

    def read_byte(self, ack):
        """Clock in 8 bits MSB first, then send ACK (ack=True) or NACK."""
        self.pad.set_sda(True)
        value = 0
        for _ in range(8):
            self._scl_high()
            value = (value << 1) | (1 if self.pad.read_sda() else 0)
            self._scl_low()
        self.pad.set_sda(not ack)
        self._wait()
        self._scl_high()
        self._scl_low()
        self.pad.set_sda(True)
        return value
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_bitbang -v`
Expected: PASS — 7 tests OK

- [ ] **Step 6: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Tasks 1-3).

---

### Task 4: SMBus layer

**Files:**
- Create: `nitroglow/smbus.py`
- Test: `tests/test_smbus.py`

**Interfaces:**
- Consumes: `bitbang.BitBang` (`.start`, `.restart`, `.stop`, `.write_byte`, `.read_byte`), `bitbang.BitBangError`.
- Produces: `smbus.SMBus(bitbang)` with `.write_byte_data(addr: int, reg: int, value: int) -> bool` and `.read_byte_data(addr: int, reg: int) -> int | None` (None when the slave did not ACK); `smbus.SMBusError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_smbus.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_smbus -v`
Expected: FAIL — `ImportError: cannot import name 'smbus'`

- [ ] **Step 3: Write the implementation**

Create `nitroglow/smbus.py`:

```python
"""SMBus byte-data transactions on top of the bit-bang layer.

The Nitro Glow V3 protocol is exactly these two operations, matching
OpenRGB's SapphireNitroGlowV3Controller.
"""


class SMBusError(Exception):
    """A transaction failed in a way the caller cannot act on."""


def _check(addr, reg, value=None):
    if not 0 <= addr <= 0x7F:
        raise ValueError("address 0x%x out of 7-bit range" % addr)
    if not 0 <= reg <= 0xFF:
        raise ValueError("register 0x%x out of range" % reg)
    if value is not None and not 0 <= value <= 0xFF:
        raise ValueError("value 0x%x out of range" % value)


class SMBus:
    def __init__(self, bb):
        self.bb = bb

    def write_byte_data(self, addr, reg, value):
        """START, addr+W, reg, value, STOP. False if any phase was NACKed."""
        _check(addr, reg, value)
        self.bb.start()
        try:
            if not self.bb.write_byte(addr << 1):
                return False
            if not self.bb.write_byte(reg):
                return False
            return self.bb.write_byte(value)
        finally:
            self.bb.stop()

    def read_byte_data(self, addr, reg):
        """START, addr+W, reg, RESTART, addr+R, byte, NACK, STOP."""
        _check(addr, reg)
        self.bb.start()
        try:
            if not self.bb.write_byte(addr << 1):
                return None
            if not self.bb.write_byte(reg):
                return None
            self.bb.restart()
            if not self.bb.write_byte((addr << 1) | 1):
                return None
            return self.bb.read_byte(ack=False)
        finally:
            self.bb.stop()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_smbus -v`
Expected: PASS — 6 tests OK

- [ ] **Step 5: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Tasks 1-4).

---

### Task 5: Glow V3 protocol layer

**Files:**
- Create: `nitroglow/glow.py`
- Test: `tests/test_glow.py`

**Interfaces:**
- Consumes: `smbus.SMBus` (`.write_byte_data`, `.read_byte_data`).
- Produces:
  - `glow.Glow(smbus, addr: int = 0x28)` with `.set_color(r, g, b) -> None`, `.get_color() -> tuple[int, int, int]`, `.set_mode(mode: int) -> None`, `.get_mode() -> int`, `.set_brightness(value: int) -> None`, `.get_brightness() -> int`, `.set_external_control(enabled: bool) -> None`, `.off() -> None`, `.present() -> bool`
  - `glow.REG_*` constants, `glow.MODES: dict[str, int]`, `glow.GlowError`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_glow.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_glow -v`
Expected: FAIL — `ImportError: cannot import name 'glow'`

- [ ] **Step 3: Write the implementation**

Create `nitroglow/glow.py`:

```python
"""Sapphire Nitro Glow V3 register map.

Register numbers and mode values are taken from OpenRGB's
SapphireNitroGlowV3Controller.h, independently corroborated by the decompiled
TriXX control (see docs/investigation/02-trixx-reversing.md).
"""

ADDR = 0x28

REG_EXTERNAL_CONTROL = 0x0F
REG_MODE = 0x10
REG_RUNWAY_SPEED = 0x11
REG_RUNWAY_REPEAT = 0x12
REG_COLOR_CYCLE_SPEED = 0x13
REG_RAINBOW_SPEED = 0x15
REG_SERIAL_SPEED = 0x16
REG_RED = 0x1A
REG_GREEN = 0x1B
REG_BLUE = 0x1C
REG_MUSIC_VOLUME = 0x29
REG_BRIGHTNESS = 0x3E

MODES = {
    "rainbow": 0x00,
    "runway": 0x01,
    "color_cycle": 0x02,
    "serial": 0x03,
    "sapphire_blue": 0x04,
    "audio": 0x05,
    "custom": 0x06,
    "off": 0x07,
    "external": 0xFF,
}


class GlowError(Exception):
    """The device did not respond as expected."""


def _byte(name, value):
    if not 0 <= value <= 0xFF:
        raise ValueError("%s 0x%x out of range" % (name, value))
    return value


class Glow:
    def __init__(self, sm, addr=ADDR):
        self.sm = sm
        self.addr = addr

    def _write(self, reg, value):
        if not self.sm.write_byte_data(self.addr, reg, value):
            raise GlowError("no ACK writing 0x%02x to register 0x%02x"
                            % (value, reg))

    def _read(self, reg):
        value = self.sm.read_byte_data(self.addr, reg)
        if value is None:
            raise GlowError("no ACK reading register 0x%02x" % reg)
        return value

    def present(self):
        """True if the device ACKs a read of its mode register."""
        return self.sm.read_byte_data(self.addr, REG_MODE) is not None

    def set_color(self, red, green, blue):
        self._write(REG_RED, _byte("red", red))
        self._write(REG_GREEN, _byte("green", green))
        self._write(REG_BLUE, _byte("blue", blue))

    def get_color(self):
        return (self._read(REG_RED), self._read(REG_GREEN), self._read(REG_BLUE))

    def set_mode(self, mode):
        if mode not in MODES.values():
            raise ValueError("unknown mode 0x%x" % mode)
        self._write(REG_MODE, mode)

    def get_mode(self):
        return self._read(REG_MODE)

    def set_brightness(self, value):
        self._write(REG_BRIGHTNESS, _byte("brightness", value))

    def get_brightness(self):
        return self._read(REG_BRIGHTNESS)

    def set_external_control(self, enabled):
        self._write(REG_EXTERNAL_CONTROL, 1 if enabled else 0)

    def off(self):
        self.set_mode(MODES["off"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_glow -v`
Expected: PASS — 10 tests OK

- [ ] **Step 5: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Tasks 1-5).

---

### Task 6: Probe sequence and exit-code classification

**Files:**
- Create: `nitroglow/probe.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `pad.Pad`, `bitbang.BitBang`, `smbus.SMBus`, `glow.Glow`, `pad.CLK_BIT`, `pad.DATA_BIT`.
- Produces:
  - `probe.ProbeResult` — dataclass-like with fields `code: int`, `y: int | None`, `acked: bool`, `per_byte_seconds: float | None`, `message: str`
  - `probe.run_probe(pad_obj, delay: float = 1e-5) -> ProbeResult`
  - `probe.EXIT_OK = 0`, `EXIT_ACCESS = 2`, `EXIT_IDENTITY = 3`, `EXIT_LINES_LOW = 4`, `EXIT_NACK = 5`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_probe.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_probe -v`
Expected: FAIL — `ImportError: cannot import name 'probe'`

- [ ] **Step 3: Write the implementation**

Create `nitroglow/probe.py`:

```python
"""The probe: does anything actually answer on the DDCVGA pads?

The whole point is to distinguish outcomes that the kernel path collapses into
a single -EIO. Each outcome gets its own exit code and its own message.
"""

import time

from nitroglow import bitbang, glow, pad as padmod, smbus

EXIT_OK = 0
EXIT_ACCESS = 2
EXIT_IDENTITY = 3
EXIT_LINES_LOW = 4
EXIT_NACK = 5


class ProbeResult:
    def __init__(self, code, message, y=None, acked=False, per_byte_seconds=None):
        self.code = code
        self.message = message
        self.y = y
        self.acked = acked
        self.per_byte_seconds = per_byte_seconds

    def __repr__(self):
        return ("ProbeResult(code=%d, y=%s, acked=%s, per_byte_seconds=%s)"
                % (self.code, self.y, self.acked, self.per_byte_seconds))


def run_probe(pad_obj, delay=1e-5):
    """Claim the pads, read the line state, and try to address the MCU.

    The caller is responsible for save()/restore() around this; run_probe only
    drives the bus.
    """
    bb = bitbang.BitBang(pad_obj, delay=delay)

    idle = bb.bus_idle()
    y = pad_obj.read_y()

    if not idle:
        return ProbeResult(
            EXIT_LINES_LOW,
            "SCL/SDA do not float high with the pull-down cleared (Y=0x%03x): "
            "no external pull-ups, so most likely nothing is wired to these pads."
            % y,
            y=y,
        )

    sm = smbus.SMBus(bb)
    started = time.monotonic()
    acked = sm.read_byte_data(glow.ADDR, glow.REG_MODE) is not None
    elapsed = time.monotonic() - started
    # A read_byte_data is 4 byte-times: addr+W, reg, addr+R, data.
    per_byte = elapsed / 4.0

    if not acked:
        return ProbeResult(
            EXIT_NACK,
            "Bus is idle-high (Y=0x%03x) but 0x%02x did not ACK: "
            "the lines are alive, yet no device answers at that address."
            % (y, glow.ADDR),
            y=y,
            per_byte_seconds=per_byte,
        )

    return ProbeResult(
        EXIT_OK,
        "Device ACKed at 0x%02x (Y=0x%03x, %.1f ms per byte)."
        % (glow.ADDR, y, per_byte * 1000.0),
        y=y,
        acked=True,
        per_byte_seconds=per_byte,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd <repo> && python3 -m unittest tests.test_probe -v`
Expected: PASS — 6 tests OK

- [ ] **Step 5: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Tasks 1-6).

---

### Task 7: CLI, locking, config, systemd unit

**Files:**
- Create: `nitroglow/cli.py`
- Create: `bin/nitroglow`
- Create: `nitroglow.conf.example`
- Create: `systemd/nitroglow.service`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above — `identity.check_identity`, `regs.RegFile`, `pad.Pad`, `bitbang.BitBang`, `smbus.SMBus`, `glow.Glow`, `probe.run_probe`, all `EXIT_*` codes.
- Produces: `cli.main(argv: list[str] | None = None) -> int`, `cli.parse_args(argv) -> argparse.Namespace`, `cli.load_config(path: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
import os
import tempfile
import unittest

from nitroglow import cli, probe


class TestCLIParsing(unittest.TestCase):
    def test_probe_subcommand(self):
        args = cli.parse_args(["probe"])
        self.assertEqual(args.command, "probe")

    def test_set_parses_hex_colour(self):
        args = cli.parse_args(["set", "--color", "ff8000"])
        self.assertEqual(args.color, (0xFF, 0x80, 0x00))

    def test_set_rejects_bad_colour(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["set", "--color", "nothex"])

    def test_set_parses_mode_and_brightness(self):
        args = cli.parse_args(["set", "--mode", "custom", "--brightness", "80"])
        self.assertEqual(args.mode, "custom")
        self.assertEqual(args.brightness, 80)

    def test_dry_run_flag(self):
        self.assertTrue(cli.parse_args(["set", "--color", "010203",
                                        "--dry-run"]).dry_run)


class TestConfig(unittest.TestCase):
    def test_load_config_reads_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nitroglow.conf")
            with open(path, "w") as fh:
                fh.write("[glow]\nmode = custom\ncolor = 00ff00\nbrightness = 50\n")
            cfg = cli.load_config(path)
            self.assertEqual(cfg["mode"], "custom")
            self.assertEqual(cfg["color"], (0x00, 0xFF, 0x00))
            self.assertEqual(cfg["brightness"], 50)

    def test_missing_config_returns_empty(self):
        self.assertEqual(cli.load_config("/nonexistent/nitroglow.conf"), {})


class TestExitCodes(unittest.TestCase):
    def test_access_error_when_debugfs_missing(self):
        rc = cli.main(["probe", "--regs-path", "/nonexistent/amdgpu_regs",
                       "--sysfs-root", "/nonexistent"])
        self.assertIn(rc, (probe.EXIT_ACCESS, probe.EXIT_IDENTITY))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd <repo> && python3 -m unittest tests.test_cli -v`
Expected: FAIL — `ImportError: cannot import name 'cli'`

- [ ] **Step 3: Write the implementation**

Create `nitroglow/cli.py`:

```python
"""Command-line interface.

Guarantees, in order, for every command that touches hardware:
  identity check -> advisory lock -> save registers -> work -> restore,
with restore also wired to SIGINT/SIGTERM/SIGHUP.
"""

import argparse
import configparser
import fcntl
import os
import signal
import sys

from nitroglow import bitbang, glow, identity, pad as padmod, probe, regs, smbus

LOCK_PATH = "/run/lock/nitroglow.lock"
CONFIG_PATH = "/etc/nitroglow.conf"


def _parse_color(text):
    if len(text) != 6:
        raise argparse.ArgumentTypeError("colour must be 6 hex digits, e.g. ff8000")
    try:
        value = int(text, 16)
    except ValueError:
        raise argparse.ArgumentTypeError("colour must be hex, e.g. ff8000")
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="nitroglow",
                                     description="Sapphire Nitro Glow control")
    parser.add_argument("--regs-path", default=regs.DEBUGFS_REGS)
    parser.add_argument("--sysfs-root", default=identity.DEFAULT_SYSFS)
    parser.add_argument("--delay", type=float, default=1e-5,
                        help="per-half-bit delay in seconds")
    parser.add_argument("--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="test whether the MCU answers")
    p_probe.add_argument("--dry-run", action="store_true")

    p_get = sub.add_parser("get", help="read current mode, colour, brightness")
    p_get.add_argument("--dry-run", action="store_true")

    p_set = sub.add_parser("set", help="set mode, colour and/or brightness")
    p_set.add_argument("--mode", choices=sorted(glow.MODES))
    p_set.add_argument("--color", type=_parse_color)
    p_set.add_argument("--brightness", type=int)
    p_set.add_argument("--dry-run", action="store_true")

    p_off = sub.add_parser("off", help="turn the lighting off")
    p_off.add_argument("--dry-run", action="store_true")

    p_apply = sub.add_parser("apply", help="apply the config file")
    p_apply.add_argument("--config", default=CONFIG_PATH)
    p_apply.add_argument("--dry-run", action="store_true")

    p_restore = sub.add_parser("restore", help="restore saved register state")
    p_restore.add_argument("--dry-run", action="store_true")

    return parser.parse_args(argv)


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        return {}
    if "glow" not in parser:
        return {}
    section = parser["glow"]
    cfg = {}
    if "mode" in section:
        cfg["mode"] = section["mode"]
    if "color" in section:
        cfg["color"] = _parse_color(section["color"])
    if "brightness" in section:
        cfg["brightness"] = int(section["brightness"])
    return cfg


class _Session:
    """identity + lock + open + save/restore, as a context manager."""

    def __init__(self, args):
        self.args = args
        self.regfile = None
        self.pad = None
        self._lock_fd = None

    def __enter__(self):
        identity.check_identity(self.args.sysfs_root)
        self._lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.regfile = regs.RegFile(self.args.regs_path,
                                    dry_run=getattr(self.args, "dry_run", False))
        self.pad = padmod.Pad(self.regfile)
        self.pad.save()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._on_signal)
        self.pad.claim()
        return self

    def _on_signal(self, signum, frame):
        self._cleanup()
        sys.exit(128 + signum)

    def _cleanup(self):
        try:
            if self.pad is not None:
                self.pad.release()
                self.pad.restore()
        finally:
            if self.regfile is not None:
                self.regfile.close()
            if self._lock_fd is not None:
                os.close(self._lock_fd)

    def __exit__(self, *exc_info):
        self._cleanup()
        return False

    def glow(self):
        bb = bitbang.BitBang(self.pad, delay=self.args.delay)
        return glow.Glow(smbus.SMBus(bb))


def main(argv=None):
    args = parse_args(argv)

    if args.command == "probe" and args.dry_run:
        print("dry-run: would claim pads, clear MASK bit 12, read Y, "
              "then address 0x%02x" % glow.ADDR)
        return probe.EXIT_OK

    try:
        with _Session(args) as session:
            if args.command == "probe":
                result = probe.run_probe(session.pad, delay=args.delay)
                print(result.message)
                return result.code

            g = session.glow()

            if args.command == "get":
                print("mode=0x%02x color=%s brightness=0x%02x"
                      % (g.get_mode(), g.get_color(), g.get_brightness()))
                return 0

            if args.command == "set":
                if args.color is not None:
                    g.set_color(*args.color)
                if args.brightness is not None:
                    g.set_brightness(args.brightness)
                if args.mode is not None:
                    g.set_mode(glow.MODES[args.mode])
                return 0

            if args.command == "off":
                g.off()
                return 0

            if args.command == "apply":
                cfg = load_config(args.config)
                if not cfg:
                    print("no config at %s" % args.config, file=sys.stderr)
                    return 1
                if "color" in cfg:
                    g.set_color(*cfg["color"])
                if "brightness" in cfg:
                    g.set_brightness(cfg["brightness"])
                if "mode" in cfg:
                    g.set_mode(glow.MODES[cfg["mode"]])
                return 0

            if args.command == "restore":
                return 0        # _Session.__exit__ restores unconditionally

    except identity.IdentityError as exc:
        print("identity check failed: %s" % exc, file=sys.stderr)
        return probe.EXIT_IDENTITY
    except regs.RegError as exc:
        print("register access failed: %s" % exc, file=sys.stderr)
        return probe.EXIT_ACCESS
    except (BlockingIOError, PermissionError) as exc:
        print("cannot acquire %s: %s" % (LOCK_PATH, exc), file=sys.stderr)
        return probe.EXIT_ACCESS
    except (bitbang.BitBangError, glow.GlowError) as exc:
        print("bus error: %s" % exc, file=sys.stderr)
        return 1

    return 1
```

Create `bin/nitroglow`:

```python
#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from nitroglow.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

Create `nitroglow.conf.example`:

```ini
[glow]
mode = custom
color = 00ff00
brightness = 100
```

Create `systemd/nitroglow.service`:

```ini
[Unit]
Description=Apply Sapphire Nitro Glow lighting
After=multi-user.target

[Service]
Type=oneshot
ExecStart=<repo>/bin/nitroglow apply --config /etc/nitroglow.conf
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Make the entry point executable and run the tests**

Run: `cd <repo> && chmod +x bin/nitroglow && python3 -m unittest tests.test_cli -v`
Expected: PASS — 8 tests OK

- [ ] **Step 5: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests pass (Tasks 1-7), roughly 54 tests.

---

### Task 8: Hardware bring-up

This is the only task that touches the real GPU. Everything before it is provable without hardware; nothing here should be attempted until the full suite passes.

**Files:**
- Create: `docs/investigation/03-hardware-results.md`

**Interfaces:**
- Consumes: `bin/nitroglow` and every layer beneath it.
- Produces: a recorded, dated result — the `Y` value, the exit code, per-byte timing, and whether a round-trip succeeded.

- [ ] **Step 1: Confirm debugfs is writable by root**

The design rests on `CAP_DAC_OVERRIDE` letting root write a mode-`0400` file. Verify before anything else:

Run: `sudo python3 -c "import os; fd=os.open('/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs', os.O_RDWR); print('writable, fd', fd); os.close(fd)"`
Expected: `writable, fd 3`. If this raises `EACCES` or `EPERM`, stop — the userspace approach is dead and the fallback is the deferred kernel module. Record that outcome and report it.

- [ ] **Step 2: Dry-run the probe**

Run: `cd <repo> && sudo ./bin/nitroglow probe --dry-run`
Expected: prints the intended sequence, exits 0, touches nothing.

- [ ] **Step 3: Confirm nothing else is using the OEM bus**

Make sure OpenRGB is not running: `pgrep -a openrgb || echo "not running"`
Expected: `not running`. If it is running, stop it before continuing — concurrent access to `i2c-13` is unsupported.

- [ ] **Step 4: Run the probe for real**

Run: `cd <repo> && sudo ./bin/nitroglow probe --verbose; echo "exit=$?"`

Expected: one of three informative outcomes, all of them valid results:
- `exit=0` — the MCU ACKed. The driver-defect hypothesis is confirmed and the rest of the tool should work.
- `exit=4` — lines stay low with the pull-down cleared. Nothing is wired to these pads; the device is elsewhere. Stop and report.
- `exit=5` — bus idle-high but no ACK at `0x28`. Lines are alive, no device at that address. Stop and report.

- [ ] **Step 5: Verify the registers were restored**

Run: `sudo python3 -c "
import os, struct
fd = os.open('/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs', os.O_RDONLY)
for name, off in [('MASK',0x176A0),('A',0x176A4),('EN',0x176A8),('Y',0x176AC)]:
    print(name, hex(struct.unpack('<I', os.pread(fd,4,off))[0]))
os.close(fd)"`
Expected: `MASK 0xcf401000`, `A 0x0`, `EN 0x0`, `Y 0x0` — i.e. back to the pre-run state recorded in `docs/investigation/01-vbios-i2c-lines.md` section 8.1.

- [ ] **Step 6: If the probe ACKed, round-trip a register**

Run: `cd <repo> && sudo ./bin/nitroglow get`
Then: `sudo ./bin/nitroglow set --mode custom --color ff0000 --brightness 100 && sudo ./bin/nitroglow get`
Expected: the second `get` reports `color=(255, 0, 0)`. Confirm visually that the card's lighting changed — that is the actual success criterion for this whole project.

- [ ] **Step 7: Record the result**

Write `docs/investigation/03-hardware-results.md` containing: the date, the exact exit code, the `Y` value, the measured per-byte time, whether the round-trip worked, and whether the lighting visibly changed. If per-byte time exceeds ~4 ms (making a 4-byte transaction approach a 35 ms SMBus timeout), note that the `bitbang` layer likely needs porting to C, as anticipated in the spec.

- [ ] **Step 8: Checkpoint**

Run: `cd <repo> && python3 -m unittest discover -s tests -v`
Expected: all tests still pass. Hardware work must not have required changing tested behaviour; if it did, the change needs its own test.

---

## Self-Review

**Spec coverage** — every section maps to a task:

| spec section | task |
|---|---|
| Architecture, six layers | 1-7, one layer per task |
| Registers and offsets | 1 (`regs.py`), 2 (`pad.py`) |
| `EN` polarity verification | 2, step 1 |
| Glow V3 register map | 5 |
| Probe gate, steps 1-7 | 6 (sequence), 7 (`_Session` wraps identity/save/restore), 8 (execution) |
| Exit codes | 6 (constants and classification), 7 (mapped in `main`) |
| Register allowlist | 1 |
| Identity guard | 1, enforced in 7 |
| Save/restore incl. signals | 2 (mechanism), 7 (signal wiring) |
| Advisory lock | 7 |
| Bounded retries | 3 (`STRETCH_RETRIES`) |
| `--dry-run` semantics | 1 (`RegFile`), 7 (probe special case) |
| Fake pad model and fake MCU | 3 |
| Hardware test order | 8 |
| Register invariant after run | 8, step 5 |
| CLI, config, systemd | 7 |
| Timing measurement | 6 (`per_byte_seconds`), 8 step 7 |
| debugfs writability confirmation | 8, step 1 |

**Placeholder scan:** none — every step carries runnable code or an exact command with expected output.

**Type consistency:** `Pad` exposes `set_scl/set_sda/read_scl/read_sda/read_y`; `FakePad` implements exactly those, so `BitBang` accepts either. `SMBus.read_byte_data` returns `int | None` and both `Glow._read` and `probe.run_probe` treat `None` as "no ACK". `EXIT_*` constants live only in `probe` and are imported from there by `cli`. `MODES` values are validated in `Glow.set_mode` and reused as `argparse` choices via `sorted(glow.MODES)`.

**One deliberate deviation from the skill default:** commit steps are replaced by full-suite checkpoint runs, because this project is intentionally not under git.
