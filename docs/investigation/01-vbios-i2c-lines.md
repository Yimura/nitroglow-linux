# VBIOS track — every i2c line on the Sapphire RX 5700 XT Nitro+ (0000:0e:00.0)

Working log + results. Card: `1002:731F` Navi 10, subsystem `1DA2:E409`.
Kernel 7.1.5-arch1-2. Nothing was built, applied, loaded or flashed.

Legend used throughout: **[V]** = verified from bytes/source I read; **[I]** = inferred.

---

## TL;DR

* The VBIOS declares **7** i2c pin pairs (`gpio_pin_lut` entries with `I2C_HW_CAP`):
  ATOM i2c ids `0x90 0x91 0x92 0x93 0x94 0x95 0x97`, mapping 1:1 onto
  `DC_GPIO_DDC1..DDC6` and `DC_GPIO_DDCVGA`. **[V]**
* 4 are claimed by display connectors, 1 is the OEM line, **2 are unclaimed and
  have no `/dev/i2c-*` node at all** (`0x92`/DDC3, `0x94`/DDC5). **[V]**
* **Candidate for the Nitro Glow MCU: ATOM i2c id `0x97` = `DC_GPIO_DDCVGA`.**
  The VBIOS names it outright: `firmwareinfo.board_i2c_feature_gpio_id = 0x97`
  with `board_i2c_feature_slave_addr = 0x50`, which is an 8-bit wire-format
  address per AMD's own kernel comment → **7-bit `0x28`**, exactly OpenRGB's
  Nitro Glow V3 address. **[V]**
* That line is *already* exposed as `i2c-13` "AMDGPU DM i2c OEM bus", and `0x28`
  is silent there. **Whether that means the bus is empty or that DC's transfer
  path is broken is NOT resolved** — see §8.2. A read-only register dump (§8.1)
  proved DC leaves an internal SDA pull-down asserted on this line through a
  guard that can never evaluate false, and killed two competing theories, but it
  could not read the external line state.
* The proposed patch adds a second, independent MMIO bit-banging adapter on the
  same pins, named exactly what OpenRGB already whitelists. It closes a real
  gap (`amdgpu_i2c_init()` is never called at all on atomfirmware ASICs) and it
  is the cheapest available experiment that discriminates the two hypotheses.
  See §9.

---

## 1. Obtaining the VBIOS

DRI debugfs index for the AMD card: `/sys/kernel/debug/dri/` contains symlinks
`0 -> 0000:0f:00.0` (NVIDIA), `1 -> 0000:0e:00.0`, `128 -> 0000:0f:00.0`,
`129 -> 0000:0e:00.0`. Modern debugfs also exposes the PCI-address directory
directly, so I used the unambiguous path.

Both routes were taken:

| file | route | size | sha256 |
|---|---|---|---|
| `vbios/vbios-debugfs.rom` | `/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_vbios` | 59392 (0xE800) | `2dadbd76638bae92e77d2b511ce4f9530333d548f20d8e5f2ebd31ef61f06006` |
| `vbios/vbios-pcirom.rom` | `echo 1 > .../0000:0e:00.0/rom; cat rom` | 103424 (0x19400) | `c6a12d138ea7d97c9e82771db423854c2c52f670b377bff282a005a263be0d3a` |

Sanity checks, all passing **[V]**:

* `0x00`: `55 AA`, size byte at `0x02` = `0x74` → 116 × 512 = **59392**, exactly
  the debugfs length. The debugfs dump is therefore complete, not truncated.
* ASCII `761295520` at offset 49 (the classic ATI/AMD ROM marker).
* `0x48` (`OFFSET_TO_ATOM_ROM_HEADER_POINTER`) = `0x02BE`; at `0x02BE+4` the
  signature `"ATOM"`.
* The PCI-ROM dump is the same legacy image (first 0xE800 bytes) followed by a
  second option ROM at `0xE800` containing `PCIR` + the string
  `GOP AMD REV:` (the UEFI GOP driver), length `0x56 × 512 = 0xAC00`.
  `0xE800 + 0xAC00 = 0x19400` = exactly the dump size. Also complete.

The two dumps differ in **4 bytes** only (0-based offsets 33, 50428, 53840,
53860). Independently identified as POST-time mutations: `0x21` in the option-ROM
header, `vram_usagebyfirmware+0x08` (`used_by_firmware_in_kb`, 0 vs 128),
`firmwareinfo+0x10` bit 0 = `ATOM_FIRMWARE_CAP_FIRMWARE_POSTED`, and
`firmwareinfo+0x24` (`mem_module_id`). **None of them falls inside the ROM
header, master data table, `gpio_pin_lut`, `displayobjectinfo`, or
`firmwareinfo+0x30..0x33`.** Spot-checked directly — the load-bearing bytes are
identical in both files **[V]**:

```
vbios-debugfs.rom 0xd270: 02975000     vbios-pcirom.rom 0xd270: 02975000
vbios-debugfs.rom 0xcae6: a95d000008089700   vbios-pcirom.rom 0xcae6: a95d000008089700
```

So every conclusion below holds for both the running driver's view and the
as-flashed ROM.

---

## 2. Correction to the task framing: there is no `GPIO_I2C_Info` table

The brief asked for `GPIO_I2C_Info` / `ATOM_GPIO_I2C_ASSIGNMENT`. Those are
**legacy atombios** names and **do not exist in this VBIOS** — this is an
*atomfirmware* image (`atom_rom_header_v2_2`, `format_revision = 2`). **[V]**

* `grep` for `GPIO_I2C_Info`, `ATOM_GPIO_I2C_ASSIGNMENT`,
  `atom_i2c_id_config_access` in
  `<linux>/drivers/gpu/drm/amd/include/atomfirmware.h` → zero hits.
  They live only in `drivers/gpu/drm/radeon/atombios.h`.
* The master data table slot that the legacy layout used for `GPIO_I2C_Info`
  (index 10, file offset `0x92D6`) is **`0x0000` — absent** in this ROM.

On atomfirmware the same information is split across three tables:

| what | table | struct |
|---|---|---|
| the physical pin pairs | `gpio_pin_lut` (index 12) | `atom_gpio_pin_lut_v2_1` / `atom_gpio_pin_assignment` (`atomfirmware.h:701-739`) |
| which connector owns which line | `displayobjectinfo` (index 22) | `display_object_info_table_v1_4` + `atom_i2c_record` (`atomfirmware.h:830-835`) |
| the board/OEM line + its slave address | `firmwareinfo` (index 4) | `atom_firmware_info_v3_3` `board_i2c_feature_*` (`atomfirmware.h:564-566`) |

Semantics of the id byte are unchanged from legacy, though
(`enum atom_gpio_pin_assignment_gpio_id`, `atomfirmware.h:711-714`):

