#!/usr/bin/env python3
"""
atom_i2c_parse.py -- enumerate every i2c line described by an AMD 'atomfirmware'
(format_revision 2) VBIOS image, and cross-reference it against the display
connectors and the OEM board-i2c feature.

Written for the Sapphire RX 5700 XT Nitro+ (Navi 10, 1002:731F / 1DA2:E409)
investigation, but generic for any Vega/Navi-era ATOM image.

IMPORTANT -- naming: the *legacy* atombios data table `GPIO_I2C_Info` holding an
`ATOM_GPIO_I2C_ASSIGNMENT[]` array does NOT exist in an atomfirmware VBIOS.
On Vega and later the equivalent information is split:

  * data table  gpio_pin_lut  (index 12)  -> struct atom_gpio_pin_lut_v2_1
                                             { atom_common_table_header;
                                               atom_gpio_pin_assignment[] }
    Each atom_gpio_pin_assignment is 8 bytes:
        uint32_t data_a_reg_index;   /* dword register index of DC_GPIO_*_A  */
        uint8_t  gpio_bitshift;      /* bit position inside that register    */
        uint8_t  gpio_mask_bitshift;
        uint8_t  gpio_id;            /* == legacy ucI2cId when bit7 (HW_CAP) */
        uint8_t  reserved;
    gpio_id bitfields (atomfirmware.h enum atom_gpio_pin_assignment_gpio_id):
        0x80 I2C_HW_CAP            -> this pin is one half of an i2c pin pair
        0x70 I2C_HW_ENGINE_ID_MASK -> HW i2c engine id (>>4)
        0x0f I2C_HW_LANE_MUX       -> line/lane mux number

  * data table  displayobjectinfo (index 22) -> display_object_info_table_v1_4
    Per display path, a record list; ATOM_I2C_RECORD_TYPE(1) records carry
    struct atom_i2c_record { u8 type; u8 size; u8 i2c_id; u8 i2c_slave_addr; }.
    i2c_id has exactly the same bitfield layout as gpio_id above; the kernel
    matches record->i2c_id against pin->gpio_id in
    drivers/gpu/drm/amd/display/dc/bios/bios_parser2.c:get_gpio_i2c_info().

  * data table  firmwareinfo (index 4) -> atom_firmware_info_v3_x
    board_i2c_feature_id / _gpio_id / _slave_addr. When
    board_i2c_feature_id == 0x2 the DC resource layer creates the extra
    "AMDGPU DM i2c OEM bus" on gpio pin id == board_i2c_feature_gpio_id
    (bios_parser2.c get_firmware_info_v3_x + dcn20_resource.c:2802).

Note on `data_a_reg_index`: it is an *absolute SOC15 dword register index*, i.e.
   data_a_reg_index == DCN_BASE__INST0_SEG<n> + mmDC_GPIO_<pad>_A
For Navi 10, DCN_BASE__INST0_SEG2 == 0x000034C0 (navi10_ip_offset.h:269) and all
mmDC_GPIO_* registers have _BASE_IDX 2, so pass --regbase 0x34C0 together with
--regmap to resolve register names. Each DC_GPIO_* pad group is a fixed 4-register
block MASK, A, EN, Y at A-1, A, A+1, A+2.

Usage:
  python3 atom_i2c_parse.py <vbios.rom> \
      [--regmap .../asic_reg/dcn/dcn_2_0_0_offset.h] [--regbase 0x34C0] [--json]
Plain Python 3 stdlib only.
"""

import json
import os
import re
import struct
import sys

OFFSET_TO_ATOM_ROM_HEADER_POINTER = 0x48

