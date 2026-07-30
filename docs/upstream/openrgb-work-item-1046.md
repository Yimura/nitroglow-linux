# Draft comment for OpenRGB work_item 1046

*Target: https://gitlab.com/CalcProgrammer1/OpenRGB/-/work_items/1046 — not yet posted.*

---

## Sapphire RX 5700 XT Nitro+ (Navi 10): the VBIOS names the RGB device, and the line is already exposed — but unusable

Adding data for the Navi 10 case, since it turns out this card does **not** fit the "RGB chip is on an i2c bus the kernel never exposes" summary — the bus *is* exposed, and the failure is downstream of that.

**System:** Sapphire RX 5700 XT Nitro+, `1002:731F` subsystem `1DA2:E409`, kernel 7.1.5, OpenRGB 1.0rc3, Arch.

### The card's own firmware states where the MCU is

`firmwareinfo` (atomfirmware master data table index 4), rev 3.3, bytes at `+0x30`:

```
02 97 50 00
^  ^  ^
|  |  +-- board_i2c_feature_slave_addr = 0x50   8-bit wire format -> 7-bit 0x28
|  +----- board_i2c_feature_gpio_id    = 0x97
+-------- board_i2c_feature_id         = 0x02   "OEM i2c device present"
```

`board_i2c_feature_id == 0x2` is the same test DC applies at `dc/bios/bios_parser2.c:1934`. The 8-bit reading of the address field follows the convention documented for its sibling field in `amdgpu_ras_eeprom.c:184-194`.

**7-bit `0x28` is exactly `SAPPHIRE_NITRO_GLOW_V3_ADDR`.** So OpenRGB's constant and the board's firmware agree, independently.

gpio id `0x97` resolves through `gpio_pin_lut` (master index 12) entry 12:

```
a9 5d 00 00 08 08 97 00
data_a_reg_index = 0x5DA9, gpio_bitshift = 8, gpio_mask_bitshift = 8, gpio_id = 0x97
```

`0x5DA9` = `DCN_BASE__INST0_SEG2` (`0x34C0`, `navi10_ip_offset.h:269`) + `mmDC_GPIO_DDCVGA_A` (`0x28E9`). Subtracting the same base from all 13 LUT entries yields a named DCN2.0 register with zero misses, so the identification does not rest on any assumption about ATOM's lane-mux convention. `GPIO_DDC_LINE_I2C_PAD` is not a possibility on this ASIC — there is no `mmDC_GPIO_I2CPAD_*` block in `dcn_2_0_0_offset.h`, and `hw_translate_dcn20.c:178` records it as `case REG(DC_GPIO_I2CPAD_A): not exit`.

This also independently corroborates the Windows side: `ADL_DL_I2C_LINE_OEM == 1` (`dependencies/display-library/include/adl_defines.h:962`), and `i2c_smbus_amdadl.cpp:148` hardcodes `pI2C->iLine = 1` with the comment *"location of the Aura chip"*. Windows reaches this MCU over the OEM line. Same line, three sources.

### Independent confirmation from Sapphire's own software

Reversing TriXX 11.2.0 gives the same answer by a completely separate route. Its managed assembly (recovered from PE resource RCDATA 132, obfuscated with ConfuserEx) holds the Glow V3 control with `address = 40` (`0x28`) and a line field initialised to `1`, marshalled through a native bridge into `ADLI2C.iLine`:

```
managed field initializer = 1, alongside address = 40 = 0x28
  -> native bridge (api id 40 read / 41 write)
  -> SetLine: *(byte *)(this + 0x18) = arg
  -> ADLI2C.iLine
  -> ADL_Display_WriteAndReadI2C
```

`ADL_DL_I2C_LINE_OEM == 1`. So TriXX drives the Glow over the **OEM line** — the same line the VBIOS says carries a device at `0x28`. Two independent sources, one from the board firmware and one from the vendor's own tool, agree on both the line and the address.

Also confirmed from TriXX's device table: subsystem `0xE409` maps to `Glow = V3` (traced through the ConfuserEx dispatcher; conditioned on PCI revision `0xC1`, which this card is). Across all 140 return blocks in that table, `V1` never occurs on a Navi part — all 32 Navi entries are V3 or None. OpenRGB's choice of the V3 controller for this device is correct, and the 11 registers on TriXX's Glow V3 control match OpenRGB's V3 register map exactly — so the protocol implementation is validated too, independently of whether the bus can be reached.