| bits | mask | name | meaning |
|---|---|---|---|
| 7 | `0x80` | `I2C_HW_CAP` | 1 = this entry is an i2c pin pair; 0 = plain GPIO |
| 6:4 | `0x70` | `I2C_HW_ENGINE_ID_MASK` | HW i2c engine id (`>> 4`) |
| 3:0 | `0x0F` | `I2C_HW_LANE_MUX` | line/lane mux number |

Consumer confirming the decode: `bios_parser2.c:521-523`.

Parser: **`scripts/atom_i2c_parse.py`** (plain Python 3 stdlib, no deps).

```
python3 scripts/atom_i2c_parse.py vbios/vbios-debugfs.rom \
  --regmap <linux>/drivers/gpu/drm/amd/include/asic_reg/dcn/dcn_2_0_0_offset.h \
  --regbase 0x34C0
```

---

## 3. ATOM header chain (all offsets are file offsets in `vbios-debugfs.rom`)

```
0x0048  ATOM_ROM_HEADER pointer                = 0x02BE
0x02BE  atom_rom_header_v2_2
        +0x00 structuresize                    = 40 (== sizeof, self-check OK)
        +0x02 format_revision / +0x03 content  = 2 / 2
        +0x04 signature                        = "ATOM"
        +0x18 subsystem_vendor_id              = 0x1DA2   <- Sapphire
        +0x1A subsystem_id                     = 0xE409   <- Nitro+ 5700 XT
        +0x1C pci_info_offset                  = 0x02E8   (PCIR, 1002:731F)
        +0x1E masterhwfunction_offset          = 0x9218
        +0x20 masterdatatable_offset           = 0x92BE
0x92BE  atom_master_data_table_v2_1, structuresize=74 = 4 + 35*2, rev 2.1
        entry i at +0x04 + 2i
```

`subsystem_vendor_id`/`subsystem_id` matching `1DA2:E409` from the brief is a
strong independent check that both the dump and the struct offsets are right
**[V]**. Note `atomfirmware.h` is `#pragma pack(1)` (line 214), so all offset
arithmetic is naive-sequential — without that, `atom_rom_header_v2_2` would be
44 bytes and `+0x20` would be wrong.

### Master data table (35 entries)

| idx | +off | file | ptr | name | header |
|---|---|---|---|---|---|
| 0 | +04 | 92C2 | 0000 | utilitypipeline | absent |
| 1 | +06 | 92C4 | 0000 | multimedia_info | absent |
| 2 | +08 | 92C6 | D178 | smc_dpm_info | 200 B rev 4.5 |
| 3 | +0A | 92C8 | C39C | sw_datatable3 | 200 B rev 2.1 |
| **4** | **+0C** | **92CA** | **D240** | **firmwareinfo** | **108 B rev 3.3** |
| 5 | +0E | 92CC | C464 | sw_datatable5 | 52 B rev 2.1 |
| 6 | +10 | 92CE | C498 | lcd_info | 92 B rev 2.1 |
| 7 | +12 | 92D0 | C64E | sw_datatable7 | 514 B rev 5.3 |
| 8 | +14 | 92D2 | E55A | smu_info | 244 B rev 3.4 |
| 9 | +16 | 92D4 | C8A8 | sw_datatable9 | 473 B rev 2.5 |
| 10 | +18 | 92D6 | **0000** | *(legacy `GPIO_I2C_Info` slot)* | **absent** |
| 11 | +1A | 92D8 | C4F4 | vram_usagebyfirmware | 12 B rev 2.1 |
| **12** | **+1C** | **92DA** | **CA82** | **gpio_pin_lut** | **108 B rev 2.1** |
| 13 | +1E | 92DC | C500 | sw_datatable13 | 116 B rev 1.1 |
| 14 | +20 | 92DE | C850 | gfx_info | 88 B rev 2.5 |
| 15 | +22 | 92E0 | CAEE | powerplayinfo | 1674 B rev 12.0 |
| 16 | +24 | 92E2 | 0000 | sw_datatable16 | absent |
| 17 | +26 | 92E4 | C574 | sw_datatable17 | 20 B rev 2.1 |
| 18 | +28 | 92E6 | E53A | sw_datatable18 | 32 B rev 2.1 |
| 19 | +2A | 92E8 | 0000 | sw_datatable19 | absent |
| 20 | +2C | 92EA | E526 | sw_datatable20 | 20 B rev 2.2 |
| 21 | +2E | 92EC | 0000 | sw_datatable21 | absent |
| **22** | **+30** | **92EE** | **C588** | **displayobjectinfo** | **136 B rev 1.4** |
| 23 | +32 | 92F0 | C610 | indirectioaccess | 5 B rev 1.1 |
| 24 | +34 | 92F2 | E438 | umc_info | 89 B rev 3.3 |
| 25 | +36 | 92F4 | 0000 | sw_datatable25 | absent |
| 26 | +38 | 92F6 | 0000 | sw_datatable26 | absent |
| 27 | +3A | 92F8 | C616 | dce_info | 56 B rev 4.3 |
| 28 | +3C | 92FA | D2AC | vram_info | 4492 B rev 2.4 |
| 29 | +3E | 92FC | 0000 | sw_datatable29 | absent |
| 30 | +40 | 92FE | 0000 | integratedsysteminfo | absent |
| 31 | +42 | 9300 | 0000 | asic_profiling_info | absent |
| **32** | **+44** | **9302** | **E492** | **voltageobject_info** | **148 B rev 4.2** |
| 33 | +46 | 9304 | 0000 | sw_datatable33 | absent |
| 34 | +48 | 9306 | 0000 | sw_datatable34 | absent |

22 present, 13 absent. No pointer overruns the image.

---

## 4. `gpio_pin_lut` @ `0xCA82` — the complete pin list

Header: `6c 00 02 01` → structuresize 108, rev 2.1. `(108-4)/8 = 13` entries of
`atom_gpio_pin_assignment` (8 bytes: `u32 data_a_reg_index`, `u8 gpio_bitshift`,
`u8 gpio_mask_bitshift`, `u8 gpio_id`, `u8 reserved`).

### 4.1 Decoding `data_a_reg_index` — proven, not guessed

`data_a_reg_index` is an **absolute SOC15 dword register index**:

```
data_a_reg_index == DCN_BASE__INST0_SEG<base_idx> + mmDC_GPIO_<pad>_A
```

For Navi 10, `navi10_ip_offset.h:269` gives `DCN_BASE__INST0_SEG2 = 0x000034C0`,
and every `mmDC_GPIO_*` has `_BASE_IDX 2`. Subtracting `0x34C0` from each of the
13 values yields a **real, named register for all 13 entries with zero leftovers**
**[V]**:

