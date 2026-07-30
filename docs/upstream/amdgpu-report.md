# Bug report for gitlab.freedesktop.org/drm/amd

Paste-ready. **Not yet filed.** Items marked **[FILL IN]** need attention before submitting.

Scope: this reports the DC software-i2c bug only. The separate `amdgpu_i2c_init()` enablement gap is a feature request, not a bug, and is mentioned at the end rather than muddled into the same issue.

---

**Title:** `drm/amd/display: DC software i2c engine cannot work on GPIO_DDC_LINE_DDC_VGA — read path missing the special case added in c0b2753f5db2`

---

## Summary

On Navi 10, the DDC line exposed as `AMDGPU DM i2c OEM bus` cannot complete an i2c transfer to any address. Every transfer fails identically, and it fails **before the address phase** — so this is not a device-absent condition.

The cause is a read/write asymmetry in `hw_ddc.c`. Commit `c0b2753f5db2` ("drm/amd/display: Fix gpio port mapping issue") special-cased the **write** path for `GPIO_DDC_LINE_DDC_VGA`, because bit 4 of that line's `MASK` register has different usage. The **read** path feeding the guard around it was left unchanged, so it still samples `DC_GPIO_DDC1CLK_PD_EN` on a register where that field does not exist. The guard can therefore never evaluate false on this line, and the internal SDA pull-down is re-asserted on every open and never cleared.

I have confirmed on hardware that a device is present on this line and is reachable once that pull-down is cleared, so the pads are wired and functional.

## Hardware and software

- GPU: Sapphire Radeon RX 5700 XT Nitro+ — `1002:731F`, subsystem `1DA2:E409`, revision `0xC1` (Navi 10), at `0000:0e:00.0`
- Kernel: 7.1.5 (Arch Linux, `7.1.5-arch1-2`)
- A second GPU (NVIDIA GTX 1080) is present but unrelated
- **[FILL IN]** attach full `dmesg`

## What happens

```
$ i2cget -y 13 0x28
Error: Read failed

$ i2cget -y 13 0x50
Error: Read failed
```

`i2c-13` is `AMDGPU DM i2c OEM bus`. Every address fails the same way. Note that `amdgpu_dm_i2c_xfer()` returns `-EIO` identically for a NULL ddc_pin, a failed START and a failed address ACK, so the errno carries no diagnostic information here.

## Root cause

`hw_ddc.c:84-87` reads the pull-down enables:

```c
REG_GET_3(gpio.MASK_reg,
        DC_GPIO_DDC1DATA_PD_EN, &ddc_data_pd_en,
        DC_GPIO_DDC1CLK_PD_EN, &ddc_clk_pd_en,
        AUX_PAD1_MODE, &aux_pad_mode);
```

All DDC lines, VGA included, use `DDC_MASK_SH_LIST_COMMON` (`ddc_regs.h:96-102`), which supplies DDC1 field positions. That is fine for DDC1-6, whose register layouts match. It is not fine for VGA:

```
$ grep -c 'DC_GPIO_DDCVGA_MASK__DC_GPIO_DDCVGACLK_PD_EN' dcn_2_0_0_sh_mask.h
0
```

`DC_GPIO_DDCVGA_MASK` has no `CLK_PD_EN` field. The read therefore returns 0 unconditionally, the guard at `hw_ddc.c:96`

```c
if (!ddc_data_pd_en || !ddc_clk_pd_en) {
```

is always true, and the VGA branch added by `c0b2753f5db2` re-asserts the SDA pull-down on every open:

```c
if (hw_gpio->base.en == GPIO_DDC_LINE_DDC_VGA) {
    // bit 4 of mask has different usage in some cases
    REG_SET(gpio.MASK_reg, regval, DC_GPIO_DDC1DATA_PD_EN, 1);
} else {
    ...
}
```

Nothing in `hw_ddc.c` clears it again.

Downstream, this line can only use DC's software engine — `link == NULL` forces `hw_supported = false` at `link_ddc.c:131-134`, and `GPIO_DDC_LINE_DDC_VGA == 6` is not `< res_cap->num_ddc == 6` (`dcn20_resource.c:691`). `dce_i2c_sw.c:298-339 start_sync_sw` then releases SDA, reads it back at `:316`, never sees it rise, and gives up after `I2C_SW_RETRIES = 10` — before any address bit is transmitted. That is precisely the observed address-independent failure.

