# TriXX 11.2.0 reversing log — recovering the Nitro Glow `iLine`

Goal: find what TriXX passes as `iLine` to `ADL_Display_WriteAndReadI2C` when talking to
the Nitro Glow MCU at i2c address `0x28` on a Sapphire RX 5700 XT Nitro+ (`1DA2:E409`).

Facts 1-9 from the shared investigation brief (summarised in the repository README) are taken as given and not re-derived.

> **Note on paths.** References below to `resources/`, `decompiled/` and `ghidra_proj/`
> are the analyst's local working directories. None of that material is published
> here — see "What is deliberately not in this repo" in the README. The scripts in
> `scripts/` regenerate it from inputs you supply yourself.

---

## TL;DR

**`iLine = 1`** (with `iAddress = 0x28 << 1`, `iSpeed = 150`, `iSize = 0x20`).

**Nature of the value — say this precisely.** The *native* layer does not hardcode it: the
i2c object's line byte is a **runtime parameter**, read out of a struct the managed side
marshals in (`SetLine(struct[+1])` at `0x0043cdc5` and `0x0043d3f6`). But the *value* that
flows through that parameter is a **compile-time literal in the managed assembly**. It is
never computed, never looked up in a per-model table, and never derived from ADL display
enumeration — TriXX does not even resolve an ADL display-enumeration entry point.