| raw | −0x34C0 | register (`dcn_2_0_0_offset.h`) |
|---|---|---|
| 0x5DB5 | 0x28F5 | `mmDC_GPIO_HPD_A` (line 12876) |
| 0x5D91 | 0x28D1 | `mmDC_GPIO_DDC1_A` (12812) |
| 0x5D95 | 0x28D5 | `mmDC_GPIO_DDC2_A` (12820) |
| 0x5D99 | 0x28D9 | `mmDC_GPIO_DDC3_A` (12828) |
| 0x5D9D | 0x28DD | `mmDC_GPIO_DDC4_A` (12836) |
| 0x5DA1 | 0x28E1 | `mmDC_GPIO_DDC5_A` (12844) |
| 0x5DA5 | 0x28E5 | `mmDC_GPIO_DDC6_A` (12852) |
| 0x5DA9 | 0x28E9 | `mmDC_GPIO_DDCVGA_A` (12860) |

The driver arrives at the same numbers independently: `hw_translate_dcn20.c`
uses `#define REG(reg_name) BASE(mm##reg_name##_BASE_IDX) + mm##reg_name`, so
`REG(DC_GPIO_DDCVGA_A) == 0x34C0 + 0x28E9 == 0x5DA9`, and line 173-175 maps that
offset to `GPIO_DDC_LINE_DDC_VGA`. **[V]**

Note: the ATOM `lane_mux` (0..7) and DC's `enum gpio_ddc_line` are *different*
numbering spaces. `lane_mux 7` here does **not** mean
`GPIO_DDC_LINE_I2C_PAD (=7)`; DC selects the pad purely from the register offset
(`link_ddc.c:136-140` → `dal_gpio_create_ddc(..., clk_a_register_index, ...)` →
`offset_to_id()`), and `dal_ddc_get_line()` (`gpio_service.c:646-652`) returns
`GPIO_DDC_LINE_DDC_VGA (=6)` for this pin. `lane_mux 7` only ends up in
`hw_info.ddc_channel` (`link_ddc.c:130`). **[V]**

Two further checks that `lane_mux 7` cannot be an I2C_PAD line on this ASIC
**[V]**:

* `grep -c '^#define mmDC_GPIO_I2CPAD_' dcn_2_0_0_offset.h` → **0**. There is no
  I2C pad register block on DCN 2.0 at all.
* `hw_translate_dcn20.c:178` carries it as a comment:
  `/* case REG(DC_GPIO_I2CPAD_A): not exit */`, and `id_to_offset()` maps
  `GPIO_DDC_LINE_I2C_PAD` to `ASSERT_CRITICAL(false); result = false;`
  (`:224-227`, `:254-257`).

So exactly seven DDC pads exist (DDC1..DDC6 + DDCVGA), the LUT contains exactly
seven `I2C_HW_CAP` entries, and the register arithmetic assigns them one-to-one.
The identification rests on `data_a_reg_index`, **not** on any story about what
lane mux 7 conventionally means — I have no header that documents that
convention and do not rely on it.

Register-group stride, verified from `dcn_2_0_0_offset.h:12858-12864`:
`mmDC_GPIO_DDCVGA_MASK/_A/_EN/_Y = 0x28E8/0x28E9/0x28EA/0x28EB`, i.e.
**MASK = A−1, EN = A+1, Y = A+2**. Same stride for `DDC1..DDC6` and
`DC_GPIO_GENERIC`. Bit positions, from `dcn_2_0_0_sh_mask.h:49221-49245` (and
`:49008-49019` for DDC1): **clock = bit 0 (`0x00000001`), data = bit 8
(`0x00000100`)**, in the MASK, A, EN and Y registers alike. `DC` hardcodes the
same two bits (`hw_translate_dcn20.c:201`, `:231`). **[V]**

Two honest caveats on that derivation:

* The MASK/A/EN/Y stride is **not self-validating**. It holds for all the
  complete `DC_GPIO_*` quads, but the surrounding register space is not uniform
  (e.g. `mmDC_GPIO_TX12_EN` at `0x2915` is an EN with no matching A/Y, and
  `DC_GPIO_PAD_STRENGTH_1`, `DC_GPIO_AUX_CTRL_*` sit between groups). DC itself
  never derives — it uses static tables. The proposed patch therefore guards the
  derivation (see §9).
* ATOM gives only **one** bit shift per entry (`gpio_bitshift = 8`), which is
  the **data** bit. That clock is bit 0 is my **[I]**, supported by the fact
  that DC hardcodes exactly that pairing for every DDC line, and by
  `dcn_2_0_0_sh_mask.h` showing `..._DDC*CLK_*_MASK = 0x1` in all four registers
  of every DDC group. It is not stated by the VBIOS.

### 4.2 Full table

```
 idx  file_off  raw bytes                 data_a_reg  register             bitshift maskshift gpio_id  HW_CAP eng lane  consumer
   0  0xCA86    b5 5d 00 00 00 00 01 00   0x00005DB5  mmDC_GPIO_HPD_A          0        0      0x01    no     0    1   HPD1 (not i2c)
   1  0xCA8E    b5 5d 00 00 08 08 02 00   0x00005DB5  mmDC_GPIO_HPD_A          8        8      0x02    no     0    2   HPD2 (not i2c)
   2  0xCA96    b5 5d 00 00 10 10 03 00   0x00005DB5  mmDC_GPIO_HPD_A         16       16      0x03    no     0    3   HPD3 (not i2c)
   3  0xCA9E    b5 5d 00 00 18 14 04 00   0x00005DB5  mmDC_GPIO_HPD_A         24       20      0x04    no     0    4   HPD4 (not i2c)
   4  0xCAA6    b5 5d 00 00 1a 18 05 00   0x00005DB5  mmDC_GPIO_HPD_A         26       24      0x05    no     0    5   HPD5 (not i2c)
   5  0xCAAE    b5 5d 00 00 1c 1c 06 00   0x00005DB5  mmDC_GPIO_HPD_A         28       28      0x06    no     0    6   HPD6 (not i2c)
   6  0xCAB6    91 5d 00 00 08 08 90 00   0x00005D91  mmDC_GPIO_DDC1_A         8        8      0x90    YES    1    0   DP enum2  (path 1)
   7  0xCABE    95 5d 00 00 08 08 91 00   0x00005D95  mmDC_GPIO_DDC2_A         8        8      0x91    YES    1    1   DP enum1  (path 0)
   8  0xCAC6    99 5d 00 00 08 08 92 00   0x00005D99  mmDC_GPIO_DDC3_A         8        8      0x92    YES    1    2   -- NONE --
   9  0xCACE    9d 5d 00 00 08 08 93 00   0x00005D9D  mmDC_GPIO_DDC4_A         8        8      0x93    YES    1    3   HDMI enum2 (path 3)
  10  0xCAD6    a1 5d 00 00 08 08 94 00   0x00005DA1  mmDC_GPIO_DDC5_A         8        8      0x94    YES    1    4   -- NONE --
  11  0xCADE    a5 5d 00 00 08 08 95 00   0x00005DA5  mmDC_GPIO_DDC6_A         8        8      0x95    YES    1    5   HDMI enum1 (path 2)
  12  0xCAE6    a9 5d 00 00 08 08 97 00   0x00005DA9  mmDC_GPIO_DDCVGA_A       8        8      0x97    YES    1    7   OEM (firmwareinfo)
```

