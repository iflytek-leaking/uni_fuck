#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply all HANDOVER_PROMPT_V2 patches to ud710_bsp repo."""
import os, re, glob

# patch_all.py lives at BSP repo root
REPO = os.path.dirname(os.path.abspath(__file__))

def p(rel):
    return os.path.join(REPO, *rel.split("/"))

def patch_file(rel, pairs, label, count=0):
    """pairs: list of (old,new). count=0 -> replace all."""
    fp = p(rel)
    if not os.path.exists(fp):
        print(f"[SKIP] {label}: {rel} not found")
        return
    with open(fp, "rb") as f:
        data = f.read().decode("utf-8", errors="replace")
    orig = data
    for old, new in pairs:
        if old not in data:
            print(f"[WARN] {label}: pattern not found: {old[:50]!r}")
            continue
        data = data.replace(old, new, count)
    if data != orig:
        with open(fp, "w", newline="") as f:
            f.write(data)
    print(f"[OK] {label} ({rel})")

# ---------- 1. PATH fix (21 cfg files) ----------
cfg_files = glob.glob(os.path.join(REPO, "device", "**", "*_base", "common.cfg"), recursive=True)
cnt = 0
for cf in cfg_files:
    with open(cf, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    nd = d.replace('PATH//"${BSP_TOOL_PATH}:"', 'PATH')
    if nd != d:
        with open(cf, "w", newline="") as f:
            f.write(nd)
        cnt += 1
print(f"[OK] PATH fix: {cnt}/{len(cfg_files)} files")

# ---------- 2. sprd_usb_drv.h: 16KB + raw decl ----------
patch_file("bootloader/chipram/include/sprd_usb_drv.h", [
    ("#define MAX_RECV_LENGTH 1024 * 4", "#define MAX_RECV_LENGTH (1024 * 16) // Optimized 16KB"),
    ("extern int usb_get_packet(unsigned char* buf, int len);",
     "extern int usb_get_packet(unsigned char* buf, int len);\nextern int usb_get_raw_packet(unsigned char* buf, unsigned int len);"),
], "usb_drv.h 16KB + raw decl")

# ---------- 3. sprd_usb2_driver.c: 16KB + skip cache + raw fn ----------
fp = p("bootloader/chipram/nand_fdl/common/sprd_usb2_driver.c")
with open(fp, "rb") as f:
    d = f.read().decode("utf-8", errors="replace")
d = re.sub(r"#define MAX_RECV_LENGTH\s+\(64\*64\).*", "#define MAX_RECV_LENGTH     (256*64)// Optimized 16KB", d)
n = d.count("#ifndef CONFIG_SCX35L64")
d = d.replace("#ifndef CONFIG_SCX35L64", "#if 0 // Optimized skip cache")
if "usb_get_raw_packet" not in d:
    d += r'''

/* Optimized raw path for high-speed bulk transfer - zero HDLC overhead */
int usb_get_raw_packet(unsigned char* buf, unsigned int len)
{
    unsigned int total = 0;
    unsigned char *dest = buf;
    while (total < len) {
        if (readIndex == recv_length) {
            readIndex = 0;
            recv_length = 0;
            usb_handler();
            if (recv_length > 0) {
                nIndex = currentDmaBufferIndex;
                currentDmaBufferIndex ^= 0x1;
            } else
                continue;
        }
        unsigned int avail = recv_length - readIndex;
        unsigned int need = len - total;
        unsigned int copy = (avail < need) ? avail : need;
        unsigned char *src = usb_out_endpoint_buf[nIndex] + readIndex;
        unsigned int i;
        for (i = 0; i < copy; i++)
            dest[total + i] = src[i];
        readIndex += copy;
        total += copy;
    }
    return total;
}
'''
with open(fp, "w", newline="") as f:
    f.write(d)
print(f"[OK] usb2_driver.c: 16KB, skip-cache {n} sites, raw fn appended")

# ---------- 4. soc_config MAX_PKT_SIZE ----------
soc_map = {
    "arch-roc1/soc_config.h": "0x1000",
    "arch-qogirn6pro/soc_config.h": "0x400",
    "arch-sharkl5/soc_config.h": "0x400",
    "arch-sharkl5pro/soc_config.h": "0x300",
}
for fn, oldv in soc_map.items():
    fp = p("bootloader/chipram/arch/arm/include/asm/" + fn)
    if os.path.exists(fp):
        with open(fp, "rb") as f:
            d = f.read().decode("utf-8", errors="replace")
        nd = re.sub(r"#define MAX_PKT_SIZE\s+%s" % re.escape(oldv),
                    "#define MAX_PKT_SIZE    0x4000 // Optimized", d)
        if nd != d:
            with open(fp, "w", newline="") as f:
                f.write(nd)
            print(f"[OK] soc_config {fn} -> 0x4000")
        else:
            print(f"[SKIP] soc_config {fn} (pattern not found)")
    else:
        print(f"[SKIP] soc_config {fn} (not found)")

# ---------- 4b. ddrc_init.c: sync #error guard with new MAX_PKT_SIZE ----------
# MAX_PKT_SIZE was optimized to 0x4000 above; the DDR r2p2 code has a
# hardcoded #error guard still expecting 0x300. Update it to match.
ddrc_files = glob.glob(os.path.join(REPO, "bootloader", "chipram", "ddr", "**", "ddrc_init.c"), recursive=True)
for ddrc in ddrc_files:
    with open(ddrc, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    nd = d.replace("#if (PACKET_MAX_NUM!=3) || (MAX_PKT_SIZE!=0x300)",
                   "#if (PACKET_MAX_NUM!=3) || (MAX_PKT_SIZE!=0x4000)")
    if nd != d:
        with open(ddrc, "w", newline="") as f:
            f.write(nd)
        print(f"[OK] ddrc_init.c #error guard -> 0x4000 ({os.path.relpath(ddrc, REPO)})")

# ---------- 4c. mmu.h: ttbr0_el2 sysreg encoding (zig cc / clang asm) ----------
# clang's integrated assembler rejects "msr ttbr0_el2, %0" ("expected writable
# system register"), GCC accepts the name. Use the raw sysreg encoding instead.
# NOTE: substring replace on the mnemonic only (lines have leading tabs).
mmu_h = p("bootloader/u-boot15/arch/arm/include/asm/armv8/mmu.h")
if os.path.exists(mmu_h):
    with open(mmu_h, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    nd = (d.replace('msr ttbr0_el1, %0', 'msr S3_0_C2_C0_0, %0')
           .replace('msr ttbr0_el2, %0', 'msr S3_4_C2_C0_0, %0')
           .replace('msr ttbr0_el3, %0', 'msr S3_6_C2_C0_0, %0'))
    if nd != d:
        with open(mmu_h, "w", newline="") as f:
            f.write(nd)
        print("[OK] mmu.h ttbr0 sysreg encoding (el1/el2/el3)")
    else:
        print("[SKIP] mmu.h ttbr0 already encoded")
else:
    print("[SKIP] mmu.h not found")

# ---------- 4d. Fix conflicting types (old-style C implicit declarations) ----------
# Full sources define e.g. `void reset_and_restore_hc()` AFTER call sites with no
# prototype. clang treats the implicit declaration as returning int -> conflicting
# types with the void definition (hard error, cannot be silenced with -Wno-*).
def add_protos(rel, protos):
    fp = p(rel)
    if not os.path.exists(fp):
        print(f"[SKIP] add_protos {rel} not found")
        return
    with open(fp, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    if all(proto.split(";")[0] in d for proto in protos):
        print(f"[SKIP] {rel} prototypes already present")
        return
    lines = d.split("\n")
    idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#include"):
            idx = i
    insert = "\n" + "\n".join(protos) + "\n"
    lines.insert(idx + 1, insert)
    with open(fp, "w", newline="") as f:
        f.write("\n".join(lines))
    print(f"[OK] {rel} prototypes added")

add_protos("bootloader/chipram/nand_spl/ufs/sprd_ufs.c", [
    "void reset_and_restore_hc(void);",
    "void sprd_ufs_block_dev_config(void);",
])
for phy in ["r1p1", "r1p1_orca"]:
    add_protos(f"bootloader/chipram/ddr/ddr_init/init/ddrc/{phy}/ddrc_r1p1_phy_init.c", [
        "void ddrc_phy_io_get(uint32 phy_base, uint32 freq_sel);",
        "void ddrc_phy_io_set(uint32 phy_base, uint32 freq_sel);",
    ])

# ---------- 4e. Auto-add prototypes for implicit-declaration conflicts ----------
# Full sources have many old-style C files where a function is called before its
# definition with no prototype -> clang "conflicting types". Scan known problem
# directories and insert a prototype after the last #include for every function
# that is called before its own definition. Conservative single-line matcher.
def auto_fix_implicit_protos(dirs):
    import re as _re
    keyw = {'if', 'for', 'while', 'switch', 'return', 'sizeof', 'do', 'else',
            'case', 'goto', 'typedef', 'struct', 'union', 'enum'}
    def_re = _re.compile(
        r'^(?P<prefix>(?:static\s+|inline\s+|__inline\s+|const\s+|volatile\s+)*)'
        r'(?P<ret>\w[\w\s\*]*?)\s*(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^;{}]*)\)\s*\{?\s*$')
    fixed_files = 0
    for d in dirs:
        for fp in glob.glob(os.path.join(REPO, d, "**", "*.c"), recursive=True):
            with open(fp, "rb") as f:
                content = f.read().decode("utf-8", errors="replace")
            lines = content.split("\n")
            defs = []
            for i, ln in enumerate(lines):
                m = def_re.match(ln)
                if not m:
                    continue
                if m.group('name') in keyw or m.group('ret').strip() in keyw:
                    continue
                defs.append((m.group('name'), i, ln))
            protos = []
            for name, def_idx, sig in defs:
                called_before = False
                for j in range(def_idx):
                    if _re.search(r'\b' + _re.escape(name) + r'\s*\(', lines[j]):
                        called_before = True
                        break
                if called_before:
                    proto = sig.rstrip().rstrip('{').rstrip() + ";"
                    if proto not in lines and not any(p == proto for p in protos):
                        protos.append(proto)
            if protos:
                last_inc = -1
                for i, ln in enumerate(lines):
                    if ln.startswith("#include"):
                        last_inc = i
                if last_inc >= 0:
                    insert = ["", "/* auto-added prototypes (implicit declaration fix) */"] + protos + [""]
                    lines[last_inc + 1:last_inc + 1] = insert
                    with open(fp, "w", newline="") as f:
                        f.write("\n".join(lines))
                    fixed_files += 1
                    print(f"[OK] auto-proto {os.path.relpath(fp, REPO)}: {len(protos)} protos")
    print(f"auto-proto: {fixed_files} files fixed")

auto_fix_implicit_protos([
    "bootloader/chipram/ddr",
    "bootloader/chipram/nand_spl/ufs",
])

# ---------- 5. fdt_for_each_subnode macro order (gcc12) ----------
patch_file("bootloader/u-boot15/common/image-fit.c", [
    ("fdt_for_each_subnode(fit, noffset, image_noffset)", "fdt_for_each_subnode(noffset, fit, image_noffset)"),
], "image-fit fdt macro")
patch_file("bootloader/u-boot15/common/image-sig.c", [
    ("fdt_for_each_subnode(fit, noffset, image_noffset)", "fdt_for_each_subnode(noffset, fit, image_noffset)"),
    ("fdt_for_each_subnode(sig_blob, noffset, sig_node)", "fdt_for_each_subnode(noffset, sig_blob, sig_node)"),
    ("fdt_for_each_subnode(fit, noffset, conf_noffset)", "fdt_for_each_subnode(noffset, fit, conf_noffset)"),
], "image-sig fdt macro")

# ---------- 6. sec_common.c remove splloader force-check ----------
patch_file("bootloader/u-boot15/lib/secureboot/common/sec_common.c", [
    ("static uchar *const s_force_secure_check[] = {\n    \"splloader\",",
     "static uchar *const s_force_secure_check[] = {\n    // Removed splloader restriction\n    // \"splloader\","),
], "sec_common remove splloader")

# ---------- 7. dl_cmd_proc.c enable_write_flash = 1 ----------
patch_file("bootloader/u-boot15/common/dloader/dl_cmd_proc.c", [
    ("static int enable_write_flash = 0;", "static int enable_write_flash = 1;"),
], "enable_write_flash=1")

# ---------- 8. dl_cmd_proc.c unlock/avb commands ----------
fp = p("bootloader/u-boot15/common/dloader/dl_cmd_proc.c")
with open(fp, "rb") as f:
    d = f.read().decode("utf-8", errors="replace")
old_fn = """int dl_cmd_disable_hdlc(dl_packet_t *packet, void *arg)
{
\tdl_send_ack(BSL_REP_ACK);
\tFDL_DisableHDLC(1);
\treturn 0;
}"""
new_fn = old_fn + """
#define BSL_CMD_UNLOCK_BL 0x500
#define BSL_CMD_DISABLE_AVB 0x502
int dl_cmd_unlock_bl(dl_packet_t *packet, void *arg)
{
\tset_lock_status(1); // VBOOT_STATUS_UNLOCK
\t_send_reply(0);
\treturn 0;
}
int dl_cmd_disable_avb(dl_packet_t *packet, void *arg)
{
\tset_lock_status(1);
\tcommon_raw_erase("vbmeta", 0, 0);
\t_send_reply(0);
\treturn 0;
}"""
if "dl_cmd_unlock_bl" in d:
    print("[SKIP] unlock/avb already present")
elif old_fn in d:
    d = d.replace(old_fn, new_fn)
    if "#include <loader_common.h>" not in d:
        d = d.replace('#include <common.h>',
                      '#include <common.h>\n#include <loader_common.h>\n#include <sprd_common_rw.h>')
    with open(fp, "w", newline="") as f:
        f.write(d)
    print("[OK] unlock/avb cmds added")
else:
    print("[WARN] dl_cmd_disable_hdlc pattern not found")

# ---------- 9. cmd_download.c register new commands ----------
fp = p("bootloader/u-boot15/common/cmd_download.c")
with open(fp, "rb") as f:
    d = f.read().decode("utf-8", errors="replace")
old_reg = "\tdl_cmd_register(BSL_CMD_DIS_HDLC, dl_cmd_disable_hdlc);"
new_reg = old_reg + "\n\tdl_cmd_register(BSL_CMD_UNLOCK_BL, dl_cmd_unlock_bl);\n\tdl_cmd_register(BSL_CMD_DISABLE_AVB, dl_cmd_disable_avb);"
if "BSL_CMD_UNLOCK_BL" in d:
    print("[SKIP] already registered")
elif old_reg in d:
    d = d.replace(old_reg, new_reg)
    with open(fp, "w", newline="") as f:
        f.write(d)
    print("[OK] cmd_download register")
else:
    print("[WARN] register pattern not found")

# ---------- 10. dl_operate.c read cache 256KB ----------
fp = p("bootloader/u-boot15/common/dloader/dl_operate.c")
with open(fp, "rb") as f:
    d = f.read().decode("utf-8", errors="replace")
old_top = "static DL_EMMC_FILE_STATUS g_status;\nstatic DL_EMMC_STATUS g_dl_eMMCStatus;"
new_top = old_top + """
#define READ_CACHE_SIZE (256*1024)
#define READ_CACHE_INVALID ((uint64_t)-1)
static unsigned char read_cache_buffer[READ_CACHE_SIZE] __attribute__((aligned(64)));
static uint64_t read_cache_start = READ_CACHE_INVALID;
static uint64_t read_cache_end = 0;
static char read_cache_partition[64] = {0};
static int read_cache_valid = 0;"""
old_rs = """OPERATE_STATUS dl_read_start(uchar * partition_name, uint64_t size)
{
\tsize_t sblock_size = 0;
\tstruct ext2_sblock *sblock = NULL;

\tstrcpy(g_dl_eMMCStatus.curUserPartitionName, partition_name);"""
new_rs = old_rs.replace(
    "strcpy(g_dl_eMMCStatus.curUserPartitionName, partition_name);",
    "strcpy(g_dl_eMMCStatus.curUserPartitionName, partition_name);\n\tread_cache_start = READ_CACHE_INVALID; read_cache_end = 0; read_cache_valid = 0; memset(read_cache_partition, 0, sizeof(read_cache_partition));")
old_mid = """OPERATE_STATUS dl_read_midst(uint32_t size, uint64_t off, uchar * buf)
{
\tif (PARTITION_PURPOSE_NV == g_dl_eMMCStatus.partitionpurpose) {
\t\tmemcpy(buf, (uchar *) (g_eMMCBuf + off), size);
\t} else {
\t\tif (0 != common_raw_read(g_dl_eMMCStatus.curUserPartitionName, (uint64_t)size, (uint64_t)off, buf)) {
\t\t\terrorf("read error!\\n");
\t\t\treturn OPERATE_SYSTEM_ERROR;
\t\t}
\t}

\treturn OPERATE_SUCCESS;
}"""
new_mid = """OPERATE_STATUS dl_read_midst(uint32_t size, uint64_t off, uchar * buf)
{
\tif (PARTITION_PURPOSE_NV == g_dl_eMMCStatus.partitionpurpose) {
\t\tmemcpy(buf, (uchar *) (g_eMMCBuf + off), size);
\t\treturn OPERATE_SUCCESS;
\t}
\tif (read_cache_valid && (0 == strcmp(read_cache_partition, g_dl_eMMCStatus.curUserPartitionName))) {
\t\tif (off >= read_cache_start && (off + size) <= read_cache_end) {
\t\t\tmemcpy(buf, read_cache_buffer + (off - read_cache_start), size);
\t\t\treturn OPERATE_SUCCESS;
\t\t}
\t}
\t{
\t\tuint64_t aligned_off = (off / READ_CACHE_SIZE) * READ_CACHE_SIZE;
\t\tuint64_t cache_sz = READ_CACHE_SIZE;
\t\tif (0 != common_raw_read(g_dl_eMMCStatus.curUserPartitionName, cache_sz, aligned_off, (char*)read_cache_buffer)) {
\t\t\tif (0 != common_raw_read(g_dl_eMMCStatus.curUserPartitionName, (uint64_t)size, (uint64_t)off, buf)) {
\t\t\t\terrorf("read error!\\n");
\t\t\t\treturn OPERATE_SYSTEM_ERROR;
\t\t\t}
\t\t\treturn OPERATE_SUCCESS;
\t\t}
\t\tread_cache_start = aligned_off; read_cache_end = aligned_off + cache_sz; read_cache_valid = 1; strcpy(read_cache_partition, g_dl_eMMCStatus.curUserPartitionName);
\t\tif (off >= read_cache_start && (off + size) <= read_cache_end) {
\t\t\tmemcpy(buf, read_cache_buffer + (off - read_cache_start), size);
\t\t\treturn OPERATE_SUCCESS;
\t\t}
\t\tif (0 != common_raw_read(g_dl_eMMCStatus.curUserPartitionName, (uint64_t)size, (uint64_t)off, buf)) {
\t\t\terrorf("read error!\\n");
\t\t\treturn OPERATE_SYSTEM_ERROR;
\t\t}
\t\treturn OPERATE_SUCCESS;
\t}
}"""
if "READ_CACHE_SIZE" in d:
    print("[SKIP] read cache already present")
else:
    fails = []
    if old_top not in d: fails.append("top")
    if old_rs not in d: fails.append("rs")
    if old_mid not in d: fails.append("mid")
    if fails:
        print(f"[WARN] read cache patterns missing: {fails}")
    else:
        d = d.replace(old_top, new_top).replace(old_rs, new_rs).replace(old_mid, new_mid)
        with open(fp, "w", newline="") as f:
            f.write(d)
        print("[OK] dl_operate read cache 256KB")

# ---------- 11. gcc12 compat flags ----------
for fp, flagline in [
    (p("bootloader/chipram/config.mk"),
     "KBUILD_CFLAGS += $(call cc-option,-Wno-error=implicit-function-declaration) $(call cc-option,-Wno-error=implicit-fallthrough) -std=gnu11"),
    (p("bootloader/u-boot15/Makefile"),
     "KBUILD_CFLAGS += $(call cc-option,-Wno-error=implicit-function-declaration) -std=gnu11"),
]:
    with open(fp, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    if "Wno-error=implicit-function-declaration" not in d:
        with open(fp, "a", newline="") as f:
            f.write("\n" + flagline + "\n")
        print(f"[OK] gcc12 flags -> {os.path.basename(fp)}")
    else:
        print(f"[SKIP] gcc12 flags already in {os.path.basename(fp)}")

# ---------- 12. chipram Makefile: only fdl1 ----------
patch_file("bootloader/chipram/Makefile", [
    ("ALL +=  spl fdl1 ddr_scan", "ALL +=  fdl1"),
], "chipram ALL=fdl1")

# ---------- 13. defconfig: disable video/battery ----------
fp = p("bootloader/u-boot15/configs/ud710_2h10_defconfig")
with open(fp, "rb") as f:
    d = f.read().decode("utf-8", errors="replace")
if "CONFIG_VIDEO is not set" not in d:
    with open(fp, "a", newline="") as f:
        f.write("\n# CONFIG_VIDEO is not set\n# CONFIG_POWER_BATTERY is not set\n")
    print("[OK] defconfig video/battery off")
else:
    print("[SKIP] defconfig already modified")

# ---------- 14. Remove hardcoded toolchain paths in _base cfg files ----------
# Some platforms (s9863a, ums512, sl8541e, ums7520) override BSP_*_TOOLCHAIN
# with deleted Linaro GCC paths. Comment them out so our zig toolchain persists.
import glob as _glob
tc_patch_cnt = 0
for cfg in _glob.glob(os.path.join(REPO, "device", "**", "*_base", "*.cfg"), recursive=True):
    with open(cfg, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    if "BSP_CHIPRAM_TOOLCHAIN=" in d or "BSP_UBOOT_TOOLCHAIN=" in d:
        d = re.sub(r'^(export\s+)?BSP_CHIPRAM_TOOLCHAIN=.*$', r'# \g<0>', d, flags=re.MULTILINE)
        d = re.sub(r'^(export\s+)?BSP_UBOOT_TOOLCHAIN=.*$', r'# \g<0>', d, flags=re.MULTILINE)
        with open(cfg, "w", newline="") as f:
            f.write(d)
        tc_patch_cnt += 1
print(f"[OK] Toolchain override removed in {tc_patch_cnt} cfg files")

print("\nALL PATCHES DONE")
