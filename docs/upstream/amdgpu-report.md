# Draft report for amd-gfx / dri-devel

*Not yet sent. Two separable issues; the second is the more clearly actionable bug.*

Hardware: Sapphire RX 5700 XT Nitro+, Navi 10, `1002:731F` subsystem `1DA2:E409`.
Kernel: 7.1.5 (`7.1.5-arch1-2`). All source citations are against v7.1.5.
All evidence below is from read-only VBIOS parsing and read-only register reads. Nothing was written to the GPU.

---

## Issue 1: `amdgpu_i2c_init()` is never called on atomfirmware ASICs, so board OEM i2c lines are never exposed

`amdgpu_device.c:3987-4007` calls `amdgpu_i2c_init()` only in the `else` arm of `if (adev->is_atom_fw)`. Every Vega-and-later ASIC has `is_atom_fw = true`, so the function never runs on them.

Even if it did, it could not help there today:

* `amdgpu_i2c.c:220-234` gates the DC branch on `CHIP_POLARIS10/11/12` only.
* `amdgpu_atombios_oem_i2c_init()` walks the legacy `GPIO_I2C_Info` data table, which does not exist in an atomfirmware image. Verified on this card's ROM: master data table index 10 (file offset `0x92D6`) is `0x0000`.

On atomfirmware the same information is present, just relocated. This card's VBIOS states it outright — `firmwareinfo` (master index 4) rev 3.3, at `+0x30`:

```
02 97 50 00
board_i2c_feature_id         = 0x02   (feature present; same test as bios_parser2.c:1934)
board_i2c_feature_gpio_id    = 0x97
board_i2c_feature_slave_addr = 0x50   (8-bit wire format; cf. amdgpu_ras_eeprom.c:184-194)
```

and `gpio_pin_lut` (master index 12) entry 12 resolves `0x97`:

```
a9 5d 00 00 08 08 97 00
data_a_reg_index = 0x5DA9  gpio_bitshift = 8  gpio_mask_bitshift = 8  gpio_id = 0x97
```

`0x5DA9` = `DCN_BASE__INST0_SEG2` (`0x34C0`, `navi10_ip_offset.h:269`) + `mmDC_GPIO_DDCVGA_A` (`0x28E9`). Subtracting that base from all 13 LUT entries yields a named DCN2.0 register with no misses (`0x5D91..0x5DA5` = `DDC1_A..DDC6_A`, `0x5DA9` = `DDCVGA_A`, `0x5DB5` = `HPD_A`), so the mapping does not depend on interpreting the ATOM lane-mux field.

**Effect.** Boards that hang a vendor device off a spare i2c pin pair — RGB controllers on Sapphire/ASUS/MSI cards, board EEPROMs — have no bit-banging adapter registered for it on any Vega+ ASIC. Userspace that expects the adapter name `"AMDGPU i2c bit bus OEM 0x97"` (produced by `amdgpu_atombios.c:169` → `amdgpu_i2c.c:192-193`, and matched verbatim by OpenRGB) never sees it on these parts.

A candidate patch adding the atomfirmware equivalent is attached separately (`kernel/amdgpu-atomfirmware-oem-i2c.diff`). **It has not been compiled, applied, or loaded** — it is offered as a starting point and as the cheapest experiment that would settle Issue 2, not as tested work. It derives `MASK`/`EN`/`Y` from the `_A` register index using the fixed 4-register `DC_GPIO_*` pad-block stride, guarded by an `I2C_HW_CAP` check plus a structural check that the entry shares a 4-dword-strided block with another i2c entry.

---

## Issue 2: on `GPIO_DDC_LINE_DDC_VGA`, `hw_ddc.c` reads a `..CLK_PD_EN` bit that `DC_GPIO_DDCVGA_MASK` does not define

This one is a plain bug and independent of Issue 1.

`hw_ddc.c:84-87` does:

```c
REG_GET_3(..., DC_GPIO_DDC1CLK_PD_EN, &ddc_clk_pd_en, ...);
```