Entries 0-5 have `I2C_HW_CAP` clear — they are the six HPD pins in
`DC_GPIO_HPD_A`, not i2c lines. **7 i2c pin pairs exist**: `0x90 0x91 0x92 0x93
0x94 0x95 0x97`.

---

## 5. `displayobjectinfo` @ `0xC588` — which lines belong to connectors

Header: 136 B rev 1.4 → `display_object_info_table_v1_4`,
`supporteddevices = 0x0688`, `number_of_path = 4`.
`atom_display_object_path_v2` is **16 bytes** (7×u16 + 2×u8) — I initially used
20 and got garbage for paths 1-3; see [Dead ends](#dead-ends).

| path | file | connector | device_tag | encoder | records @ | `atom_i2c_record` | i2c_id | slave |
|---|---|---|---|---|---|---|---|---|
| 0 | 0xC590 | DISPLAY_PORT enum1 | 0x0008 (DFP1) | 0x2120 | 0xC5D0 | `01 04 91 00` | **0x91** | 0x00 |
| 1 | 0xC5A0 | DISPLAY_PORT enum2 | 0x0080 (DFP2) | 0x2220 | 0xC5D9 | `01 04 90 00` | **0x90** | 0x00 |
| 2 | 0xC5B0 | HDMI_TYPE_A enum1 | 0x0200 (DFP3) | 0x2121 | 0xC5E2 | `01 04 95 00` | **0x95** | 0x00 |
| 3 | 0xC5C0 | HDMI_TYPE_A enum2 | 0x0400 (DFP4) | 0x211E | 0xC5EB | `01 04 93 00` | **0x93** | 0x00 |

Each path also carries one `ATOM_HPD_INT_RECORD_TYPE` (`02 04 xx 00`) referencing
HPD pins 2, 1, 6, 4 respectively.

**Validated against live hardware, not just self-consistent** **[V]**:
`/sys/class/drm` shows card1 with exactly `card1-DP-4`, `card1-DP-5`,
`card1-HDMI-A-2`, `card1-HDMI-A-3` — 2× DP + 2× HDMI, matching the ROM's
connector types and count precisely, and `supporteddevices = 0x0688` =
DFP1|DFP2|DFP3|DFP4 matches the four `device_tag`s. (This contradicts the usual
"3× DP + 1× HDMI" marketing spec for a 5700 XT Nitro+, but the hardware agrees
with the ROM, so the ROM parse is right.)

### Non-display i2c consumers (task step 4)

* `voltageobject_info` @ `0xE492` (rev 4.2, 8 objects): types `0x01/0x02/0x04/0x05`,
  modes `0x03` (`SVID2`) and `0x07`. **No** `VOLTAGE_TYPE_GENERIC_I2C_*`
  (`0x11..0x1A`) object is present, so no VRM sits on an ATOM i2c line here.
  Raw bytes are in the parser output. **[V]**
* `dce_info` rev 4.3 has no OEM/i2c field (`grep -i oem_i2c atomfirmware.h` is
  empty for every revision). **[V]**
* The only non-display i2c consumer named anywhere in this ROM is the
  `firmwareinfo` board-i2c feature — section 6.

---

## 6. `firmwareinfo` @ `0xD240` — the OEM line, named by the VBIOS

Header: 108 B rev **3.3**. `board_i2c_feature_*` sits at `+0x30..+0x33`
(header 4 + 6×u32 + 4×u16 + 4 + 2×u32 = 0x30; identical in
`atom_firmware_info_v3_1` … `v3_5`).

```
file 0xD270:  02 97 50 00
              │  │  │  └── reserved3 / ras_rom_i2c_slave_addr = 0x00
              │  │  └───── board_i2c_feature_slave_addr       = 0x50
              │  └──────── board_i2c_feature_gpio_id          = 0x97
              └─────────── board_i2c_feature_id               = 0x02
```

* `board_i2c_feature_id == 0x2` is exactly the test DC performs
  (`bios_parser2.c:1934-1939` for the v3_3 reader) to set
  `fw_info.oem_i2c_present = true` and `oem_i2c_obj_id = 0x97`. The mere
  existence of `i2c-13` on this host empirically re-confirms this byte read,
  because `dm_oem_i2c_hw_init()` bails out otherwise. **[V]**
* `board_i2c_feature_gpio_id = 0x97` → `gpio_pin_lut` entry 12 →
  `DC_GPIO_DDCVGA`. **[V]**

### `board_i2c_feature_slave_addr = 0x50` is an 8-bit address → 7-bit `0x28`

This is the crux, so here is the chain of evidence rather than an assertion:

1. The byte immediately after it, `ras_rom_i2c_slave_addr` (`+0x33` in
   `atom_firmware_info_v3_4`), is read by
   `amdgpu_atomfirmware_ras_rom_addr()` (`amdgpu_atomfirmware.c:801-803`) and
   consumed in `amdgpu_ras_eeprom.c:184-194`, where AMD's own comment states:
   *"The address given by VBIOS is an 8-bit, wire-format address, i.e. the most
   significant byte. Normalize it to a 19-bit EEPROM address. Remove the device
   type identifier and make it a 7-bit address"* — implemented as
   `i2c_addr = (i2c_addr & 0x0F) >> 1;`. So the ATOM `firmwareinfo` i2c
   slave-address convention **is 8-bit wire format**. **[V]**
2. AMD's userspace API agrees: `ADLI2C.iAddress` is the shifted address, and
   TriXX does `iAddress = addr << 1` (brief fact 6).
3. DC's own bit-bang engine treats its internal address as 7-bit and shifts on
   the wire: `dce_i2c_sw.c:456`
   `request.address = (payload->address << 1) | (write ? 0 : 1);` **[V]**

`0x50 >> 1 = 0x28` — the Nitro Glow V3 address in
`<OpenRGB>/Controllers/SapphireGPUController/SapphireGPUControllerDetect.cpp:75`.

Caveat, stated plainly: nothing in the kernel reads
`board_i2c_feature_slave_addr` itself (grep confirms), so point 1 is an argument
from the *sibling field in the same struct*, not from a direct consumer. A raw
7-bit `0x50` would be a plausible EEPROM. I rate the 8-bit reading as strongly
supported but formally **[I]**.

### Independent confirmation that the MCU lives on the OEM line

`<OpenRGB>/dependencies/display-library/include/adl_defines.h:962`:

```c
#define ADL_DL_I2C_LINE_OEM                0x00000001
#define ADL_DL_I2C_LINE_OD_CONTROL         0x00000002
#define ADL_DL_I2C_LINE_OEM2               0x00000003
...
```

and `<OpenRGB>/i2c_smbus/Windows/i2c_smbus_amdadl.cpp:148`:

```cpp
pI2C->iLine = 1; //location of the Aura chip
```

`iLine` is **not** a DDC index — `1` is `ADL_DL_I2C_LINE_OEM`. So OpenRGB on
Windows reaches the Sapphire RGB MCU through the *OEM i2c line*, which this
VBIOS declares to be `0x97`. **[V]**

And `<OpenRGB>/i2c_smbus/i2c_amd_gpu.h:23` whitelists the literal bus name
`"AMDGPU i2c bit bus OEM 0x97"` — the same `0x97`. **[V]**

---

## 7. Mapping ATOM lines to `/dev/i2c-*`

How each currently-registered AMD bus comes to exist:

* **`AMDGPU DM i2c hw bus N`** — `amdgpu_dm.c:9290` `create_i2c(link->ddc, false)`
  per DC link, named with `ddc_service->link->link_index`
  (`amdgpu_dm.c:9240-9241`). Link index order follows the
  `displayobjectinfo` path order.
* **`AMDGPU DM i2c OEM bus`** — `amdgpu_dm.c:3040-3061 dm_oem_i2c_hw_init()`,
  from `dc->res_pool->oem_device`, created in `dcn20_resource.c:2802-2808`
  with `id.id = fw_info.oem_i2c_obj_id (=0x97)`, `id.type = OBJECT_TYPE_GENERIC`,
  `link = NULL`.
* **`AMDGPU DM aux hw bus N`** — DDC-over-AUX on a DP link, not a GPIO i2c line
  at all. (Minor correction to brief fact 1: `card1-DP-4`'s `i2c-14` is
  `AMDGPU DM aux hw bus 0`, so the `i2cget -y 14 0x50` → `0x02` control test is
  an EDID read over AUX and says nothing about the GPIO bit-bang path.)
* **`AMDGPU SMU 0/1`** — the SMU's own i2c controller (SMUIO block), registered
  by the powerplay/swsmu layer. **These have no `gpio_pin_lut` entry**; they are
  not ATOM DC-GPIO lines and cannot be reached through them.

Resulting map:

| ATOM i2c id | pin pair (reg A) | claimed by | `/dev` node | bus name |
|---|---|---|---|---|
| `0x90` | `DC_GPIO_DDC1` (0x5D91) | DP enum2 / path 1 → link 1 | **i2c-10** | `AMDGPU DM i2c hw bus 1` |
| `0x91` | `DC_GPIO_DDC2` (0x5D95) | DP enum1 / path 0 → link 0 | **i2c-9** | `AMDGPU DM i2c hw bus 0` |
| `0x92` | `DC_GPIO_DDC3` (0x5D99) | **nothing** | **none** | — **HIDDEN** |
| `0x93` | `DC_GPIO_DDC4` (0x5D9D) | HDMI enum2 / path 3 → link 3 | **i2c-12** | `AMDGPU DM i2c hw bus 3` |
| `0x94` | `DC_GPIO_DDC5` (0x5DA1) | **nothing** | **none** | — **HIDDEN** |
| `0x95` | `DC_GPIO_DDC6` (0x5DA5) | HDMI enum1 / path 2 → link 2 | **i2c-11** | `AMDGPU DM i2c hw bus 2` |
| `0x97` | `DC_GPIO_DDCVGA` (0x5DA9) | `firmwareinfo` OEM feature | **i2c-13** | `AMDGPU DM i2c OEM bus` |
| — | DDC-over-AUX, DP link 0 | `card1-DP-4` | i2c-14 | `AMDGPU DM aux hw bus 0` |
| — | DDC-over-AUX, DP link 1 | `card1-DP-5` | i2c-15 | `AMDGPU DM aux hw bus 1` |
| — | SMUIO i2c master (no ATOM entry) | SMU | i2c-7 / i2c-8 | `AMDGPU SMU 0/1` |

The path→link_index assignment is **[I]** (DC assigns link indices in
`displayobjectinfo` path order, and 4 paths ↔ 4 `hw bus 0..3` is consistent, but
I did not read a per-bus register dump to prove which of i2c-9..12 is which).
The set `{0x90,0x91,0x93,0x95} ↔ {i2c-9..12}` is **[V]**.

**ATOM i2c lines with no `/dev` node: `0x92` (DDC3) and `0x94` (DDC5).**

---

## 8. Candidate line and why the already-exposed OEM bus is silent

**Candidate: ATOM i2c id `0x97`, `DC_GPIO_DDCVGA`** (A=`0x5DA9`, MASK=`0x5DA8`,
EN=`0x5DAA`, Y=`0x5DAB`; clk bit 0, data bit 8).

Evidence, in descending strength:

1. The VBIOS names it and gives its slave address: `02 97 50 00` at `0xD270`
   → OEM feature present, on gpio id `0x97`, device at 8-bit `0x50` = 7-bit
   `0x28`, the exact OpenRGB Nitro Glow V3 address. **[V]** bytes, **[I]** the
   8-bit reading (§6).
2. `ADL_DL_I2C_LINE_OEM == 1` and OpenRGB's Windows path uses `iLine = 1`
   "location of the Aura chip" → Windows reaches the MCU via the OEM line. **[V]**
3. OpenRGB's Linux whitelist hardcodes `"AMDGPU i2c bit bus OEM 0x97"`. **[V]**
4. It is the only i2c line in the ROM claimed by something that is not a display
   connector. **[V]**

Distant alternates: `0x92` (DDC3) and `0x94` (DDC5) — HW-capable, unclaimed, no
`/dev` node, but **not named by anything in the VBIOS**. Worth probing only if
`0x97` is exhausted.

### The awkward fact, and what it does and does not mean

`0x97` is already exposed as `i2c-13`, and it is silent. I reproduced brief
fact 3 and added a discriminating read:

```
i2cget -y 13 0x28  -> Error: Read failed        (reproduces brief fact 3)
i2cget -y 13 0x50  -> Error: Read failed        (new)
i2cget -y 14 0x50  -> 0xff                      (control, AUX/EDID, works)
```

Neither candidate address answers on `i2c-13`. Note that a failure here is
**address-independent** if the START condition never completes: `start_sync_sw()`
releases SDA and reads it back (`dce_i2c_sw.c:314-319`), giving up after
`I2C_SW_RETRIES` before a single address bit is transmitted. So identical
silence at `0x28` and `0x50` is what *both* the "nothing is wired" and the
"START never completes" hypotheses predict, and it discriminates nothing on its
own. See §8.1 and §8.2.

What *is* verified about `i2c-13`'s path:

* It can **only ever** use DC's software bit-bang engine, never the hardware
  i2c engine — double-gated. Gate 1: `link == NULL` for the OEM ddc_service
  forces `hw_info.hw_supported = false` (`link_ddc.c:131-134`). Gate 2:
  `acquire_i2c_hw_engine()` requires `line < pool->res_cap->num_ddc`
  (`dce_i2c_hw.c:454-458`), and `num_ddc = 6` for DCN2.0
  (`dcn20_resource.c:691`) while this pin is `GPIO_DDC_LINE_DDC_VGA = 6`.
  So `dce_i2c_submit_command()` always falls through to
  `dce_i2c_engine_acquire_sw()` (`dce_i2c.c:73-83`). **[V]**
* That SW engine is `dce_i2c_sw.c`, driving the pads through DC's GPIO
  abstraction (`dal_gpio_set_value`/`get_value`), with
  `clock_delay = max(1000/speed, 12)` → `clock_delay_div_4 = 3 µs` for
  `cmd.speed = 100` (`amdgpu_dm.c` sets `cmd.speed = 100` literally). **[V]**

Also note that `i2cget`'s error text cannot discriminate causes:
`amdgpu_dm_i2c_xfer()` initialises `result = -EIO` and returns it identically
for a NULL `ddc_pin`, a failed START, and a failed address ACK
(`amdgpu_dm.c:9177`, `:9179-9180`). "Error: Read failed" is therefore consistent
with every hypothesis. **[V]**

And the brief's control test does not exonerate DC's i2c: `i2c-14` is DP **AUX**,
a different engine entirely. `i2c-9..12` use the DCE **hardware** i2c engine
(`GPIO_MODE_HARDWARE`, `dce_i2c_hw.c:468`). **`i2c-13` is the only bus on this
machine that goes through `dce_i2c_submit_command_sw()`**
(`GPIO_MODE_FAST_OUTPUT`, `dce_i2c_sw.c:362`). DC's software bit-bang path has
never been exercised successfully on this host, on any line. **[V]**

### 8.1 Register readback (read-only, new evidence)

Read via `dd` on `/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs` (size 524288,
matching `rmmio_size`; `RREG32(reg)` reads byte offset `reg << 2`). GPU idle,
DP-4 connected, nothing written. Verbatim:

```
=== DDCVGA  (ATOM i2c id 0x97 - OEM / candidate) ===
  DC_GPIO_DDCVGA_MASK    dword 0x05da8 (byte 0x0176a0) = 0xcf401000
  DC_GPIO_DDCVGA_A       dword 0x05da9 (byte 0x0176a4) = 0x00000000
  DC_GPIO_DDCVGA_EN      dword 0x05daa (byte 0x0176a8) = 0x00000000
  DC_GPIO_DDCVGA_Y       dword 0x05dab (byte 0x0176ac) = 0x00000000
=== DDC2 (id 0x91) - DP enum1, monitor connected - reference ===
  DC_GPIO_DDC2_MASK      dword 0x05d94 (byte 0x017650) = 0xcf411010
  DC_GPIO_DDC2_A         dword 0x05d95 (byte 0x017654) = 0x00000000
  DC_GPIO_DDC2_EN        dword 0x05d96 (byte 0x017658) = 0x00000000
  DC_GPIO_DDC2_Y         dword 0x05d97 (byte 0x01765c) = 0x00000000
=== DDC1 (id 0x90) - DP enum2, nothing plugged - reference ===
  DC_GPIO_DDC1_MASK      dword 0x05d90 (byte 0x017640) = 0xcf411010
  DC_GPIO_DDC1_Y         dword 0x05d93 (byte 0x01764c) = 0x00000000
=== DDC3 (id 0x92) - HIDDEN, no consumer ===
  DC_GPIO_DDC3_MASK      dword 0x05d98 (byte 0x017660) = 0xcf400000
  DC_GPIO_DDC3_Y         dword 0x05d9b (byte 0x01766c) = 0x00000000
=== DDC5 (id 0x94) - HIDDEN, no consumer ===
  DC_GPIO_DDC5_MASK      dword 0x05da0 (byte 0x017680) = 0xcf400000
  DC_GPIO_DDC5_Y         dword 0x05da3 (byte 0x01768c) = 0x00000000
```

Field decode of the three distinct `MASK` values:

| bit | field (`dcn_2_0_0_sh_mask.h`) | DDCVGA `0xcf401000` | DDC1/DDC2 `0xcf411010` | DDC3/DDC5 `0xcf400000` |
|---|---|---|---|---|
| 0 | `..CLK_MASK` (pad claimed by SW) | 0 | 0 | 0 |
| 4 | `..CLK_PD_EN` | **0** | **1** | 0 |
| 8 | `..DATA_MASK` (pad claimed by SW) | 0 | 0 | 0 |
| 12 | `..DATA_PD_EN` (internal pull-down on SDA) | **1** | **1** | 0 |
| 16 | `AUX_PAD*_MODE` | **0** | 1 | 0 |
| 22 | `ALLOW_HW_*_PD_EN` | 1 | 1 | 1 |
| 24-27 | `..CLK_STR` | 0xF | 0xF | 0xF |
| 28-31 | `..DATA_STR` | 0xC | 0xC | 0xC |

**What this proves [V]:**

1. **DC opened the OEM line and left an asymmetric pull-down state.** DDCVGA has
   `DATA_PD_EN` set but `CLK_PD_EN` clear; the two lines DC opens normally
   (DDC1, DDC2) have **both** set; the two lines nothing ever touches (DDC3,
   DDC5) have **neither**. That is exactly and only the signature of the
   `GPIO_DDC_LINE_DDC_VGA` special case at `hw_ddc.c:97-99`, which sets *only*
   `DC_GPIO_DDC1DATA_PD_EN`. The predicted code path demonstrably ran.
2. **The guard around it can never be false on this line.** All DDC lines,
   including VGA, share `DDC_MASK_SH_LIST_COMMON` (`ddc_regs.h:96-102`), which
   uses the **DDC1** field positions — so
   `REG_GET_3(..., DC_GPIO_DDC1CLK_PD_EN, &ddc_clk_pd_en, ...)`
   (`hw_ddc.c:84-87`) reads **bit 4 of `DC_GPIO_DDCVGA_MASK`**. That register has
   no `CLK_PD_EN` field at all — `grep -c
   'DC_GPIO_DDCVGA_MASK__DC_GPIO_DDCVGACLK_PD_EN' dcn_2_0_0_sh_mask.h` → **0** —
   so `ddc_clk_pd_en` reads 0 forever, `if (!ddc_data_pd_en || !ddc_clk_pd_en)`
   (`hw_ddc.c:96`) is unconditionally true, and the internal SDA pull-down is
   re-asserted on **every** open and never cleared anywhere in `hw_ddc.c`.
   Observed bit 4 = 0 on DDCVGA confirms the read returns 0.
3. **The "pad stuck in AUX mode" theory is dead.**
   `AUX_PADVGA_MODE` (bit 16) is **clear** on DDCVGA. (It is *set* on DDC1/DDC2,
   consistent with those being DP-AUX-capable pads.)

**What this does NOT prove — stated plainly:**

`DC_GPIO_DDCVGA_Y = 0x00000000` is **uninformative**. Every `Y` register read
back `0`, including DDC2's, whose connector has a monitor attached. The pad-claim
bits (`CLK_MASK` bit 0 / `DATA_MASK` bit 8) are `0` on *all* lines at rest, i.e.
no pad is currently handed to the GPIO block, so `Y` is not reporting external
line levels. **I cannot tell from this readback whether external pull-ups exist
on the DDCVGA pins.** The hoped-for `Y = 0x101` ("something is wired") vs
`Y = 0x000` ("nothing there") discrimination does not work at rest, because
`Y = 0` is what an unclaimed pad reads regardless.

Whether an asserted internal pull-down is even *sufficient* to break the bus is
also unproven **[I]**: these internal pull-downs are typically weak (tens of kΩ)
and a normal 2.2k–10k external i2c pull-up would dominate. So point 1 above
establishes that the mechanism is **present**, not that it is **fatal**.

### 8.2 Are the two hypotheses distinguishable yet? No.

* **"Empty bus"** — nothing is wired to the DDCVGA pads, the VBIOS OEM record is
  vestigial or describes a device Sapphire moved elsewhere, and `0x28`/`0x50`
  correctly NAK.
* **"Driver defect"** — the device is there, and DC's SW bit-bang path (the only
  path this line can use, and a path never once validated on this host) fails
  before or during the address phase.