# struct atom_master_list_of_data_tables_v2_1 -- atomfirmware.h:390
MASTER_LIST_V2_1 = [
    "utilitypipeline", "multimedia_info", "smc_dpm_info", "sw_datatable3",
    "firmwareinfo", "sw_datatable5", "lcd_info", "sw_datatable7",
    "smu_info", "sw_datatable9", "sw_datatable10", "vram_usagebyfirmware",
    "gpio_pin_lut", "sw_datatable13", "gfx_info", "powerplayinfo",
    "sw_datatable16", "sw_datatable17", "sw_datatable18", "sw_datatable19",
    "sw_datatable20", "sw_datatable21", "displayobjectinfo", "indirectioaccess",
    "umc_info", "sw_datatable25", "sw_datatable26", "dce_info",
    "vram_info", "sw_datatable29", "integratedsysteminfo", "asic_profiling_info",
    "voltageobject_info", "sw_datatable33", "sw_datatable34",
]

I2C_HW_LANE_MUX = 0x0F
I2C_HW_ENGINE_ID_MASK = 0x70
I2C_HW_CAP = 0x80

ATOM_I2C_RECORD_TYPE = 1
ATOM_RECORD_END_TYPE = 0xFF

# grph_object_id.h -- connector object ids (OBJECT_TYPE_CONNECTOR == 3, bits 12..14)
CONNECTOR_ID = {
    0x00: "NONE", 0x01: "SINGLE_LINK_DVII", 0x02: "DUAL_LINK_DVII",
    0x03: "SINGLE_LINK_DVID", 0x04: "DUAL_LINK_DVID", 0x05: "VGA",
    0x06: "COMPOSITE", 0x07: "SVIDEO", 0x08: "YPbPr", 0x09: "D_CONNECTOR",
    0x0A: "9PIN_DIN", 0x0B: "SCART", 0x0C: "HDMI_TYPE_A", 0x0D: "HDMI_TYPE_B",
    0x0E: "LVDS", 0x0F: "7PIN_DIN", 0x10: "PCIE", 0x11: "CROSSFIRE",
    0x12: "HARDCODE_DVI", 0x13: "DISPLAY_PORT", 0x14: "EDP",
    0x15: "MXM", 0x16: "LVDS_eDP", 0x17: "USBC",
}
OBJECT_TYPE = {
    0: "UNKNOWN", 1: "GPU", 2: "ENCODER", 3: "CONNECTOR", 4: "ROUTER",
    5: "GENERIC",
}


def objid_str(objid):
    """graphics_object_id packed in a VBIOS u16: id = bits 0..7,
    enum_id = bits 8..11, type = bits 12..15 (grph_object_id.h)."""
    oid = objid & 0xFF
    enum_id = (objid >> 8) & 0x0F
    otype = (objid >> 12) & 0x0F
    tname = OBJECT_TYPE.get(otype, "TYPE%d" % otype)
    if otype == 3:
        return "%s/%s/enum%d" % (tname, CONNECTOR_ID.get(oid, "id0x%02x" % oid), enum_id)
    return "%s/id0x%02x/enum%d" % (tname, oid, enum_id)