All DDC lines, VGA included, share `DDC_MASK_SH_LIST_COMMON` (`ddc_regs.h:96-102`), which uses the **DDC1** field positions. On the VGA line that resolves to bit 4 of `DC_GPIO_DDCVGA_MASK` — and that register has no `CLK_PD_EN` field:

```
$ grep -c 'DC_GPIO_DDCVGA_MASK__DC_GPIO_DDCVGACLK_PD_EN' dcn_2_0_0_sh_mask.h
0
```

So `ddc_clk_pd_en` reads 0 unconditionally, the guard at `hw_ddc.c:96`

```c
if (!ddc_data_pd_en || !ddc_clk_pd_en)
```

can never evaluate false on this line, and the `GPIO_DDC_LINE_DDC_VGA` case at `hw_ddc.c:97-99` re-asserts the internal SDA pull-down on **every** open. Nothing in `hw_ddc.c` ever clears it.

**Observed on hardware.** Read-only, via `/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs`, GPU idle, DP-4 connected, nothing written:

```
DC_GPIO_DDCVGA_MASK  (dword 0x05da8) = 0xcf401000
DC_GPIO_DDC2_MASK    (dword 0x05d94) = 0xcf411010
DC_GPIO_DDC1_MASK    (dword 0x05d90) = 0xcf411010
DC_GPIO_DDC3_MASK    (dword 0x05d98) = 0xcf400000
DC_GPIO_DDC5_MASK    (dword 0x05da0) = 0xcf400000
```

| line | bit 4 `CLK_PD_EN` | bit 12 `DATA_PD_EN` | status |
|---|---|---|---|
| DDCVGA | **0** | **1** | asymmetric |
| DDC1, DDC2 | 1 | 1 | DC opens these normally |
| DDC3, DDC5 | 0 | 0 | no consumer, never touched |

Only the VGA line shows the asymmetry, and it is precisely what `hw_ddc.c:97-99` produces — the lines DC opens normally have both bits set, the lines nothing touches have neither. The predicted path demonstrably ran.

**Downstream consequence.** The VGA/OEM line can only use DC's software engine (`link == NULL` forces `hw_supported = false` at `link_ddc.c:131-134`, and `GPIO_DDC_LINE_DDC_VGA == 6` is not `< res_cap->num_ddc == 6` per `dcn20_resource.c:691`). `dce_i2c_sw.c:298-339 start_sync_sw` releases SDA, reads it back at `:316`, and fails after `I2C_SW_RETRIES = 10` — before any address byte. On this host every transfer on `AMDGPU DM i2c OEM bus` fails for every address tried, which matches an address-phase failure rather than a NAK.

**Honest limits.** I have not shown this is fatal. These internal pull-downs are typically weak (tens of kΩ) and a normal 2.2k–10k board pull-up should dominate, so the mechanism is confirmed *present* but not proven to be what breaks the bus. Nor can I show a device is on those pads: `DC_GPIO_DDCVGA_Y` reads `0`, but so does every `Y` register including DDC2's with a monitor attached, because no pad is claimed at rest. And `amdgpu_dm_i2c_xfer()` (`amdgpu_dm.c:9177`) returns `-EIO` alike for NULL ddc_pin, failed START, and NAK, so the error code carries no information.

Two related observations noted in passing, not investigated: `.ddc_setup = 0` in `hw_factory_dcn20.c:112-117` looks like a genuine omission (`mmDC_I2C_DDCVGA_SETUP = 0x1eb5` exists and `ddc_regs.h:70-72` names it), though it is reachable only from the polling paths, not from i2c; and `ALLOW_HW_DDCVGA_PD_EN` (bit 22) is referenced by no DC source.

---

## Reproduction

```
# the OEM bus on this host is i2c-13, "AMDGPU DM i2c OEM bus"
i2cget -y 13 0x28    # Error: Read failed
i2cget -y 13 0x50    # Error: Read failed  (address-independent)
```

Both fail identically, as does every address tried. Do not run `i2cdetect` sweeps on these buses — they poke VRM/fan/thermal controllers.