Everything observed is consistent with **both**. The register readback narrowed
the defect hypothesis to a specific, confirmed-present mechanism and killed two
competing ones, but it did not discriminate, because `Y` cannot be read at rest
and `-EIO` is returned identically for all failure modes. Deciding between them
requires either (a) driving the pads and reading `Y` back mid-transfer, which
means writing GPU registers — out of scope here; or (b) a second independent
transfer implementation, which is what the proposed patch provides.

---

## 9. Proposed patch

Written to **`kernel/amdgpu-atomfirmware-oem-i2c.diff`**. Not built, not applied, not loaded.

### The gap it closes (this is the point)

`amdgpu` already contains a bit-banging OEM-i2c registration path that produces
exactly the bus name OpenRGB whitelists — and Navi 10 never reaches it.
`amdgpu_i2c.c:218-235`:

```c
void amdgpu_i2c_init(struct amdgpu_device *adev)
{
	if (!adev->is_atom_fw) {
		if (!amdgpu_device_has_dc_support(adev)) {
			amdgpu_atombios_i2c_init(adev);
		} else {
			switch (adev->asic_type) {
			case CHIP_POLARIS10:
			case CHIP_POLARIS11:
			case CHIP_POLARIS12:
				amdgpu_atombios_oem_i2c_init(adev, 0x97);
				break;
			default:
				break;
			}
		}
	}
}
```