class Atom:
    def __init__(self, blob):
        self.d = blob
        self.rom_hdr = struct.unpack_from("<H", self.d, OFFSET_TO_ATOM_ROM_HEADER_POINTER)[0]
        if self.rom_hdr == 0 or self.rom_hdr + 0x28 > len(self.d):
            raise ValueError("bogus ATOM_ROM_HEADER pointer 0x%04x" % self.rom_hdr)
        (self.rh_size, self.rh_fmt, self.rh_cont) = struct.unpack_from("<HBB", self.d, self.rom_hdr)
        self.sig = self.d[self.rom_hdr + 4:self.rom_hdr + 8]
        if self.sig != b"ATOM":
            raise ValueError("no ATOM signature at 0x%04x (got %r)" % (self.rom_hdr + 4, self.sig))
        (self.subsys_vid, self.subsys_id) = struct.unpack_from("<HH", self.d, self.rom_hdr + 0x18)
        self.master_hw = struct.unpack_from("<H", self.d, self.rom_hdr + 0x1E)[0]
        self.master_data = struct.unpack_from("<H", self.d, self.rom_hdr + 0x20)[0]
        self.tables = {}
        self._parse_master_data()

    def hdr_at(self, off):
        return struct.unpack_from("<HBB", self.d, off)  # size, fmt_rev, cont_rev

    def _parse_master_data(self):
        base = self.master_data
        self.md_size, self.md_fmt, self.md_cont = self.hdr_at(base)
        n = (self.md_size - 4) // 2
        for i in range(n):
            off = struct.unpack_from("<H", self.d, base + 4 + i * 2)[0]
            name = MASTER_LIST_V2_1[i] if i < len(MASTER_LIST_V2_1) else "unknown%d" % i
            self.tables[name] = off
        self.md_entries = n

    # ---------------- gpio_pin_lut ----------------
    def gpio_pin_lut(self):
        off = self.tables.get("gpio_pin_lut", 0)
        if not off:
            return None, []
        size, fmt, cont = self.hdr_at(off)
        count = (size - 4) // 8
        pins = []
        for i in range(count):
            eo = off + 4 + i * 8
            raw = self.d[eo:eo + 8]
            (reg, bitshift, maskshift, gpio_id, resv) = struct.unpack("<IBBBB", raw)
            pins.append(dict(index=i, file_off=eo, raw=raw.hex(" "),
                             data_a_reg_index=reg, gpio_bitshift=bitshift,
                             gpio_mask_bitshift=maskshift, gpio_id=gpio_id,
                             reserved=resv,
                             hw_cap=bool(gpio_id & I2C_HW_CAP),
                             engine_id=(gpio_id & I2C_HW_ENGINE_ID_MASK) >> 4,
                             lane_mux=gpio_id & I2C_HW_LANE_MUX))
        return dict(offset=off, size=size, fmt_rev=fmt, cont_rev=cont, count=count), pins

    # ---------------- firmwareinfo ----------------
    def firmwareinfo(self):
        off = self.tables.get("firmwareinfo", 0)
        if not off:
            return None
        size, fmt, cont = self.hdr_at(off)
        info = dict(offset=off, size=size, fmt_rev=fmt, cont_rev=cont)
        # board_i2c_feature_* live at the same offset in v3_1..v3_4:
        #   header 4 + 6*u32 (24) + 4*u16 (8) + 1+1+2 (4) + 2*u32 (8) = 0x30
        bi = off + 0x30
        if fmt == 3 and cont in (1, 2, 3, 4):
            (fid, gid, saddr, r3) = struct.unpack_from("<BBBB", self.d, bi)
            info.update(board_i2c_off=bi,
                        board_i2c_raw=self.d[bi:bi + 4].hex(" "),
                        board_i2c_feature_id=fid,
                        board_i2c_feature_gpio_id=gid,
                        board_i2c_feature_slave_addr=saddr,
                        byte3=r3,
                        oem_i2c_present=(fid == 0x2))
        return info

    # ---------------- displayobjectinfo ----------------
    def display_objects(self):
        off = self.tables.get("displayobjectinfo", 0)
        if not off:
            return None, []
        size, fmt, cont = self.hdr_at(off)
        supported, npath, resv = struct.unpack_from("<HBB", self.d, off + 4)
        paths = []
        # atom_display_object_path_v2 = 7*u16 + 2*u8 = 16 bytes
        # atom_display_object_path_v3 = 8*u16       = 16 bytes
        if cont == 4:
            pathsz, pathfmt = 16, "v2"
        elif cont == 5:
            pathsz, pathfmt = 16, "v3"
        else:
            return dict(offset=off, size=size, fmt_rev=fmt, cont_rev=cont,
                        supporteddevices=supported, number_of_path=npath,
                        unsupported=True), []
        for i in range(npath):
            po = off + 8 + i * pathsz
            if cont == 4:
                (dobj, drec, enc, extenc, encrec, extencrec, devtag, prio, r) = \
                    struct.unpack_from("<HHHHHHHBB", self.d, po)  # 16 bytes
            else:
                (dobj, drec, enc, r1, r2, r3, devtag, r4) = \
                    struct.unpack_from("<HHHHHHHH", self.d, po)
                extenc = encrec = extencrec = 0
                prio = 0
            recs = self._records(off + drec) if drec else []
            paths.append(dict(index=i, file_off=po, display_objid=dobj,
                              display_objid_str=objid_str(dobj),
                              disp_recordoffset=drec,
                              disp_record_file_off=(off + drec) if drec else 0,
                              encoderobjid=enc, device_tag=devtag,
                              records=recs))
        return dict(offset=off, size=size, fmt_rev=fmt, cont_rev=cont,
                    supporteddevices=supported, number_of_path=npath,
                    path_struct=pathfmt), paths

    def _records(self, off):
        out = []
        for _ in range(32):  # BIOS_MAX_NUM_RECORD
            if off + 2 > len(self.d):
                break
            rtype, rsize = self.d[off], self.d[off + 1]
            if rtype == ATOM_RECORD_END_TYPE or rsize == 0:
                break
            rec = dict(type=rtype, size=rsize, file_off=off,
                       raw=self.d[off:off + rsize].hex(" "))
            if rtype == ATOM_I2C_RECORD_TYPE and rsize >= 4:
                i2c_id = self.d[off + 2]
                rec.update(i2c_id=i2c_id,
                           i2c_slave_addr=self.d[off + 3],
                           hw_cap=bool(i2c_id & I2C_HW_CAP),
                           engine_id=(i2c_id & I2C_HW_ENGINE_ID_MASK) >> 4,
                           lane_mux=i2c_id & I2C_HW_LANE_MUX)
            out.append(rec)
            off += rsize
        return out

    # ---------------- voltageobject_info (non-display i2c consumers) -------
    def voltage_objects(self):
        off = self.tables.get("voltageobject_info", 0)
        if not off:
            return None, []
        size, fmt, cont = self.hdr_at(off)
        objs = []
        p = off + 4
        while p + 4 <= off + size:
            vtype, vmode, osize = self.d[p], self.d[p + 1], \
                struct.unpack_from("<H", self.d, p + 2)[0]
            if osize == 0:
                break
            objs.append(dict(file_off=p, voltage_type=vtype, voltage_mode=vmode,
                             size=osize, raw=self.d[p:p + min(osize, 32)].hex(" ")))
            p += osize
        return dict(offset=off, size=size, fmt_rev=fmt, cont_rev=cont), objs


