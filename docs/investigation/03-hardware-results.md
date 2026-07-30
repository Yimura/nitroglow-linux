# Hardware bring-up results

Date: 2026-07-30
Host: kernel 7.1.5-arch1-2, Sapphire RX 5700 XT Nitro+ at `0000:0e:00.0`
Tool: `bin/nitroglow` (this repo), Python 3.14, stdlib only

## Result: the MCU is present and reachable from userspace

```
$ sudo ./bin/nitroglow --verbose probe
Device ACKed at 0x28 (Y=0x101, 2.5 ms per byte).
exit=0
```

Exit code 0 — `EXIT_OK`. Both open questions from the investigation are now closed.

### 1. Is a device wired to the DDCVGA pads? YES

`Y = 0x101` with the pads claimed and both lines released: SCL (bit 0) and SDA
(bit 8) both float high, so external pull-ups are present and something is
wired. This is the measurement `docs/investigation/01-vbios-i2c-lines.md` section 8.1 could not make,
because that dump was taken with the pads unclaimed, where `Y` reads `0`
regardless.

### 2. Was the silence a driver defect? EFFECTIVELY YES

Clearing `DC_GPIO_DDCVGA_MASK` bit 12 (`DATA_PD_EN`) and bit-banging from
userspace reaches a device that the kernel's `i2c-13` path cannot reach at any
address. The stuck internal SDA pull-down described in
`docs/upstream/amdgpu-report.md` (issue 2) is therefore not merely *present*
but *load-bearing*: with it cleared, the bus works.

Caveat kept honest: this demonstrates that bypassing DC's software engine works.
It does not isolate which of DC's behaviours is individually fatal, since this
tool replaces the whole transfer path rather than fixing one bit in it.

## Device state

Stock state as found (never set by this tool):

```
mode=0x00 (rainbow)  color=(0, 0, 255)  brightness=0x06
```

After `set --mode custom --color ff0000 --brightness 100`:

```
mode=0x06 (custom)   color=(255, 0, 0)  brightness=0x64
```

Write-then-read-back round trip succeeded, which is stronger evidence than a
bare ACK — the MCU stored and returned the values.

## Timing

**2.5 ms per byte.** A `read_byte_data` is 4 byte-times ≈ 10 ms, comfortably
under the ~35 ms SMBus transaction timeout. The risk flagged in the spec did not
materialise: **Python is fast enough, no C port of the `bitbang` layer needed.**

## Register hygiene

Restored correctly after every run:

```
MASK  0xcf401000     (as saved, pull-down bit back on)
A     0x00000000
EN    0x00000000
Y     0x00000101     (read-only status; reflects both lines sitting high)
```

`MASK`/`A`/`EN` match the pre-run values recorded in `docs/investigation/01-vbios-i2c-lines.md`
section 8.1. Note `Y` reads `0x101` here where the earlier dump showed
`0x00000000`; `Y` is read-only line status, not state this tool left behind.

## Preconditions confirmed

- `amdgpu_regs` opens `O_RDWR` as root despite its `0400` mode — `CAP_DAC_OVERRIDE`
  works as the spec assumed. The userspace approach was viable.
- No display disturbance observed: DDCVGA drives no connector on this card.
- OpenRGB was not running; `i2c-13` untouched by anything else.

## Consequences for the upstream reports

`docs/upstream/openrgb-work-item-1046.md` and `docs/upstream/amdgpu-report.md` were
written while "empty bus vs driver defect" was still open. Both now need
updating: the bus is **not** empty, and the fix direction is confirmed. The
amdgpu report's issue 2 (the `hw_ddc.c` VGA guard reading an undefined
`CLK_PD_EN` bit) gains a working userspace counter-example.