Three independent reasons it never runs here **[V]**:

1. `amdgpu_device.c:3987-4006` calls `amdgpu_i2c_init(adev)` **only** in the
   `else` arm of `if (adev->is_atom_fw)`. Navi 10 has `is_atom_fw = true`, so
   the function is never called at all.
2. Even if called, the inner `switch` handles only Polaris10/11/12.
3. Even if reached, `amdgpu_atombios_oem_i2c_init()`
   (`amdgpu_atombios.c:147-177`) walks the legacy `GPIO_I2C_Info` table, which
   is **absent** from this ROM (master index 10 = `0x0000`).

The bus name that path produces: `amdgpu_atombios.c:169`
`sprintf(stmp, "OEM 0x%x", i2c.i2c_id)` → `amdgpu_i2c.c:192-193`
`"AMDGPU i2c bit bus %s"` → **`AMDGPU i2c bit bus OEM 0x97`**, byte-for-byte the
string at `<OpenRGB>/i2c_smbus/i2c_amd_gpu.h:23`. So the patch is not
inventing an interface; it is extending an existing one to atomfirmware ASICs.

### What the patch does

1. `amdgpu_atomfirmware.c`: new
   `amdgpu_atomfirmware_get_bus_rec_for_gpio_pin()` — builds an
   `amdgpu_i2c_bus_rec` from one `atom_gpio_pin_assignment`, deriving
   MASK/EN/Y from `data_a_reg_index` ∓1/+1/+2 and using clk bit 0 /
   data bit `gpio_bitshift`.
