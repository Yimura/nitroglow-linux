# Nitro Glow userspace i2c control — design

Date: 2026-07-30
Status: approved, not yet implemented
Repo: `<repo>` (local only, not under git by choice)

## Goal

Set the RGB lighting on the Sapphire RX 5700 XT Nitro+ from Linux, persistently across reboots, without building or reloading any kernel module.

Success means: a CLI that sets colour, mode and brightness on this machine, and a systemd unit that reapplies it at boot.

## Background — what is already established

From the prior investigation (the shared investigation brief, `docs/investigation/02-trixx-reversing.md`, `docs/investigation/01-vbios-i2c-lines.md`):

1. The Glow MCU is at 7-bit i2c address `0x28` on the **OEM line**, which is the `DC_GPIO_DDCVGA` pin pair. Two independent sources agree: the card's VBIOS `firmwareinfo` table (`board_i2c_feature_id = 0x02`, `gpio_id = 0x97`, `slave_addr = 0x50` 8-bit → `0x28` 7-bit), and TriXX 11.2.0's decrypted managed code (`address = 40`, line `= 1` = `ADL_DL_I2C_LINE_OEM`).
2. That line is already exposed by the kernel as `i2c-13` (`AMDGPU DM i2c OEM bus`), but every transfer fails for every address.
3. The likely cause is a DC defect: `hw_ddc.c:96` reads a `CLK_PD_EN` bit that `DC_GPIO_DDCVGA_MASK` does not define, so the guard is always true and `hw_ddc.c:97-99` re-asserts the internal SDA pull-down on every open, cleared nowhere. Register readback confirms the asymmetric signature: DDCVGA `MASK = 0xcf401000` (bit 12 set, bit 4 clear), versus `0xcf411010` on lines DC opens normally and `0xcf400000` on lines nothing touches.
4. `dce_i2c_sw.c:298-339 start_sync_sw` drives SDA high then requires `read_bit_from_ddc(SDA)` to read back high; with the pull-down asserted it never can, so it exhausts `I2C_SW_RETRIES = 10` and fails **before the address phase**. This matches the observed address-independent failure.
5. Not established: whether a device is actually on those pads. The earlier register dump could not tell, because `Y` reads `0` on every line when no pad is claimed.
6. The Glow V3 protocol is plain SMBus — `i2c_smbus_write_byte_data` / `read_byte_data` (`SapphireNitroGlowV3Controller.cpp`). No custom framing.
7. `amdgpu_debugfs_regs_fops` has a `.write` handler (`amdgpu_debugfs.c:1614-1619`). The file is mode `0400`; root writes via `CAP_DAC_OVERRIDE`. **To be confirmed by a harmless open-for-write as the first implementation step.**

The decisive insight this design rests on: the stuck pull-down bit is writable from userspace, so we can clear it, claim the pads ourselves, and bypass DC's software engine entirely.

## Non-goals

- Any kernel module, patched or standalone. (Deferred; `kernel/amdgpu-atomfirmware-oem-i2c.diff` exists, untested.)
- OpenRGB integration. The tool is standalone; OpenRGB will not see the device.
- Upstream submission. Drafts exist at `docs/upstream/openrgb-work-item-1046.md` and `docs/upstream/amdgpu-report.md`, unposted.
- Display colour management (ICC, DRM colour pipeline). Unrelated subsystem.
- Anything touching the NVIDIA GTX 1080.

## Architecture

Six layers. Hardware-specific knowledge is quarantined in the bottom two; everything above is ordinary i2c and portable.

| layer | responsibility | depends on |
|---|---|---|
| `reg` | `pread`/`pwrite` of 4 bytes at `reg << 2` in `amdgpu_regs` | debugfs, root |
| `pad` | claim/release pads, set and read SCL/SDA, clear the stuck pull-down | `reg` |
| `bitbang` | START, STOP, repeated START, write byte + read ACK, read byte + NACK | `pad` |
| `smbus` | `write_byte_data`, `read_byte_data` | `bitbang` |
| `glow` | Nitro Glow V3 register map | `smbus` |
| `cli` | subcommands, config file, systemd unit | `glow` |

Only `pad` encodes DC's conventions. Only `reg` knows about debugfs. This boundary is what would make a later kernel module cheap, and what lets nearly everything be tested without hardware.

### Registers

Device node: `/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs`, seek to `reg << 2`.

| register | dword | byte offset |
|---|---|---|
| `DC_GPIO_DDCVGA_MASK` | `0x5DA8` | `0x176A0` |
| `DC_GPIO_DDCVGA_A` | `0x5DA9` | `0x176A4` |
| `DC_GPIO_DDCVGA_EN` | `0x5DAA` | `0x176A8` |
| `DC_GPIO_DDCVGA_Y` | `0x5DAB` | `0x176AC` |

Semantics, mirroring `hw_gpio.c` / `dce_i2c_sw.c`: `MASK` bit set claims the pad for software; `A` holds the driven value; `EN` is the drive enable and is written inverted (`REG_UPDATE(EN_reg, EN, ~value)`); `Y` reads the line back.

The exact `EN` polarity is the one piece of pad semantics taken from a single reading of `hw_gpio.c:109-115`. It must be re-checked against that source as the first task of the `pad` layer, and encoded as an explicit assertion in the fake pad model so that getting it backwards fails a test rather than silently driving the bus wrong. In all four registers **CLK is bit 0 and DATA is bit 8**. `MASK` bit 12 is `DATA_PD_EN`, the internal SDA pull-down to clear.