For completeness on the "is it computed at runtime?" question: it is not. TriXX resolves 61 ADL entry points, of which exactly one is an `ADL_Display_*` function (`WriteAndReadI2C`), so the API needed to enumerate displays and derive a line is not even bound. The line is a compile-time literal, assigned in five places, all literals.

### Why it still doesn't work on Linux

On kernel ≥ 6.15 that line **is** exposed, as `AMDGPU DM i2c OEM bus` — `i2c-13` on this host, which `i2c_amd_gpu.h:22` already whitelists. Detection nevertheless fails, and `0x28` NAKs. So does `0x50`, and so does every other address, which is the tell: the failure is address-independent.

Two mechanisms are in play, one confirmed present, neither yet proven fatal:

1. **`amdgpu_i2c_init()` is never called on atomfirmware ASICs.** `amdgpu_device.c:3987-4007` calls it only in the `else` arm of `if (adev->is_atom_fw)`. Consequence for OpenRGB specifically: the whitelist entry `"AMDGPU i2c bit bus OEM 0x97"` at `i2c_amd_gpu.h:23` is **currently unreachable on every Vega-and-later card**, because the only code that produces that adapter name (`amdgpu_atombios_oem_i2c_init()` → `amdgpu_i2c.c:192-193`) is never reached. Worth knowing before anyone debugs against that string.
2. **DC's software i2c engine looks broken on the VGA line.** The OEM line can only ever use DC's SW bit-bang engine (`link == NULL` forces `hw_supported = false` at `link_ddc.c:131-134`; `GPIO_DDC_LINE_DDC_VGA == 6` is not `< res_cap->num_ddc == 6`). A read-only register dump on this host shows:

   | line | `MASK` value | bit 4 `CLK_PD_EN` | bit 12 `DATA_PD_EN` |
   |---|---|---|---|
   | DDCVGA (OEM, `0x97`) | `0xcf401000` | **0** | **1** |
   | DDC1 / DDC2 (DC opens these) | `0xcf411010` | 1 | 1 |
   | DDC3 / DDC5 (nothing touches) | `0xcf400000` | 0 | 0 |

   The asymmetry on DDCVGA is the signature of the `GPIO_DDC_LINE_DDC_VGA` case at `hw_ddc.c:97-99`, which sets *only* the SDA pull-down. Its guard at `hw_ddc.c:96` can never evaluate false, because `DDC_MASK_SH_LIST_COMMON` (`ddc_regs.h:96-102`) makes it read bit 4 of `DC_GPIO_DDCVGA_MASK` — a bit that register does not define. So the internal SDA pull-down is re-asserted on every open and cleared nowhere in `hw_ddc.c`. `dce_i2c_sw.c:298-339 start_sync_sw` then releases SDA, reads it back at `:316`, and gives up after `I2C_SW_RETRIES = 10` — before any address byte goes out.

### What is *not* established

Whether the MCU is actually on those pads. `DC_GPIO_DDCVGA_Y` reads `0`, but so does every other `Y` register including DDC2's with a monitor attached — no pad is claimed at rest, so `Y` does not report external line levels. And `amdgpu_dm_i2c_xfer()` (`amdgpu_dm.c:9177`) returns `-EIO` identically for NULL ddc_pin, failed START, and address NAK, so `i2cget` cannot discriminate. The internal pull-downs are also typically weak enough that a board pull-up would dominate, so mechanism 2 is *present* but not demonstrated *fatal*.

Concretely: "empty bus" and "driver defect" both remain consistent with everything observed.

### Suggested takeaways for OpenRGB

* **Do not relax `is_amd_gpu_i2c_bus()`** on the strength of this. `0x28` was probed individually on every amdgpu-exposed bus on this host — `i2c-7/8` (SMU 0/1), `i2c-9..12` (DM i2c hw bus 0-3), `i2c-13` (DM OEM bus) — and all are silent. Widening the whitelist would gain nothing here.
* The `"AMDGPU i2c bit bus OEM 0x97"` whitelist entry is dead code on Vega+ until the kernel side is fixed (mechanism 1).
* `SAPPHIRE_NAVI10_NITRO_PLUS_SUB_DEV1 = 0xE409` and `SAPPHIRE_NITRO_GLOW_V3_ADDR = 0x28` are **confirmed correct** by the card's firmware. The detector is right; it just cannot reach the device.

A proposed kernel patch for mechanism 1 exists but is **untested — not built, not applied, not loaded** — so it is deliberately not offered here as a fix, only as the cheapest experiment that would distinguish the two hypotheses, by providing a second independent transfer path to the same pads.