2. `amdgpu_atomfirmware.c`: new `amdgpu_atomfirmware_oem_i2c_init()` — reads
   `firmwareinfo.board_i2c_feature_id`, requires `0x2`, takes
   `board_i2c_feature_gpio_id`, finds the matching `gpio_pin_lut` entry, and
   registers the adapter via the existing `amdgpu_i2c_create()`. Forces
   `hw_capable = false` so the i2c-algo-bit path is always taken (the ATOM
   hw-i2c command tables that `amdgpu_atombios_i2c_xfer()` needs do not exist in
   an atomfirmware image) — which also guarantees the `bit bus` bus name.
   Logs the decoded slave address on success.
3. `amdgpu_i2c.c`: route `is_atom_fw` ASICs to the new function.
4. `amdgpu_device.c`: call `amdgpu_i2c_init()` on both BIOS paths.

Nothing existing changes behaviour: for `!is_atom_fw` ASICs the code path is
identical, and for `is_atom_fw` ASICs without `board_i2c_feature_id == 0x2` the
new function returns immediately.

`RREG32(0x5DA9)` is a **direct** MMIO access, not the indirect PCIe path:
`0x5DA9 << 2 = 0x176A4 = 95,908` and this host reports
`register mmio size: 524288` (dmesg). So bit-banging is fast and takes no
locks beyond the adapter mutex. **[V]**

