# nitroglow-linux

RGB lighting control for the **Sapphire Radeon RX 5700 XT Nitro+** on Linux, by bit-banging i2c over the GPU's GPIO pads from userspace.

No kernel module. No patched driver. No `LD_PRELOAD` hack. Just four registers and a state machine.

```console
$ sudo ./bin/nitroglow --verbose probe
Device ACKed at 0x28 (Y=0x101, 2.5 ms per byte).

$ sudo ./bin/nitroglow set --mode custom --color ff0000 --brightness 100
$ sudo ./bin/nitroglow get
mode=0x06 color=(255, 0, 0) brightness=0x64
```

## Why this exists

OpenRGB does not detect this card on Linux, and the usual explanations are all wrong:

- The device **is** supported — `SAPPHIRE_NAVI10_NITRO_PLUS_SUB_DEV1 = 0xE409`, Nitro Glow V3, address `0x28`.
- Permissions are **fine** — `60-openrgb.rules` is installed, `/dev/i2c-*` carry the right ACLs.
- The i2c bus **is** exposed — kernel ≥ 6.15 publishes it as `AMDGPU DM i2c OEM bus`.

And yet every transfer on that bus fails, for every address, with `-EIO`.

The cause is a defect in the display driver. On the VGA/OEM line, `hw_ddc.c:96` tests a `CLK_PD_EN` bit that `DC_GPIO_DDCVGA_MASK` does not define:

```
$ grep -c 'DC_GPIO_DDCVGA_MASK__DC_GPIO_DDCVGACLK_PD_EN' dcn_2_0_0_sh_mask.h
0
```

So the guard can never be false, `hw_ddc.c:97-99` re-asserts the internal SDA pull-down on **every** open, and nothing in that file ever clears it. `dce_i2c_sw.c:298-339 start_sync_sw` then releases SDA, reads it back, never sees it rise, and gives up after `I2C_SW_RETRIES = 10` — *before a single address bit reaches the wire*. That is why the failure is address-independent, and why it looks identical to "nothing is there".

That pull-down bit is writable from userspace. This tool clears it, claims the pads, and does the i2c itself.

## Evidence that the MCU is really there

Three independent sources agree on the same line and the same address:

| source | says |
|---|---|
| The card's own VBIOS (`firmwareinfo` +0x30 = `02 97 50 00`) | OEM i2c device present, gpio `0x97` = `DC_GPIO_DDCVGA`, slave `0x50` 8-bit → **`0x28`** |
| Sapphire's TriXX 11.2.0 (decrypted, decompiled) | Glow V3 control, `address = 40`, line `= 1` = `ADL_DL_I2C_LINE_OEM` |
| OpenRGB's own source | `SAPPHIRE_NITRO_GLOW_V3_ADDR = 0x28`; whitelists a bus literally named `AMDGPU i2c bit bus OEM 0x97` |

And then the hardware confirmed it: `Y = 0x101` with the pads claimed, meaning both lines float high — external pull-ups exist, something is wired — followed by an ACK at `0x28` and a successful write/read-back round trip.

The `Y` measurement is the one that mattered, and it has a trap in it: **`Y` only reports real line levels once the pads are claimed.** Read it unclaimed, as an ordinary register dump does, and every line reads `0` whether a device is present or not. That single detail is why the question stayed open for so long.

## Install and use

Requires Python 3 (stdlib only — no pip, no venv), root, and debugfs mounted.

```console
$ git clone https://github.com/Yimura/nitroglow-linux
$ cd nitroglow-linux
$ python3 -m unittest discover -s tests -t .     # 54 tests, no hardware needed
$ sudo ./bin/nitroglow probe
```

| command | does |
|---|---|
| `probe` | test whether the MCU answers; distinct exit code per outcome |
| `get` | read current mode, colour, brightness |
| `set --mode custom --color ff0000 --brightness 100` | set them |
| `off` | mode `0x07` |
| `apply --config /etc/nitroglow.conf` | apply a config file |
| `restore` | put the registers back |

Global flags go **before** the subcommand: `nitroglow --verbose probe`.

Modes: `rainbow`, `runway`, `color_cycle`, `serial`, `sapphire_blue`, `audio`, `custom`, `off`, `external`.

Persistence across reboot:

```console
$ sudo cp nitroglow.conf.example /etc/nitroglow.conf
$ sudo cp systemd/nitroglow.service /etc/systemd/system/
$ sudo systemctl enable --now nitroglow.service
```

### Probe exit codes

The whole point is to *not* collapse every failure into one `-EIO`:

| code | meaning |
|---|---|
| 0 | ACK at `0x28` — device present |
| 2 | not root, or debugfs unavailable |
| 3 | wrong GPU — identity guard refused |
| 4 | lines held low with the pull-down cleared — likely nothing wired |
| 5 | bus idle-high but no ACK — lines alive, no device at that address |

## Safety

This writes GPU registers behind the display driver's back. That is inherently unsupported, so the guards are the design:

- **Four-offset allowlist.** Only `DC_GPIO_DDCVGA_{MASK,A,EN,Y}` (`0x176A0`/`A4`/`A8`/`AC`) may ever be written; every write asserts membership.
- **Identity guard.** Refuses to run unless PCI reports `1002:731F` / `1DA2:E409` / revision `0xC1`.
- **Save and restore** on every exit path, including `SIGINT`/`SIGTERM`/`SIGHUP`.
- **Advisory lock** at `/run/lock/nitroglow.lock`.
- **Bounded retries** — a wedged bus fails rather than hangs holding the pads.