Note: that CLK is bit 0 is taken from the DCN 2.0 headers and from DC's own code, not from the VBIOS — the ATOM entry supplies only the DATA bit shift.

### Glow V3 register map

Address `0x28`. `0x10` mode, `0x0F` external control, `0x3E` brightness, `0x1A`/`0x1B`/`0x1C` red/green/blue, `0x11`/`0x12` runway speed and repeat, `0x13` colour-cycle speed, `0x15` rainbow speed, `0x16` serial speed, `0x29` music volume.

Modes: `0x00` rainbow, `0x01` runway, `0x02` colour cycle, `0x03` serial, `0x04` sapphire blue, `0x05` audio visualisation, `0x06` custom, `0x07` off, `0xFF` external control.

## The probe gate

`probe` is built and run before anything else, because "nothing is wired to those pads" is still a live possibility.

1. Verify PCI `1002:731F`, subsystem `1DA2:E409`, revision `0xC1`. Refuse otherwise.
2. Save all four registers.
3. Clear `MASK` bit 12 (`DATA_PD_EN`).
4. Claim both pads (`MASK` bits 0 and 8) and drive nothing — release SCL and SDA.
5. Read `Y`. **This is the measurement the earlier dump could not make**, because it was taken with the pads unclaimed. `0x101` means external pull-ups exist and something is wired; `0x000` means the lines sit low with the internal pull-down gone, i.e. nothing is there.
6. Only if the bus reads idle-high: START, address `0x28 << 1 | W`, sample ACK.
7. Restore all four registers.

Step 5 gates step 6. Steps 2 and 7 bracket everything.

### Exit codes

Five outcomes that must never be collapsed into one, since exactly that ambiguity (`-EIO` for every failure mode) is what stalled the investigation:

| code | meaning |
|---|---|
| 0 | ACK at `0x28` — device present |
| 2 | not root, or debugfs unavailable/unwritable |
| 3 | wrong GPU (identity guard failed) |
| 4 | lines held low with the pull-down cleared — likely nothing wired |
| 5 | bus idle-high but NACK at `0x28` — no device at that address |

Codes 3, 4 and 5 are specific to `probe`. Every other subcommand uses 0 for success and 1 for a generic failure, with 2 and 3 retaining their meanings above.

## Safety

- **Register allowlist.** The four byte offsets are hardcoded; every write asserts membership first. Bounds the blast radius to pads that drive no connector on this card (it has no VGA port).
- **Identity guard** before the first write.
- **Save and restore** all four registers on every exit path: normal, exception, and `SIGINT`/`SIGTERM`/`SIGHUP`. Idempotent, and runs even if the probe aborts midway.
- **Advisory lock** at `/run/lock/nitroglow.lock`. Concurrent use of `i2c-13` by anything else (OpenRGB, `i2cget`) is unsupported and documented as such — we cannot detect it.
- **Everything bounded.** Fixed retry counts, an overall watchdog. A wedged bus must fail rather than hang holding the pads.
- **`--dry-run`** prints intended writes without issuing them. It applies to `set` and `off`. `probe` cannot be meaningfully dry-run — it exists to touch the hardware — so `probe --dry-run` prints the sequence it would perform and exits 0 without writing.
- Never `i2cdetect`. Single-address operations only.

## Testing

**Without hardware.** A fake pad model implements open-drain behaviour (wired-AND of all drivers plus a pull-up), and a fake Glow MCU ACKs at `0x28` and holds a register file. `bitbang`, `smbus` and `glow` are then testable in-process: START/STOP framing, ACK and NACK, repeated START for `read_byte_data`, and a NACKing device. Failure paths are tested deliberately — SDA stuck low must yield exit code 4, not a hang.

**On hardware**, in order: `probe` → read one Glow register → round-trip write and read-back of `REG_RED`, restoring the original value. The round-trip is the real proof of communication; a bare ACK could be a pull-up artifact.

**Invariant.** After every run, assert the four registers equal their saved values.

## CLI and persistence

Subcommands `probe`, `get`, `set`, `off`, `restore`; flags `--dry-run`, `--verbose`. Config at `/etc/nitroglow.conf` (mode, colour, brightness), applied by a systemd oneshot unit at boot.

Whether the MCU retains state across reboot in its own NVRAM is to be determined empirically, not assumed. The boot unit is worth having regardless: dual-booting Windows lets TriXX change the state underneath us.

## Language and the timing risk

Python 3, stdlib only, matching the existing `scripts/`.

The named risk: each bit is a `pwrite` syscall, so a byte costs tens of them. Plain i2c has no minimum clock, so slowness is harmless — but if the Glow MCU enforces an SMBus-style ~35 ms transaction timeout, Python may be too slow. `probe` therefore measures and reports per-byte wall time. If that proves to be the blocker, only the `bitbang` layer is ported to a small C helper; the layer boundary keeps that contained.

## Known risks

- The device may not be there at all. The probe is designed to say so clearly (exit 4 or 5) rather than fail ambiguously.
- Writing GPU registers behind DC's back is inherently unsupported. Mitigated by the allowlist, save/restore, and the fact that DDCVGA drives no connector here.
- Register offsets are specific to this ASIC and card; the identity guard enforces that.
- Timing, as above.
- If DC opens the OEM line concurrently it will re-assert the pull-down underneath us. Nothing on this system does so unless OpenRGB or `i2cget` is run against `i2c-13`.