### How to verify it (once the user chooses to build it)

1. After loading the patched module, a **new** bus appears:

   ```
   $ for f in /sys/class/i2c-dev/*/name; do echo "$(basename $(dirname $f)): $(cat $f)"; done | grep -i amdgpu
   ...
   i2c-16: AMDGPU i2c bit bus OEM 0x97       <-- the new one
   ```

   Expect a `dev_info` line in `dmesg` as well:

   ```
   amdgpu 0000:0e:00.0: OEM i2c line 0x97: DC_GPIO A reg 0x05da9, slave 0x50 (7-bit 0x28)
   ```

   If the bus does **not** appear, the failure is in table parsing, and
   `board_i2c_feature_id != 0x2` or the `gpio_pin_lut` match is the thing to
   instrument.

2. Single-address read at `0x28` on the new bus (substitute the real number):

   ```
   $ i2cget -y 16 0x28
   ```

   * A byte back (any value) = the MCU ACKed → line confirmed, hand the bus to
     OpenRGB, whose whitelist already accepts this exact bus name so
     `SAPPHIRE_NAVI10_NITRO_PLUS_SUB_DEV1 = 0xE409` / Nitro Glow V3 should
     detect without further changes.
   * `Error: Read failed` = still no ACK. Then the bit-bang path is not the
     problem and the next steps are, in order: (a) read back
     `DC_GPIO_DDCVGA_MASK/A/EN/Y` before and after a transfer via
     `/sys/kernel/debug/dri/0000:0e:00.0/amdgpu_regs` to see whether the pads
     respond at all; (b) try the two hidden lines `0x92`/DDC3 and `0x94`/DDC5 by
     temporarily passing their gpio ids to the same helper; (c) reconsider the
     7-bit-`0x50` reading of `board_i2c_feature_slave_addr` and probe `0x50`.

   Do **not** run `i2cdetect` sweeps on the new bus — the ATOM tables show no
   VRM/thermal device on an i2c line, but that is an argument from absence.

3. Regression check that nothing else moved: the existing `i2c-7..15` names must
   be unchanged and `i2cget -y 14 0x50` must still succeed. (Note the returned
   byte varies — the brief recorded `0x02`, I observed `0xff` — because an
   address-only SMBus read returns whatever the EDID EEPROM's internal pointer
   happens to be sitting on. Success/failure is the signal, not the value.)

---

## Dead ends

Recorded so they are not repeated.

* **Looking for `GPIO_I2C_Info` / `ATOM_GPIO_I2C_ASSIGNMENT`.** Not in an
  atomfirmware image; master data table index 10 is `0x0000` here. The
  information is in `gpio_pin_lut` + `displayobjectinfo` + `firmwareinfo`.
* **My own parser bug: `atom_display_object_path_v2` is 16 bytes, not 20.**
  With a 20-byte stride, path 0 decoded plausibly and paths 1-3 decoded as
  `ENCODER/id0x20/enum2`, `UNKNOWN/id0x7a`, `UNKNOWN/id0x00` — i.e. *believable
  garbage*, and I nearly concluded the card had one connector with one i2c
  record. Recount: 7×u16 + 2×u8 = 16. Fixed; all four paths then decoded
  cleanly and matched the four DRM connectors. Cautionary tale for anyone
  extending the parser.
* **Expecting `dce_info` to hold the OEM i2c line.** It does not;
  `grep -i oem_i2c atomfirmware.h` is empty across all revisions.
* **`dce_i2c_oem_device_present()` (`dce_i2c.c:28-53`) cannot be used to
  confirm the slave address.** It compares `i2c_info.i2c_slave_address` against
  its argument, but the `OBJECT_TYPE_GENERIC` path in
  `bios_parser_get_i2c_info()` (`bios_parser2.c:404-411`) builds
  `dummy_record = {0}` with only `i2c_id` set, so `i2c_slave_address` is always
  `0`. The VBIOS's `0x50` never reaches it.
* **Assuming ATOM `lane_mux 7` == `GPIO_DDC_LINE_I2C_PAD (7)`.** It does not;
  DC derives the pad from the register offset, giving
  `GPIO_DDC_LINE_DDC_VGA (6)`. Different numbering spaces.
* **Assuming `RREG32(0x5DA9)` needs the indirect PCIe path.** Arithmetic slip on
  my side: `0x5DA9 << 2 = 0x176A4`, well inside the 512 KiB MMIO window.
* **No kernel source on the host.** `linux-headers 7.1.5.arch1-2` ships only
  `Kconfig` files under `drivers/gpu/drm/amd/`; no `atomfirmware.h`, no `.c`.
  Nothing was installed — the exact `v7.1.5` sources used are fetched read-only
  into `<linux>/` from
  `git.kernel.org/.../stable/linux.git/plain/<path>?h=v7.1.5`.

## Latent upstream bugs noticed in passing

Not acted on, worth reporting upstream:

* `bios_parser2.c:527` applies `le16_to_cpu()` to `pin->data_a_reg_index`, which
  is a `uint32_t`. It truncates to 16 bits. Harmless for `0x5DA9`; would
  silently break for any register index above `0xFFFF`.
* `firmwareinfo` declares `structuresize = 108 = sizeof(atom_firmware_info_v3_4)`
  while `content_revision = 3`, so `bios_parser2` dispatches to the v3_3 reader
  whose struct is only 72 bytes. Harmless for `board_i2c_feature_*` (identical
  offsets in v3_3 and v3_4) but do not trust any field past `+0x33` without
  first deciding which struct applies.

## Uncertain / not verified

* The 8-bit reading of `board_i2c_feature_slave_addr` (§6) — strongly supported
  by the sibling field's documented convention, but not by a direct consumer.
* That the clock bit is bit 0 (§4.1) — ATOM supplies only the data bit shift.
* Which of `i2c-9..12` corresponds to which specific ATOM line (the *set* is
  certain, the individual assignment is inferred from path order).
* **Empty bus vs. driver defect — NOT resolved (§8.2).** The register readback
  proved that DC leaves an internal SDA pull-down asserted on the OEM line via a
  guard that can never evaluate false, and killed the AUX-mode and
  `dc_gpio_aux_ctrl_5`/`phy_aux_cntl` theories, but it could not read the
  external line state, so it does not tell us whether anything is wired to those
  pads. This is the single most important open question and everything else
  should be read in that light.
* Whether an asserted internal pull-down is strong enough to hold SDA low
  against a board pull-up — probably not, which weakens the defect hypothesis
  that the readback otherwise supports.
* Whether the proposed patch actually makes `0x28` ACK. It gives a second,
  independent path to pins the VBIOS says the device is on; it cannot be
  validated without building, which is out of scope here.