Two things that make this safer than it sounds on *this specific card*: DDCVGA drives no connector (there is no VGA port), and the tool never touches any other register.

**Do not** run OpenRGB or `i2cget` against `i2c-13` while this holds the pads — concurrent access is unsupported and undetectable. **Never** run `i2cdetect` sweeps on GPU buses; they poke VRM, fan and thermal controllers.

## Hardware support

Tested on exactly one card: Sapphire RX 5700 XT Nitro+, `1002:731F` / `1DA2:E409`, revision `0xC1`, kernel 7.1.5.

The register offsets are specific to Navi 10, and the identity guard enforces that. Other Nitro+ cards very likely need different offsets — `scripts/atom_i2c_parse.py` will enumerate them from your own VBIOS dump. Patches welcome; please include the VBIOS-derived evidence rather than guessed offsets.

## Architecture

Six layers. Hardware-specific knowledge lives in the bottom two; everything above is ordinary i2c and portable.

| layer | responsibility |
|---|---|
| `regs` | allowlisted 32-bit access to `amdgpu_regs` |
| `pad` | claim/release pads, drive and read SCL/SDA, clear the stuck pull-down |
| `bitbang` | START, STOP, repeated START, byte write + ACK, byte read + NACK |
| `smbus` | `write_byte_data`, `read_byte_data` |
| `glow` | Nitro Glow V3 register map |
| `cli` | subcommands, config, locking, signal handling |

That boundary is why the test suite needs no hardware: `tests/fakes.py` implements an open-drain electrical model (wired-AND of every driver plus a pull-up) and a bit-level fake i2c slave, so framing, ACK/NACK and repeated-START are all exercised in software.

One convention worth calling out, because the kernel's own comment gets it backwards. `hw_gpio.c:109-114` says `EN = 0` pulls the line down — but the call chain (`write_bit_to_ddc(SCL, true)` → `dal_gpio_set_value(pin, 1)` → `EN = ~1 = 0`, then `write_byte_sw` *waits for SCL to read high*) proves the opposite: **`EN` set = driving low, `EN` clear = released.** See `nitroglow/pad.py` for the full derivation.

## The investigation

Full write-ups in [`docs/investigation/`](docs/investigation/):

1. [**VBIOS i2c lines**](docs/investigation/01-vbios-i2c-lines.md) — every i2c line on the board, exposed vs hidden. Note that atomfirmware images have **no** `GPIO_I2C_Info` table; the equivalent data lives in `gpio_pin_lut` + `displayobjectinfo` + `firmwareinfo`.
2. [**TriXX reversing**](docs/investigation/02-trixx-reversing.md) — how Sapphire's own tool talks to the MCU. The resource blobs are *not* AES despite an `IAes` string in the binary; they are a hand-rolled repeating-key XOR chain, cracked by known-plaintext against the DOS header.
3. [**Hardware results**](docs/investigation/03-hardware-results.md) — the probe, the timing, the register hygiene.

Design and plan: [`docs/design.md`](docs/design.md), [`docs/implementation-plan.md`](docs/implementation-plan.md).

## Upstream

Two separable issues, written up in [`docs/upstream/`](docs/upstream/) but **not yet filed**:

1. **`amdgpu` never calls `amdgpu_i2c_init()` on atomfirmware ASICs.** `amdgpu_device.c:3987-4007` calls it only in the `else` arm of `if (adev->is_atom_fw)`. Consequence: the adapter name `AMDGPU i2c bit bus OEM 0x97` — which OpenRGB explicitly whitelists — is unreachable on *every* Vega-and-later card. A candidate patch is in [`kernel/`](kernel/), **untested: never compiled, applied or loaded.**
2. **The `hw_ddc.c` VGA pull-down guard**, described above. This is the cleaner standalone bug report.

Related: [OpenRGB work item 1046](https://gitlab.com/CalcProgrammer1/OpenRGB/-/work_items/1046).

## What is deliberately not in this repo

No vendor binaries or derived artifacts: no TriXX installer, no decrypted or decompiled assemblies, no VBIOS dumps, no Ghidra project, no vendored OpenRGB or kernel trees. Only original analysis and original code.

To reproduce the reverse engineering you supply your own inputs:

- `scripts/decrypt_res.py` — decrypts TriXX RCDATA blobs you extract yourself from a copy you obtained legitimately.
- `scripts/trace_model_table.py` — parses a decompiled assembly *you* produce; it embeds none of it.
- `scripts/atom_i2c_parse.py` — parses a VBIOS image you dump from your own card.
- `ghidra_scripts/*.java` — headless Ghidra analysis scripts.

Reverse engineering here was done for interoperability: making hardware the owner already owns work with the operating system of their choice.

## Licence

GPL-2.0. Matches OpenRGB and the kernel, so anything here can flow upstream to either. The patch in `kernel/` is GPL-2.0 as kernel-derived work regardless.

## Credits

[OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB) — the Nitro Glow V3 register map and mode values come from `SapphireNitroGlowV3Controller.h` (K900, 2021). This project's independent reversing agreed with it register for register, which is a nice testament to the original work.