So: **a hardcoded constant, plumbed through a runtime parameter.** Not "runtime-computed" in
the sense that matters (nothing about the machine's state can change it), but the native code
alone will not tell you the number — you have to decrypt and read the managed assembly, which
is exactly why this task existed.

Full chain, every link read from code:

```
managed field initializer  _cdTLZNOm67uGJgAkwJN2GL5Ky5H = 1
  (_CgANPLqAxAjWB2ZDePHuZDQvQEr.cs:185, the Glow V3 control, alongside address = 40 = 0x28)
    -> native bridge FUN_0043cc90 (read, api id 40) / FUN_0043d170 (write, api id 41)
       does  SetLine(struct[+1])   -- a runtime load, 0x0043cdc5 / 0x0043d3f6:
              mov -0x34(%ebp),%edi ; movzbl 0x1(%edi),%eax ; push %eax ; call *0xc(%edx)
    -> FUN_004320c0  { *(byte*)(this + 0x18) = arg; }        // SetLine
    -> FUN_00431fa0 / FUN_004320e0  local_3c = (char)obj[+0x18]  // ADLI2C.iLine
    -> FUN_0042b980 -> ADL_Display_WriteAndReadI2C
```

`ADL_DL_I2C_LINE_OEM == 1`
(`<OpenRGB>/dependencies/display-library/include/adl_defines.h:962`) — so line 1 is the
**OEM line**, which the VBIOS track independently shows is the line carrying a device at
`0x28`. See "Cross-track convergence" at the end.

Blob decryption key for the managed assembly: `SapphireTrixx`, cipher
`p[i] = c[i] ^ key[i % L] ^ c[i-1]`. Not AES.

---

## Phase 1 — RCDATA blob cipher

### Observations (measured, not assumed)

`scripts/` analysis of `resources/{98,99,132,133,134}`:

| blob | size | size%16 |
|------|------|---------|
| 98 | 493344 | 0 |
| 99 | 654112 | 0 |
| 132 | 6366720 | 0 |
| 133 | 97720 | **8** |
| 134 | 80312 | **8** |

First red flag against AES: 133 and 134 are **not** multiples of 16, so they cannot be
raw AES-CBC/ECB ciphertext. The `ZN/IAes` string and `CRYPT32`/`ADVAPI32` imports are
therefore not necessarily related to these blobs.

Byte-value structure: in every blob, bytes `0x10..0x3B` are **all** `< 0x80` (0 high-bit
bytes out of 44). Probability under a random/AES stream: 2^-44. Something structured.

Autocorrelation (matches of `b[i] == b[i+s]` over first 4096 bytes):
- 98: shift 20 → 327, shift 40 → 296, shift 60 → 267 (baseline ~20)
- 133/134: shift 20 → ~270, 40 → ~210, 60 → ~190
- 132: shift 26 → 80, shift 2 → 69

So a repeating structure of period 20 (98/99/133/134) and 26 (132). Both even; suggests a
base period of 10 and 13.

### The decisive measurement

XOR-diff of the pairs that share a 16-byte prefix:

```
98 vs 99   : identical 0x00..0x3B, then constant delta 0x18 for 0x3C..0x7F (68 bytes),
             then delta becomes period-8 from 0x80
133 vs 134 : identical 0x00..0x3B, then constant delta 0x10 for 0x3C..0x7F,
             then period-8 from 0x80
```

`0x3C` is exactly `e_lfanew` in a DOS header, and `0x00..0x3B` is byte-identical in every
PE. So the plaintexts differ in **one** byte at `0x3C` (`0x80` vs `0x98` → XOR `0x18`;
`0xE0` vs `0xF0` → XOR `0x10`) — and that single-byte difference **smears forward as a
constant** until the next plaintext difference.

Forward smear of a one-byte difference is the signature of a **running XOR chain**, not a
block cipher: `c[i] = p[i] ^ ... ^ c[i-1]` gives `Δc[i] = Δp[i] ^ Δc[i-1]`.

### Hypothesis and confirmation

Hypothesis: `c[i] = p[i] ^ key[i mod L] ^ c[i-1]`, with `c[-1] = 0`.

Test: un-chain (`u[i] = c[i] ^ c[i-1]`), then XOR against the known standard 0x3C-byte
DOS header. If the hypothesis holds, the result is the repeating key.

Result (`u ^ MZ` over `0x00..0x3B`):

```
132 : 53 61 70 70 68 69 72 65 54 72 69 78 78  -> "SapphireTrixx"  (L=13, matches shift 26)
98  : 71 4b 37 64 52 6d 32 56 78 50           -> "qK7dRm2VxP"     (L=10, matches shift 20)
99  : same key as 98
133 : 63 4e 52 6f 65 46 33 7a 79 79           -> "cNRoeF3zyy"     (L=10)
134 : same key as 133
```

Clean printable ASCII, exactly repeating with the period the autocorrelation predicted.
That is not a coincidence.

**VERIFIED** — `scripts/decrypt_res.py` decrypts all five blobs to valid PE images:

| blob | key | result |
|------|-----|--------|
| 132 | `SapphireTrixx` | PE32 **Mono/.NET assembly**, BSJB at `0x5C8054`, 3 sections |
| 98 | `qK7dRm2VxP` | PE32 native x86, 5 sections |
| 99 | `qK7dRm2VxP` | PE32+ native x86-64, 6 sections |
| 133 | `cNRoeF3zyy` | PE32+ **kernel driver** x86-64 (has `PAGE`/`INIT` sections) |
| 134 | `cNRoeF3zyy` | PE32 **kernel driver** x86 (`PAGE`/`INIT`) |

Output in `resources/decrypted/{98,99,132}.exe and {133,134}.sys`.

Note: the cipher is a hand-rolled repeating-key XOR chain. It is **not** AES. The
`ZN/IAes` string in the stub is a red herring for this particular data path (it is a
mangled C++ symbol, most likely for TriXX's settings/profile encryption).

### Dead ends / things ruled out
- AES-anything for 133/134: sizes not 16-aligned.
- Straight positional repeating-XOR (no chain): predicted `c[0x18] != c[0x2C]` given the
  real DOS header, but they are equal in the blobs. Rejected before implementing.
- 20/26-byte key length: the true lengths are 10/13; the autocorrelation peak at 2L is an
  artefact of the chain + zero-runs in the DOS header.

---

## Phase 2 — what the blobs actually are

| blob | key | identity |
|------|-----|----------|
| 98  | `qK7dRm2VxP`    | `TPU_Query_External_x86.exe` (TechPowerUp GPU-Z query helper), PE32 |
| 99  | `qK7dRm2VxP`    | `TPU_Query_External_x64.exe`, PE32+ |
| 132 | `SapphireTrixx` | **`GUI.exe`** — the WPF TriXX front-end, .NET, ConfuserEx-obfuscated |
| 133 | `cNRoeF3zyy`    | `driver-x64.sys` ring-0 MMIO/PCI-config helper |
| 134 | `cNRoeF3zyy`    | `driver-x86.sys` |

Sparring-partner verification (independent, `objdump` + PE checksum):
- The XOR-chain loop was located at `0x0040b6a0` (blob 132) and `0x0048c8c0` (blobs 133/134).
  `mov (%esi,%edi,1),%al ; mov %al,-0xd(%ebp)` = **ciphertext** feedback, `prev` seeded to 0
  exactly once outside the loop → no reset. My formula is confirmed at instruction level.
- Stored PE checksums match on 98/99/133/134 (132 has none — Roslyn never writes one).
  Every byte of four of the five files is verified.
- `L"SapphireTrixx"` is stored **UTF-16** in the exe and narrowed at runtime via
  `WideCharToMultiByte`; `qK7dRm2VxP` / `cNRoeF3zyy` are narrow. Key length is read from a
  `std::string` (`mov -0xc(%ecx),%ebx`), not baked in — so `recover_key()` stays the right tool.
- Blob→key binding is confirmed by a data structure at VA `0x005B5830`
  (`00000000 85000000 00000000 86000000` = resource ids `0x85`/`0x86` = 133/134, paired with
  `cNRoeF3zyy`), not merely by "it decrypted".
- Chain-reset variants (L, 16, 64, 256, 512, 4096, 65536) all break the checksum. Refuted.

NOTE: TRIXX_11.2.0.exe is **not** a CLR-header assembly. It is a native MFC/ATL exe that
hosts the CLR (`mscoree.dll`) and calls `GUI.App.Init(ulong addr)` in blob 132, passing a
native pointer to an array of `{int id; void* fn}` records. The managed side wraps those with
`Marshal.GetDelegateForFunctionPointer`. Hardware access is native; the UI is managed.

---

## Phase 3 — the i2c path and `iLine`

### Native side (verified from disassembly)

The i2c object's vtable is at `.rdata:0x53471c`:

| slot | addr | role |
|------|------|------|
| 0 (+0x00) | `FUN_00431fa0` | READ  (`iAction=1`) |
| 1 (+0x04) | `FUN_00432200` | write convenience wrapper → slot 5 → slot 2 |
| 2 (+0x08) | `FUN_004320e0` | WRITE (`iAction=2`) |
| 3 (+0x0c) | `FUN_004320c0` | **`SetLine(byte)`** |
| 4 (+0x10) | `FUN_00431e50` | scalar deleting dtor |
| 5 (+0x14) | `FUN_004320d0` | → slot 2 |
| 6 (+0x18) | `FUN_00431e80` | READWRITE (`iAction=3`) |

`FUN_004320c0` is three instructions:

```c
undefined1 __thiscall FUN_004320c0(int param_1, undefined1 param_2)
{ *(undefined1 *)(param_1 + 0x18) = param_2; return 1; }
```

That is the only writer of `obj+0x18`, and `FUN_00431fa0` builds the ADL request as:

```c
local_40 = 0x20;                 // iSize   = sizeof(ADLI2C) = 32
local_3c = (int)(char)param_1[6];// iLine   <- obj+0x18, sign-extended byte
local_38 = (uint)param_2 * 2;    // iAddress = addr << 1
local_34 = (uint)param_3;        // iOffset  = register
local_30 = 1;                    // iAction  = READ
local_2c = param_1[4];           // iSpeed   <- obj+0x10, defaults to 100 if 0
local_28 = 1;                    // iDataSize
local_24 = param_4;              // pcData
FUN_0042b980(&local_40);         // -> ADL_Display_WriteAndReadI2C
```

Field order matches the ADL SDK `ADLI2C` exactly (and matches OpenRGB's
`<OpenRGB>/i2c_smbus/Windows/i2c_smbus_amdadl.cpp:145-150`).

`FUN_00403410(this, addr, reg, buf)` is a cached read helper whose cache key is
`(obj[+0x18] << 16) | (addr << 8) | reg` — i.e. `obj+0x18` is treated as a **bus**
discriminator, not a device property. Consistent with `iLine`.

> **CORRECTED after sparring review — read this before the list below.**
> The call sites enumerated here are TriXX's **internal fan / VRM / BIOS-switch helpers**.
> They are *not* the Glow path. The Glow path goes through the managed bridges, which pass a
> **runtime variable**, not a literal (`0x0043cdc5`, `0x0043d3f6` — see Phase 4). Do not read
> the list below as "every SetLine in the binary passes 1"; that claim is false as stated.
> What is true: every *immediate* passed to `SetLine` on the ADL class is `1`, and the only
> non-immediate callers are the two Glow bridges, whose variable is `struct[+1]`.

Internal (non-Glow) native call sites all use `SetLine(1)` before touching address `0x28`:
`FUN_00407900`, `FUN_004079f0`, `FUN_004066b0`, `FUN_004039f0`, `FUN_00406430`, e.g.

```c
piVar4 = FUN_00431e20(card[0x98]);        // ctor
(**(code **)(*piVar4 + 0xc))(1);          // SetLine(1)
piVar4[4] = 0x32;                         // iSpeed = 50
(**(code **)(*piVar4 + 4))(0x28,0x50,buf);// write dev 0x28 reg 0x50
(**(code **)(*piVar4 + 0x10))(1);         // delete
```

The only `SetLine(7)` in the binary is in the device factory `FUN_004048f0`, and it is on a
**different class**: `param_2==3` builds a 0x24-byte object via `FUN_00439380` and calls
`SetLine(7)`; `param_2==4` builds the 0x20-byte ADL object via `FUN_00431e20` and calls
`SetLine(1)`. `FUN_00439380` installs vtable `PTR_FUN_00535130` (type tag `0xd`), *not*
`0x53471c` — so line 7 belongs to a different, non-ADL transport; line 1 to the ADL one.

The shared base ctor `FUN_00441200` (which also creates the global mutex
`L"Global\\Access_I2C_Sequence"`) does `*(undefined2 *)(param_1 + 6) = 0xff`, i.e. it
initialises `obj+0x18` to `0xFF` = `-1` when read back as `(char)` — an explicit
"line not set" sentinel. `SetLine()` is therefore mandatory before any transfer, and the
value it is given for the ADL class is always the literal `1`.

### Managed side (verified from decompiled IL)

The bridge type is `_e0X3ixqypsIGvVPudL5Zky9bA8H._GUqaekIMhOefXmdm9JBMLBB7sBvA`
(`decompiled/132/_e0X3ixqypsIGvVPudL5Zky9bA8H/_GUqaekIMhOefXmdm9JBMLBB7sBvA.cs`).
Its i2c parameter struct (line 222):

```csharp
public struct _tJia8gIftAIT13zsqXdRjcwZTH {
    public byte _hQuA3TWfqaW1SmUUTffJG6mgMeC;   // i2c address
    public byte _cdTLZNOm67uGJgAkwJN2GL5Ky5H;   // *** iLine ***
    public uint _KOf1KdE5x3KDjiGhUgNLQS5efw;    // iSpeed (kHz)
    public uint _0Um4NgvpO8EVmdcTeJpBGGyAtjI;   // unused (always 0)
    public _OkbzRnswXIfbYlajDtKDSuZaPIh _emFlp9TzvodBQGs4lNOCgzG6x3E;  // NONE | SKIP_ADL_I2C
}
protected delegate bool ...(uint cardIndex, ref _tJia8gIftAIT13zsqXdRjcwZTH p,
                            byte offset, byte length, out string result);   // read,  fn id 40
protected delegate bool ...(uint cardIndex, ref _tJia8gIftAIT13zsqXdRjcwZTH p,
                            byte offset, string data);                      // write, fn id 41
```

Field roles pinned down:
- field0 ∈ {40, 85} = {`0x28`, `0x55`} = OpenRGB's `SAPPHIRE_NITRO_GLOW_V3_ADDR` /
  `SAPPHIRE_NITRO_GLOW_V1_ADDR` (`SapphireGPUControllerDetect.cpp:24-25`). It is also the
  field a hidden i2c debug pane parses out of a hex text box
  (`_G9fG1c1mIFUuZC2cilC8MpeTA6A.cs:129`, `byte.Parse(..., NumberStyles.HexNumber)`).
  → **field0 = i2c address**.
- field1 ∈ {1, 7} only. `0x01`-`0x07` are I2C-spec-reserved addresses and cannot be devices.
  → **field1 = iLine** (the only other byte, and `iLine` is the only other byte-sized member).
- field2 = 150 → `iSpeed`; the one site that sets 0 is the site that also sets `SKIP_ADL_I2C`.
- the register (`iOffset`) is passed as the separate `offset` argument, matching `param_3`.

### The Glow controls

Glow version → UI class dispatch is at
`decompiled/132/_cGNaWFZsAAolCJM4GumjkFdUt73/_pRFAXlBGjoICfQctEbvWLZMj2dj.cs:34-64`:

| Glow ver | class | addr | **line** | speed | transport |
|---|---|---|---|---|---|
| V1 | `_pxxJcteIku3yaaB07oG7ViAP4Ce.cs:63`  | 85 = `0x55` | **7** | 0   | `SKIP_ADL_I2C` (driver) |
| V2 | `_MtyMSizjTYT4IEZfstX7s5fddAg.cs:171` | 85 = `0x55` | **1** | 150 | ADL |
| V3 | `_CgANPLqAxAjWB2ZDePHuZDQvQEr.cs:183` | 40 = `0x28` | **1** | 150 | ADL |
| V4 | `_ljOVDZnHJhl2oZndhdMJFPaUM0Q.cs:161` | 40 = `0x28` | **1** | 150 | ADL |
| —  | `_TSgK7CiEnxZn1gtbbZWYL09sSaO.cs:226` | 40 = `0x28` | **1** | 150 | ADL |

The V3/V4 classes drive exactly the Nitro Glow V3 register map — offsets used are
`16,17,18,19,21,22,26,27,28,62` = `0x10` mode, `0x11/0x12/0x13/0x15/0x16` animation speeds,
`0x1A/0x1B/0x1C` R/G/B, `0x3E` brightness — identical to
`<OpenRGB>/Controllers/SapphireGPUController/SapphireNitroGlowV3Controller/SapphireNitroGlowV3Controller.h:21-32`.

### Is it a constant, a table, or runtime-computed?

**A hardcoded constant, plumbed through a runtime parameter.** The two are not in tension and
the distinction matters, so state both halves:

- *Runtime parameter, in the native code.* The i2c object's line byte is set by
  `SetLine(struct[+1])` where `struct` is the managed-marshalled parameter block
  (`0x0043cdc5` in the read bridge, `0x0043d3f6` in the write bridge). Nothing in the native
  binary tells you the number. This is why the value could not be recovered without breaking
  the resource encryption.
- *Compile-time literal, in the managed code.* Exhaustive grep:
  `_cdTLZNOm67uGJgAkwJN2GL5Ky5H` (the `iLine` field) is assigned in exactly **5** places in
  the whole assembly, every one an integer literal in a field/local initializer. It is never
  read from a table, never derived from ADL display enumeration, never computed from anything.
  No machine state can change it.

There *is* a per-model table (`_3qfzvUC7NGxAR18wC5ynWjLS0Bp.cs`, a factory switched on PCI
device id and subsystem device id — our card appears at line 2661 as `num10 == 58377`
= `0xE409`), but what it selects is the **Glow version** (`None/V1/V2/V3/V4`), the fan
topology and the OC profile — *not* the i2c line. The line is then a constant baked into
whichever version-specific control class the table selects.

### Answer

**`iLine = 1`** for the Nitro Glow MCU at `0x28`, with `iSpeed = 150` kHz.
Same value the OpenRGB Windows ADL backend already hardcodes
(`i2c_smbus_amdadl.cpp:148`, `pI2C->iLine = 1; //location of the Aura chip`).

Every ADL path in TriXX — every Glow version except V1, which does not use ADL at all —
uses line 1. `7` only ever appears on the non-ADL driver transport.

### Exhaustive managed call-site check (independent of the above)

Every call of the two i2c wrappers in the whole assembly (86 sites) passes one of only four
struct instances:

| struct expression | count | source |
|---|---|---|
| `_GzJQe3FHDB2kr4b7nXQfYXiG0Ql` | 86 | protected field on base class `_OKef3tHtXfbNviTvhTXOiyfX8gc`, assigned only in the 4 Glow control ctors |
| `gzJQe3FHDB2kr4b7nXQfYXiG0Ql`  | 4  | local copy of the above in the hidden i2c debug pane `_G9fG1c1mIFUuZC2cilC8MpeTA6A`; it overrides only **address** and **speed** |
| `_5XkbQpa7Dw7Lr6DQMJSQLtWoHl9` | 3  | `_TSgK7CiEnxZn1gtbbZWYL09sSaO` power-protection poller, `{40, 1, 150, 0}` |
| `P_0` / forwarders            | 4  | base-class + bridge forwarders |

So the `iLine` byte on every single i2c transfer TriXX ever issues traces back to one of the
5 literal initializers. No other producer exists.

`_TSgK7CiEnxZn1gtbbZWYL09sSaO` (`.cs:216-241`) is a power-protection panel gated on
`_J7ETQ8J4SV9ps8e8A6u05uXvpIb.PowerProtectionSupported`; it polls device `0x28` on line 1
every 5 s. Confirms `0x28` is reached on line 1 outside the Glow UI too.

### Runtime-derivation ruled out at the API level

TriXX resolves **61** ADL entry points by name from `atiadlxx.dll`. Exactly **one** of them is
an `ADL_Display_*` function:

```
$ strings -a TRIXX_11.2.0.exe | grep -E '^ADL2?_' | sort -u | grep -iE 'display|ddc|i2c|aux'
ADL_Display_WriteAndReadI2C
```

There is no `ADL_Display_DisplayInfo_Get`, no `ADL_Display_NumberOfDisplays_Get`, no
`ADL_Display_DDCInfo*`, no `ADL_Display_ConnectedDisplays_Get` — nothing that could enumerate
displays or DDC lines. TriXX therefore **cannot** be computing `iLine` from ADL display
enumeration: the API surface required to do so is not even resolved. Combined with the
exhaustive grep showing the line byte only ever comes from 5 integer literals, the *value* is
a hardcoded constant, full stop — even though the native code *transports* it as a runtime
parameter (see the correction in Phase 3 / the appendix).

---

## Phase 4 — the native marshalling bridge (closes the last inference gap)

Earlier I mapped the managed struct's field roles by elimination. That gap is now closed by
finding the actual native functions the managed side calls.

Chain: `FUN_00408970` (WinMain-ish; gates on PCI vendor `0x1002` + subvendor
`{0x1002, 0x1DA2, 0x174B}`) → `FUN_0040b2b0` (CLR host: `FUN_00409160(L"GUI.App")`, then
`FUN_0040cae0` invokes `Init`). The `{int id; void* fn}` vector handed to `GUI.App.Init` is
built by `FUN_004072f0` (vtable slot 1 of `PTR_FUN_005b325c`), which first calls
`FUN_0043c620` for the base id range. In `FUN_0043c620`:

```c
*puVar1 = 0x28;  puVar1[1] = FUN_0043cc90;   // id 40 -> managed i2c READ  delegate
*puVar1 = 0x29;  puVar1[1] = FUN_0043d170;   // id 41 -> managed i2c WRITE delegate
```

Those are exactly the ids the managed bridge fetches
(`_DpoV9ktFiRJAreyQtxplM6JF15c<...>(40)` / `(41)`).

`FUN_0043cc90(uint cardIndex, byte *p /*the managed struct*/, offset, length, BSTR *out)`:

```c
if (*(DWORD *)(p + 8) != 0) Sleep(*(DWORD *)(p + 8));   // p+0x08 = pre-transfer delay (ms)

if ((p[0xc] & 1) != 0) {                                 // p+0x0C = SKIP_ADL_I2C
    if (*(int *)(*adapter + 0xe4) == 0x16) obj = FUN_0043b310(...);   // alternate transport
    if (*(int *)(*adapter + 0xe4) == 0x17) obj = FUN_0043b390(...);   // alternate transport
}
if (obj == NULL) obj = FUN_00408580(...);                // default = the ADL i2c class

(**(code **)(*obj + 0xc))(p[1]);          // SetLine( p[+0x01] )   -> obj+0x18 -> iLine
obj[4] = *(int *)(p + 4);                 // iSpeed = p[+0x04]
...
(**(code **)(*obj + 0x18))(*p, offset, buf, len);   // slot 6 = READWRITE, address = p[+0x00]
```

**VERIFIED, not inferred:**

| managed struct offset | field | native destination |
|---|---|---|
| `+0x00` | `_hQuA3TWfqaW1SmUUTffJG6mgMeC` | i2c **address** arg of the transfer |
| `+0x01` | `_cdTLZNOm67uGJgAkwJN2GL5Ky5H` | `SetLine()` → `obj+0x18` → **`ADLI2C.iLine`** |
| `+0x04` | `_KOf1KdE5x3KDjiGhUgNLQS5efw`  | `obj+0x10` → `ADLI2C.iSpeed` |
| `+0x08` | `_0Um4NgvpO8EVmdcTeJpBGGyAtjI` | `Sleep()` delay in ms before the transfer (always 0) |
| `+0x0C` | `_emFlp9TzvodBQGs4lNOCgzG6x3E` | `SKIP_ADL_I2C` — bit 0 set selects an alternate non-ADL transport (`FUN_0043b310`/`FUN_0043b390`) when the adapter type at `*adapter+0xe4` is `0x16`/`0x17`; otherwise falls through to `FUN_00408580`, which constructs the ADL class (`FUN_00431e20`, vtable `0x53471c`) |

This also settles the `SKIP_ADL_I2C` polarity: the flag being **set** means *bypass ADL*.
It is set only by the Glow **V1** control (`0x55`, line 7) — so line 7 is a non-ADL bus index
and never reaches `ADL_Display_WriteAndReadI2C`. Every ADL transfer uses line **1**.

The WRITE bridge `FUN_0043d170` (id 41) is identical in shape:

```c
Sleep(*(DWORD *)(param_2 + 8));
(**(code **)(*obj + 0xc))(param_2[1]);          // SetLine( struct[+1] )
obj[4] = *(int *)(param_2 + 4);                 // iSpeed  = struct[+4]
(**(code **)(*obj + 0x14))(*param_2, offset, buf, len);  // slot 5 -> slot 2 = WRITE,
                                                         // address = struct[+0]
(**(code **)(*obj + 0x10))(1);                  // delete
```

Both directions agree. The field mapping is now established from the code, end to end:
managed literal `_cdTLZNOm67uGJgAkwJN2GL5Ky5H = 1` → `SetLine(1)` → `obj+0x18` →
`ADLI2C.iLine = 1` → `ADL_Display_WriteAndReadI2C`.

---

## Phase 5 — the per-model table branch for THIS card, traced

Remaining gap: I had argued "0xE409 → Glow V3" from OpenRGB rather than proving it, because
the model factory `_3qfzvUC7NGxAR18wC5ynWjLS0Bp._yCt6mPXTyn1erZUByQmXbGwFiKF()` is
ConfuserEx switch-flattened. `scripts/trace_model_table.py` walks that state machine.

Dispatch header (`_3qfzvUC7NGxAR18wC5ynWjLS0Bp.cs:126`):
`switch ((num4 = (uint)(num3 ^ 0x7EE4DC75)) % 414)`. Inputs:

| var | source | our card |
|---|---|---|
| `num`   | `PROP_DEVICE_ID`    (`.cs:115`) | `0x731F` |
| `num2`  | `PROP_SUBVENDOR_ID` (`.cs:116`) | `0x1DA2` |
| `num10` | `PROP_SUBSYS_ID`    (`.cs:784`) | `0xE409` |
| `num5`  | `PROP_REVISION`     (`.cs:785`) | `0xC1`   |

(`lspci -nn -s 0000:0e:00.0` → `[1002:731f] (rev c1)`, `Subsystem: [1da2:e409]` — the real
card's revision is `0xC1`, and the machine really does test `num5 != 193`.)

```
$ python3 scripts/trace_model_table.py 0x731F 0xE409 0xC1 0x1DA2
tracing device=0x731F subven=0x1DA2 subsys=0xE409 rev=0xC1

*** REACHED DESCRIPTOR at case 123u, source line 966 ***
    _4qC7ji6DOHDkjSsQeUeJqWOEHnJ = new ... { ... = ..._a14q0JIqfBPFMHPcRG3DLYaklmD.V3 },
    _gw9Dmk8BLPJy9aNmpLU0eqY5ruQ = new ... { ... = ..._2OUAHrycf8GPcSIKJIAUFXzNHVDb.NaviThreeFan },
    _NugewsBatRwoJg6LPxDkGLaiKmI = _J7ETQ8J4SV9ps8e8A6u05uXvpIb.BiosSwitchSupported
```

**Glow V3, NaviThreeFan, BiosSwitchSupported** — the RX 5700 XT Nitro+ profile, and it agrees
with OpenRGB's independent `SAPPHIRE_NAVI10_NITRO_PLUS_SUB_DEV1 = 0xE409 →
DetectSapphireV3Controllers` registration.

Glow V3 → control class `_CgANPLqAxAjWB2ZDePHuZDQvQEr` → `{address 40 = 0x28, line 1,
speed 150}`. The chain is now closed with no inferred links.

Tracer sanity: it is not degenerate. `0xE409`, `0xE410` and `0xE438` reach real descriptors;
`0x0000` and several non-Navi combinations fall out of the walk instead. (The tracer only
models the Navi branch cleanly — Polaris inputs exit at an unparsed case. That is a limit of
the tracer, not a contradiction; the Navi path we care about resolves fully.)

---

## Verified vs inferred — final accounting

(Updated after the sparring review and the VBIOS track's result.)

**Verified (read directly from code / checked byte-for-byte, and independently re-derived by
the adversarial reviewer where noted):**
- the RCDATA cipher, keys, and that all five blobs decrypt to valid PEs — four of the five
  confirmed by their own stored PE checksum, i.e. every byte
- `ADLI2C.iLine` comes from `obj+0x18`; `FUN_004320c0` is its setter, and the base ctor
  `FUN_00441200` writes the `0xFF` "unset" sentinel
- the managed→native API ids 40/41 (registration table `FUN_0043c620`) and the two bridge
  functions `FUN_0043cc90` / `FUN_0043d170` — confirmed from both ends (registration table by
  me, marshalling disassembly `0x0043cdc5-0x0043ce1f` / `0x0043d3ea-0x0043d414` by the reviewer)
- the managed struct field→native destination mapping (address, line, speed, delay, flag);
  **not swapped**, checked byte-for-byte against the C# sequential layout
- `SKIP_ADL_I2C` polarity: set = bypass ADL (`0x0043cd24: testb $0x1,0xc(%ecx)`)
- `_cdTLZNOm67uGJgAkwJN2GL5Ky5H` is assigned in exactly 5 places, all integer literals
- every one of the 86 managed i2c call sites uses one of those 5 structs
- TriXX resolves no ADL display-enumeration API at all (1 of 61 ADL entry points is
  `ADL_Display_*`, and it is `WriteAndReadI2C`)
- `0x731F` / `1DA2:E409` / rev `0xC1` selects Glow V3 — traced twice, independently, through
  the ConfuserEx dispatcher; and no Navi block anywhere in the table yields V1
- `ADL_DL_I2C_LINE_OEM == 1` (`adl_defines.h:962`)

**Inferred (reasonable but not proven):**
- that `iSpeed = 150` is kHz (ADL documents `iSpeed` in kHz; TriXX passes 150 and the native
  internal paths pass 50 or default 100 — all plausible kHz values)
- that the `0x16`/`0x17` adapter-type constants gating the non-ADL transport are ASIC-family
  ids; I did not chase what they are

**Conditioned / not exhaustively ruled out:**
- the V3 selection depends on PCI revision `== 0xC1`. This card is `0xC1`, so the result
  holds *for this card*; another `1DA2:E409` at a different revision diverges at case 235
- the `obj+0x18` writer sweep was opcode-pattern based; a `rep movsd` / struct-assignment
  write into `obj+0x18` would evade it and has not been ruled out

**Open (and now largely answered by the other track):**
- what ADL `iLine = 1` corresponds to *physically* on Navi 10. The VBIOS track answers this
  from the firmware side — OEM i2c feature, GPIO `0x97`, `DC_GPIO_DDCVGA` pin pair, device at
  `0x28`. See "Cross-track convergence". What remains genuinely open is the kernel work:
  `amdgpu` does not register that pin pair as an i2c adapter, and that is a patch proposal,
  not a finding.

---

## Appendix — the Glow V3 control's register set (independent confirmation of class identity)

Registers touched by `_CgANPLqAxAjWB2ZDePHuZDQvQEr` (the class carrying `{0x28, line 1, 150}`),
extracted from its i2c call sites, vs OpenRGB's
`SapphireNitroGlowV3Controller/SapphireNitroGlowV3Controller.h`:

| reg | TriXX | OpenRGB name |
|-----|-------|--------------|
| `0x0F` | read | `REG_EXTERNAL_CONTROL` |
| `0x10` | read+write | `REG_MODE` |
| `0x11` | read+write | `REG_RUNWAY_ANIMATION_SPEED` |
| `0x12` | read+write | `REG_RUNWAY_ANIMATION_REPEAT_COUNT` |
| `0x13` | read+write | `REG_COLOR_CYCLE_ANIMATION_SPEED` |
| `0x15` | read+write | `REG_RAINBOW_ANIMATION_SPEED` |
| `0x16` | read+write | `REG_SERIAL_ANIMATION_SPEED` |
| `0x1A` | read+write | `REG_RED` |
| `0x1B` | read+write | `REG_GREEN` |
| `0x1C` | read+write | `REG_BLUE` |
| `0x3E` | read+write | `REG_BRIGHTNESS` |

Eleven registers, exact match, no extras on either side. OpenRGB's protocol reconstruction
is correct; the only thing it is missing on Linux is a bus that reaches the MCU.

---

## Appendix — exhaustive disassembly sweep for `SetLine` call sites

Ghidra's decompiler failed to recover the argument at many `(**(code **)(*obj + 0xc))()`
sites, so the "every SetLine call passes 1" claim was the weakest link. Swept it at the
instruction level instead (`objdump -d -M intel` over the whole exe, 434 934 lines):

- 299 `call DWORD PTR [reg+0xc]` sites in total (all classes).
- Immediates pushed immediately before, distribution:
  `None x272, 0x1 x11, 0x2 x3, 0x3 x2, 0x7 x1, 0x5 x1, 0x63 x1, + 8 pointer-valued`.
- Of the twelve sites pushing `1` or `7`, four are unrelated (`0x40c9bc`, `0x40c9f4` push two
  args → a different 2-parameter method; `0x4b65f6`, `0x4b7844` are `push 1; call [eax+0xc];
  push 0x20; call operator new` → a scalar deleting destructor on another class).
- The remaining eight are the i2c ones:

| site | containing fn | value |
|---|---|---|
| `0x403a61` | `FUN_004039f0` | **1** |
| `0x404c51` | `FUN_004048f0` (factory, `param_2 == 4`, ADL class) | **1** |
| `0x4064a1` | `FUN_00406430` | **1** |
| `0x406721` | `FUN_004066b0` | **1** |
| `0x407991` | `FUN_00407900` | **1** |
| `0x407a7c` | `FUN_004079f0` | **1** |
| `0x4594ef` | `FUN_004593b0` | **1** |
| `0x404b80` | `FUN_004048f0` (factory, `param_2 == 3`, **non-ADL** class `FUN_00439380`) | **7** |

**There is exactly one `SetLine(7)` immediate in the entire binary and it is on the non-ADL
transport.** Every other `SetLine` immediate is `1`.

**CORRECTION (sparring review).** The headline this sweep originally carried — "every SetLine
call passes literal 1" — is **false**, and the falsification is on the path that matters.
Two sites pass a *runtime variable*, and they are the Glow ones:

| site | fn | argument |
|---|---|---|
| `0x0043cdc5` | `FUN_0043cc90` (read bridge, api id 40) | `movzbl 0x1(%edi),%eax; push %eax` = `struct[+1]` |
| `0x0043d3f6` | `FUN_0043d170` (write bridge, api id 41) | same shape |

The eight immediate-`1` sites tabulated above are TriXX's **internal fan / VRM / BIOS-switch
helpers**, not the Glow path. They corroborate that TriXX treats the OEM line as line 1, but
they are not what drives the LEDs. Confirmed independently by the sparring partner's own
disassembly (read marshalling `0x0043cdc5-0x0043ce1f`, write `0x0043d3ea-0x0043d414`).

**Second correction: the ctor-caller set.** Vtable `0x53471c` is installed at exactly two
sites — `0x00431e32` (ctor `FUN_00431e20`) and `0x00431e56` (dtor) — so the caller set is
finitely enumerable and is **nine** call sites, not the six I originally leaned on. I missed
`0x004594d7` (another `SetLine(1)`, speed `0x32`) and `0x004085d4`. The latter matters:
`FUN_00408580` is a factory that `new`s the 0x20-byte object, ctors it, stores it to an
out-param and **returns without calling `SetLine`** — the object escapes still carrying the
`0xFF` sentinel written by the base ctor. Its two callers are precisely the two
variable-argument bridges above. That is the hole in any "look at what the ctor's neighbours
pass" argument, and it is why the ctor-adjacency reasoning had to be replaced by actually
reading the bridges (Phase 4).

---

## Sparring review — independent confirmations

An adversarial reviewer was tasked twice (cipher hypothesis, then the `iLine` conclusion) with
finding holes. Verdict: the answer **survives**; two of my load-bearing *steps* did not, and
are corrected in place above. Its independent findings:

**Confirmed, by routes I did not use:**

1. **The field mapping is not swapped.** Marshalling ranges disassembled directly:
   read `0x0043cdc5-0x0043ce1f`, write `0x0043d3ea-0x0043d414`.
   `struct[+1] -> SetLine -> obj+0x18 -> iLine`; `struct[+4] -> obj+0x10 -> iSpeed`;
   `struct[+0] -> the address argument`. The C# sequential layout
   `{byte@0, byte@1, uint@4, uint@8, enum@0xC}` matches byte for byte. So the answer is not
   `40`/`85` under a swap.
2. **`SKIP_ADL_I2C` is not inverted.** `0x0043cd24: testb $0x1,0xc(%ecx)` — `struct+0xC` is the
   enum; **clear** falls through to `call 0x408580` (the ADL class), **set** selects the
   non-ADL transports. Value `1` = bypass ADL, as I read it. So line `7` never reaches
   `ADL_Display_WriteAndReadI2C`.
3. **`0xE409 -> Glow V3`, traced independently.** Same dispatcher
   (`switch ((num4 = (uint)(num3 ^ 0x7EE4DC75)) % 414)`), from `num3 = 230617419`, path
   `116-338-94-148-230-320-59-235-166-259-127-310-123`, arriving at case 123 →
   `Glow=V3, Fan=NaviThreeFan, Flags=BiosSwitchSupported`. Identical to my
   `scripts/trace_model_table.py` result. Additionally, across all **140** return blocks: V1
   occurs 7x (6 Polaris, 1 Vega, **zero Navi**), all **32** Navi blocks are V3 or None, and
   **V2 appears in no return block at all**. So my fallback argument ("even if I picked the
   wrong version, V2/V3/V4 all use line 1") holds independently — and is in fact stronger than
   I claimed, since no Navi card can reach the V1/line-7 path.

**One residual risk it raised that I can close:** it could not locate the `{int id; void* fn}`
registration table (the `.rdata` pointers it chased were MSVC EH funclets, magic `0x19930522`)
and therefore rated the "fn 40/41" identification as circumstantial. I *did* find it —
`FUN_004072f0` (vtable slot 1 of `PTR_FUN_005b325c`) → `FUN_0043c620`, which contains the
literal registrations `*puVar1 = 0x28; puVar1[1] = FUN_0043cc90;` and
`*puVar1 = 0x29; puVar1[1] = FUN_0043d170;` (Phase 4). Combined with its disassembly of those
same two functions' marshalling code, the identification is direct from both ends.

**Residual risks that stand:**

- **Revision conditioning.** Case 235 of the model dispatcher tests PCI revision `== 0xC1`.
  This card reads `0xc1` (`/sys/bus/pci/devices/0000:0e:00.0/revision`, and `lspci` shows
  `[1002:731f] (rev c1)`), so it is on the V3 path — but a `1DA2:E409` board with a different
  revision (e.g. `0xC4`) diverges at that node. **The result is conditioned on rev `0xC1`.**
- **The `obj+0x18` writer sweep was opcode-pattern based** (202 hits; only `SetLine` at
  `0x004320c6` and the base-ctor sentinel `movw $0xff` at `0x0044123d` apply to this class).
  A `rep movsd` / structure-assignment write into `obj+0x18` would evade that pattern and has
  **not** been exhaustively ruled out.
- `iSpeed = 150` is assumed to be kHz (ADL documents `iSpeed` in kHz). Not verified.

---

## Cross-track convergence — the most important result

The VBIOS track finished independently and lands on the same place from the opposite
direction. Its result:

- this card's VBIOS `firmwareinfo` table declares an OEM i2c device outright:
  `board_i2c_feature_id = 0x02`, `board_i2c_feature_gpio_id = 0x97`,
  `board_i2c_feature_slave_addr = 0x50` — which is `0x28` in 7-bit form;
- gpio `0x97` resolves through `gpio_pin_lut` to `data_a_reg_index 0x5DA9`
  = `DCN_BASE__INST0_SEG2 (0x34C0) + mmDC_GPIO_DDCVGA_A (0x28E9)`, i.e. the
  **`DC_GPIO_DDCVGA` pin pair**.

And on my side, `ADL_DL_I2C_LINE_OEM == 1`
(`<OpenRGB>/dependencies/display-library/include/adl_defines.h:962`).

So: **TriXX asks ADL for the OEM line (1) and talks to `0x28`; the VBIOS says the OEM i2c
feature is a device at `0x28` on the `DC_GPIO_DDCVGA` pins.** Two fully independent
routes — decrypting and decompiling a Windows application, versus parsing the card's own
firmware tables — agree on both the address and the fact that it lives on the OEM/DDCVGA line,
not on any of the buses `amdgpu` currently exposes. That mutual confirmation is worth more
than the bare number `1`: it means the Linux-side gap is not a wrong address or a wrong bus
*index*, it is a bus that is **not registered at all**.

Note also the OpenRGB bus-name whitelist already anticipates this: it accepts
`AMDGPU i2c bit bus OEM 0x97` (`<OpenRGB>/i2c_smbus/i2c_amd_gpu.h`) — GPIO `0x97`, exactly
the pin id the VBIOS names. Nothing registers a bus by that name on this kernel.

---

## Strategic caveat — what this does and does not unblock

Stated plainly, because it would be easy to over-sell this result:

`iLine = 1` **restates** established fact 5 in the investigation brief and is already hardcoded at
`<OpenRGB>/i2c_smbus/Windows/i2c_smbus_amdadl.cpp:148`. The value was worth recovering as
*confirmation from the vendor's own tool* — it proves OpenRGB's Windows constant is right and
not a lucky guess, and it pins the device to the OEM line — but on its own it does not unblock
Linux, because:

- ADL line indices are a Windows display/DDC abstraction; they do **not** map 1:1 onto an
  `amdgpu` `/dev/i2c-N`;
- `0x28` still NAKs on every bus `amdgpu` currently exposes (established fact 3).

The actionable output of this track is therefore not the number itself but the convergence
above: the target is the `DC_GPIO_DDCVGA` pin pair (GPIO `0x97`), which the kernel does not
register as an i2c adapter. That is a kernel-patch proposal for the other track to carry, not
something to apply here.

---

## Deferred lead — the ring-0 driver pair (NOT pursued)

`resources/decrypted/133.sys` (x64) and `134.sys` (x86) import
`MmMapIoSpace`, `HalGetBusDataByOffset` / `HalSetBusDataByOffset`, `READ_PORT_*`,
`KeStallExecutionProcessor` — a generic MMIO/PCI-config/port-IO ring-0 helper. The Glow **V1**
path (`SKIP_ADL_I2C`, `0x55`, line 7) shows TriXX is willing to drive Glow hardware through
this driver rather than through ADL.

Worth pursuing, one paragraph on why, then stopping as instructed: if that driver contains a
bit-banged i2c implementation, it would show the **exact MMIO register sequence** used to
drive a GPIO pin pair as an i2c bus — which, given the VBIOS track has already identified the
target as `mmDC_GPIO_DDCVGA_A` at `DCN_BASE__INST0_SEG2 + 0x28E9`, would let us verify a
proposed `amdgpu` patch against a known-good reference implementation instead of against
first principles. That is the single highest-value thing left in the TriXX binaries. It is
also not needed to *state* the answer, only to de-risk the kernel patch, which is why
deferring it is correct.