def load_regmap(path):
    """mmDC_GPIO_* / mm*_A style dword register index -> name, from an
    asic_reg offset header."""
    m = {}
    if not path or not os.path.exists(path):
        return m
    pat = re.compile(r"^#define\s+(mm[A-Za-z0-9_]+)\s+(0x[0-9a-fA-F]+|\d+)\s*$")
    with open(path, "r", errors="replace") as f:
        for line in f:
            g = pat.match(line)
            if not g:
                continue
            name, val = g.group(1), g.group(2)
            if name.endswith("_BASE_IDX"):
                continue
            v = int(val, 0)
            m.setdefault(v, []).append(name)
    return m


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    if as_json:
        args.remove("--json")
    regmap_path = None
    regbase = 0
    if "--regmap" in args:
        i = args.index("--regmap")
        regmap_path = args[i + 1]
        del args[i:i + 2]
    if "--regbase" in args:
        i = args.index("--regbase")
        regbase = int(args[i + 1], 0)
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    blob = open(args[0], "rb").read()
    a = Atom(blob)
    regmap = load_regmap(regmap_path)

    def rname(v):
        """v is an absolute SOC15 dword index; subtract the IP base to get the
        mmXXX offset that the asic_reg header defines."""
        names = regmap.get(v - regbase)
        if not names:
            return ""
        # prefer the DC_GPIO ones
        pref = [n for n in names if "DC_GPIO" in n]
        return (pref or names)[0]

    out = {}
    out["file"] = os.path.abspath(args[0])
    out["file_size"] = len(blob)
    out["rom_header_off"] = a.rom_hdr
    out["rom_header"] = dict(structuresize=a.rh_size, format_revision=a.rh_fmt,
                             content_revision=a.rh_cont,
                             subsystem_vendor_id="0x%04x" % a.subsys_vid,
                             subsystem_id="0x%04x" % a.subsys_id,
                             masterhwfunction_offset="0x%04x" % a.master_hw,
                             masterdatatable_offset="0x%04x" % a.master_data)
    md = {}
    for name, off in a.tables.items():
        e = dict(offset=off)
        if off:
            s, f, c = a.hdr_at(off)
            e.update(size=s, fmt_rev=f, cont_rev=c)
        md[name] = e
    out["master_data_table"] = dict(offset=a.master_data, size=a.md_size,
                                    fmt_rev=a.md_fmt, cont_rev=a.md_cont,
                                    entries=a.md_entries, tables=md)

    lut_hdr, pins = a.gpio_pin_lut()
    out["gpio_pin_lut"] = dict(header=lut_hdr, pins=pins)
    for p in pins:
        p["reg_name"] = rname(p["data_a_reg_index"])

    fw = a.firmwareinfo()
    out["firmwareinfo"] = fw

    doi_hdr, paths = a.display_objects()
    out["displayobjectinfo"] = dict(header=doi_hdr, paths=paths)

    vhdr, vobjs = a.voltage_objects()
    out["voltageobject_info"] = dict(header=vhdr, objects=vobjs)

    # ---- cross reference ----
    consumers = {}
    for p in paths:
        for r in p["records"]:
            if r["type"] == ATOM_I2C_RECORD_TYPE and "i2c_id" in r:
                consumers.setdefault(r["i2c_id"], []).append(
                    dict(kind="connector", who=p["display_objid_str"],
                         path_index=p["index"], slave_addr=r["i2c_slave_addr"],
                         record_off=r["file_off"]))
    if fw and fw.get("oem_i2c_present"):
        consumers.setdefault(fw["board_i2c_feature_gpio_id"], []).append(
            dict(kind="oem_board_i2c", who="firmwareinfo.board_i2c_feature",
                 slave_addr=fw["board_i2c_feature_slave_addr"],
                 record_off=fw["board_i2c_off"]))
    for p in pins:
        p["consumers"] = consumers.get(p["gpio_id"], [])
    out["orphan_i2c_ids"] = sorted(
        i for i in consumers if i not in {p["gpio_id"] for p in pins})

    if as_json:
        print(json.dumps(out, indent=1, default=str))
        return 0

    # ---------------- human readable ----------------
    P = print
    P("=== %s (%d bytes) ===" % (out["file"], len(blob)))
    P("ATOM_ROM_HEADER @0x%04x  structuresize=%d  fmt=%d cont=%d  sig=%r"
      % (a.rom_hdr, a.rh_size, a.rh_fmt, a.rh_cont, a.sig))
    P("  subsystem_vendor_id=0x%04x  subsystem_id=0x%04x" % (a.subsys_vid, a.subsys_id))
    P("  masterhwfunction_offset=0x%04x  masterdatatable_offset=0x%04x"
      % (a.master_hw, a.master_data))
    P("")
    P("=== master data table @0x%04x (size=%d fmt=%d cont=%d, %d entries) ==="
      % (a.master_data, a.md_size, a.md_fmt, a.md_cont, a.md_entries))
    for i, (name, off) in enumerate(a.tables.items()):
        if off:
            s, f, c = a.hdr_at(off)
            P("  [%2d] %-24s 0x%04x  (size=%5d rev %d.%d)" % (i, name, off, s, f, c))
        else:
            P("  [%2d] %-24s ABSENT" % (i, name))
    P("")
    if lut_hdr:
        P("=== gpio_pin_lut @0x%04x  size=%d rev %d.%d  -> %d x atom_gpio_pin_assignment ==="
          % (lut_hdr["offset"], lut_hdr["size"], lut_hdr["fmt_rev"],
             lut_hdr["cont_rev"], lut_hdr["count"]))
        P("  idx  file_off  raw bytes               data_a_reg  reg name              bitshift maskshift gpio_id  HW_CAP eng lane  consumers")
        for p in pins:
            cons = ", ".join("%s:%s(slave 0x%02x)" % (c["kind"], c["who"], c["slave_addr"])
                             for c in p["consumers"]) or "-"
            P("  %3d  0x%04x    %-23s 0x%08x  %-20s %3d      %3d       0x%02x     %-5s  %d   %2d   %s"
              % (p["index"], p["file_off"], p["raw"], p["data_a_reg_index"],
                 p["reg_name"] or "?", p["gpio_bitshift"], p["gpio_mask_bitshift"],
                 p["gpio_id"], p["hw_cap"], p["engine_id"], p["lane_mux"], cons))
    P("")
    if fw:
        P("=== firmwareinfo @0x%04x size=%d rev %d.%d ==="
          % (fw["offset"], fw["size"], fw["fmt_rev"], fw["cont_rev"]))
        if "board_i2c_feature_id" in fw:
            P("  board_i2c_* @0x%04x raw=[%s]" % (fw["board_i2c_off"], fw["board_i2c_raw"]))
            P("  board_i2c_feature_id      = 0x%02x  (OEM i2c present: %s; DC requires ==0x2)"
              % (fw["board_i2c_feature_id"], fw["oem_i2c_present"]))
            P("  board_i2c_feature_gpio_id = 0x%02x  (HW_CAP=%s engine=%d lane_mux=%d)"
              % (fw["board_i2c_feature_gpio_id"],
                 bool(fw["board_i2c_feature_gpio_id"] & I2C_HW_CAP),
                 (fw["board_i2c_feature_gpio_id"] & I2C_HW_ENGINE_ID_MASK) >> 4,
                 fw["board_i2c_feature_gpio_id"] & I2C_HW_LANE_MUX))
            P("  board_i2c_feature_slave_addr = 0x%02x  (8-bit) -> 7-bit 0x%02x"
              % (fw["board_i2c_feature_slave_addr"],
                 fw["board_i2c_feature_slave_addr"] >> 1))
            P("  byte3 (reserved3 / ras_rom_i2c_slave_addr) = 0x%02x" % fw["byte3"])
    P("")
    if doi_hdr:
        P("=== displayobjectinfo @0x%04x size=%d rev %d.%d  paths=%d supporteddevices=0x%04x ==="
          % (doi_hdr["offset"], doi_hdr["size"], doi_hdr["fmt_rev"],
             doi_hdr["cont_rev"], doi_hdr["number_of_path"],
             doi_hdr["supporteddevices"]))
        for p in paths:
            P("  path %d @0x%04x  %s  device_tag=0x%04x  enc=0x%04x  records@0x%04x"
              % (p["index"], p["file_off"], p["display_objid_str"], p["device_tag"],
                 p["encoderobjid"], p["disp_record_file_off"]))
            for r in p["records"]:
                extra = ""
                if r["type"] == ATOM_I2C_RECORD_TYPE and "i2c_id" in r:
                    extra = ("  i2c_id=0x%02x (HW_CAP=%s eng=%d lane=%d) slave=0x%02x"
                             % (r["i2c_id"], r["hw_cap"], r["engine_id"],
                                r["lane_mux"], r["i2c_slave_addr"]))
                P("      rec type=%2d size=%2d @0x%04x [%s]%s"
                  % (r["type"], r["size"], r["file_off"], r["raw"], extra))
    P("")
    if vhdr:
        P("=== voltageobject_info @0x%04x size=%d rev %d.%d  (%d objects) ==="
          % (vhdr["offset"], vhdr["size"], vhdr["fmt_rev"], vhdr["cont_rev"], len(vobjs)))
        for o in vobjs:
            P("  @0x%04x voltage_type=0x%02x voltage_mode=0x%02x size=%d [%s]"
              % (o["file_off"], o["voltage_type"], o["voltage_mode"], o["size"], o["raw"]))
    if out["orphan_i2c_ids"]:
        P("")
        P("!! i2c ids referenced by a consumer but with NO gpio_pin_lut entry: %s"
          % ["0x%02x" % i for i in out["orphan_i2c_ids"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