The intent of `c0b2753f5db2` is not in dispute: it correctly recognised that bit 4 differs on this line and stopped writing it. The issue is only that the corresponding read was not given the same treatment.

## Hardware evidence

Read-only register dump via `amdgpu_regs`, GPU idle, nothing written:

| line | `MASK` value | bit 4 `CLK_PD_EN` | bit 12 `DATA_PD_EN` |
|---|---|---|---|
| DDCVGA (`0x5DA8`) | `0xcf401000` | **0** | **1** |
| DDC1, DDC2 (DC opens these normally) | `0xcf411010` | 1 | 1 |
| DDC3, DDC5 (no consumer, never touched) | `0xcf400000` | 0 | 0 |

Only the VGA line is asymmetric, and that asymmetry is exactly what the code path above produces: the lines DC opens normally have both bits set, the lines nothing touches have neither.

## The pads are wired and the device responds

This rules out "nothing is connected". Clearing `MASK` bit 12, claiming the pads and bit-banging i2c from userspace over the same four `DC_GPIO_DDCVGA_{MASK,A,EN,Y}` registers reaches a device the kernel path cannot reach at any address:

```
Device ACKed at 0x28 (Y=0x101, 2.5 ms per byte)
```

`Y = 0x101` with the pads claimed means both SCL and SDA float high, i.e. external pull-ups are present. Register read/write round-trips to the device succeed.

Worth noting for anyone reproducing: `Y` only reports real line levels once the pads are **claimed**. Read unclaimed, it returns 0 on every line regardless of what is attached, which makes a naive register dump look like an empty bus.

The device is the board's RGB controller, which this card's VBIOS declares in `firmwareinfo` at `+0x30` as `02 97 50 00` — `board_i2c_feature_id = 0x02`, `board_i2c_feature_gpio_id = 0x97` (resolving to `DC_GPIO_DDCVGA` via `gpio_pin_lut`), `board_i2c_feature_slave_addr = 0x50` 8-bit = 7-bit `0x28`.

Tool and full analysis: https://github.com/Yimura/nitroglow-linux

## Regression status: none — this has never worked

- The `DC_GPIO_DDC1CLK_PD_EN` read and the guard consuming it date to the original DC import, `4562236b3bc0` (2017).
- `b81e5aa39f66` (2018) only refactored the macro into `DDC_MASK_SH_LIST_COMMON`; no semantic change.
- Before `c0b2753f5db2`, DC wrote bit 4 on the VGA line too — but since that bit is undefined on `DC_GPIO_DDCVGA_MASK` and reads back 0 on this hardware, the guard would have remained true then as well.

That last point is inferred from the current register readback plus the missing field definition, not observed on a pre-2022 kernel. I can boot an older kernel and confirm directly if the distinction matters.

## Possible fix

Deferring to the maintainers on the right shape. The minimal change consistent with `c0b2753f5db2` would be to mirror its special case on the read side, so only `ddc_data_pd_en` is considered for `GPIO_DDC_LINE_DDC_VGA`.

Giving the VGA line a mask list without `CLK_PD_EN` is the obvious alternative, but note that `DDC_MASK_SH_LIST_DCN2_VGA` currently embeds `DDC_MASK_SH_LIST_COMMON` (`ddc_regs.h:116-121`) and so inherits the field regardless — switching lists alone would not help.

I have not submitted a patch and am not planning to. Happy to test any patch on this hardware and report back.

## Related, separate

`amdgpu_i2c_init()` is only called in the `else` arm of `if (adev->is_atom_fw)` (`amdgpu_device.c:3987-4007`), so it never runs on Vega and later. As a result `amdgpu_atombios_oem_i2c_init()` — which produces the adapter name `AMDGPU i2c bit bus OEM 0x97` — is unreachable on those ASICs, and the legacy `GPIO_I2C_Info` table it parses does not exist in an atomfirmware image anyway. That is an enablement gap rather than a bug, so it is not filed here; happy to open a separate issue if it is of interest.

---

## Before filing

- Attach full `dmesg`
- Optionally include `drm.debug=0x1e` output covering an attempted transfer on the OEM bus
- Confirm the `i2c-13` bus number still matches on the running kernel (`i2cdetect -l`)
