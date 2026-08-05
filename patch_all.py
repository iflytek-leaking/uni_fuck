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
        # count=0 must mean "replace all" (str.replace(old,new,0) does nothing)
        data = data.replace(old, new, count if count > 0 else -1)
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
                    if not _re.search(r'\b' + _re.escape(name) + r'\s*\(', lines[j]):
                        continue
                    # skip comment lines (function names inside // or /* */
                    # blocks are not call sites, e.g. fdt_support.c's doc comment
                    # " * fdt_fixup_mtdparts(blob, nodes, ARRAY_SIZE(nodes));")
                    if lines[j].lstrip().startswith(('//', '*', '/*')):
                        continue
                    # skip lines that are themselves definitions (same-name
                    # overloads/conditional variants would otherwise count as calls)
                    if def_re.match(lines[j]):
                        continue
                    # skip the OPENING line of a multi-line definition such as
                    # "static int do_bdinfo(cmd_tbl_t *cmdtp, int flag, int argc,"
                    # (fn_name( ... with NO closing ')' on the line and a return
                    # type before the name). Those are not call sites; treating
                    # them as calls spuriously protos every other definition.
                    if _re.match(
                            r'^(?:static\s+|inline\s+|__inline\s+|const\s+|volatile\s+)*'
                            r'\w[\w\s\*]*?\s+' + _re.escape(name) + r'\s*\([^)]*$',
                            lines[j]):
                        continue
                    called_before = True
                    break
                if called_before:
                    proto = sig.rstrip().rstrip('{').rstrip() + ";"
                    if proto not in lines and not any(p == proto for p in protos):
                        protos.append(proto)
            if protos:
                # Insert after the LAST #include that sits at preprocessor depth
                # 0 (outside any #if/#ifdef/#ifndef). This keeps the prototypes
                # in the unconditional top-of-file section (a #ifdef'd last
                # include, e.g. fastboot.c's CONFIG_NAND_BOOT block, would hide
                # them) while still being AFTER every unconditional include so
                # types used by the prototypes are already declared.
                depth = 0
                last_inc0 = -1
                for i, ln in enumerate(lines):
                    t = ln.lstrip()
                    if t.startswith("#if"):
                        depth += 1
                    elif t.startswith("#endif"):
                        if depth > 0:
                            depth -= 1
                    if depth == 0 and ln.startswith("#include"):
                        last_inc0 = i
                if last_inc0 >= 0:
                    insert = ["", "/* auto-added prototypes (implicit declaration fix) */"] + protos + [""]
                    lines[last_inc0 + 1:last_inc0 + 1] = insert
                    with open(fp, "w", newline="") as f:
                        f.write("\n".join(lines))
                    fixed_files += 1
                    print(f"[OK] auto-proto {os.path.relpath(fp, REPO)}: {len(protos)} protos")
    print(f"auto-proto: {fixed_files} files fixed")

auto_fix_implicit_protos([
    "bootloader/chipram/ddr",
    "bootloader/chipram/nand_spl/ufs",
    "bootloader/u-boot15/common",
])

# ---------- 4f. emmc_boot.c / ufs_boot.c: ddrc_print_debug -> printf ----------
# Generic SPL files call ddrc_print_debug under CONFIG_TEECFG_CUSTOM, but that
# function only exists in some SoCs' DDR code (orca r1p1_orca, roc1 r1p1) ->
# undefined reference on r2p2 SoCs and multiple definition on r1p1 SoCs.
# printf is universal; replace the call sites.
for _rel in ["bootloader/chipram/nand_spl/emmc_boot.c",
             "bootloader/chipram/nand_spl/ufs_boot.c"]:
    _fp = p(_rel)
    if os.path.exists(_fp):
        with open(_fp, "rb") as f:
            _d = f.read().decode("utf-8", errors="replace")
        _nd = _d.replace('ddrc_print_debug("teecfg header verify failed!\\n");',
                         'printf("teecfg header verify failed!\\n");')
        if _nd != _d:
            with open(_fp, "w", newline="") as f:
                f.write(_nd)
            print(f"[OK] {_rel}: ddrc_print_debug -> printf")
        else:
            print(f"[SKIP] {_rel}: no ddrc_print_debug call found")
    else:
        print(f"[SKIP] {_rel} not found")

# ---------- 4g. zebu (qogirn6pro) secure deps ----------
# sprd_verify.o is unconditional (obj-y) and calls sprd_set_version/sprd_get_version
# (defined in sec_efuse_api.c) and sprd_rsa_verify (defined in sprd_crypto_sw.c).
# qogirn6pro has CONFIG_SECURE_EFUSE commented out -> enable it, compile
# sec_efuse_api.o unconditionally, and add sprd_crypto_sw.o to SW_CRYPT.
patch_file("bootloader/chipram/arch/arm/include/asm/arch-qogirn6pro/soc_config.h", [
    ("//#define CONFIG_SECURE_EFUSE", "#define CONFIG_SECURE_EFUSE"),
], "qogirn6pro enable CONFIG_SECURE_EFUSE")
# secure/sprd/Makefile: sec_efuse_api.o unconditional; add sprd_crypto_sw.o to the
# non-ORCA SW_CRYPT line. The obj line uses a TAB after CONFIG_SW_CRYPT, so match
# with a regex instead of a fixed string.
sprd_mk = p("bootloader/chipram/secure/sprd/Makefile")
if os.path.exists(sprd_mk):
    with open(sprd_mk, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    d2 = d.replace("obj-$(CONFIG_SECURE_EFUSE) += sec_efuse_api.o",
                   "obj-y += sec_efuse_api.o")
    # NOTE: the ORCA branch above already lists sprd_crypto_sw.o, so a plain
    # file-wide "sprd_crypto_sw.o in d2" check is wrong. Anchor to end of line
    # so the non-ORCA line is the only one touched and the patch is idempotent.
    d2, n = re.subn(
        r'(obj-\$\(CONFIG_SW_CRYPT\))[ \t]+\+=\s*pk1\.o sec_string\.o sprd_sha256_sw\.o sprd_rsa_sw\.o$',
        r'\1\t+= pk1.o sec_string.o sprd_sha256_sw.o sprd_rsa_sw.o sprd_crypto_sw.o',
        d2, flags=re.MULTILINE)
    if n:
        print(f"[OK] secure sprd: SW_CRYPT add sprd_crypto_sw.o ({n} line)")
    elif "sprd_sha256_sw.o sprd_rsa_sw.o sprd_crypto_sw.o" in d2:
        print("[SKIP] secure sprd: sprd_crypto_sw.o already in non-ORCA SW_CRYPT")
    else:
        print("[WARN] secure sprd: SW_CRYPT pattern not found")
    if d2 != d:
        with open(sprd_mk, "w", newline="") as f:
            f.write(d2)
        print("[OK] secure sprd: sec_efuse_api.o unconditional")

# ---------- 4i. image.c boot_get_kbd HOSTCC guard ----------
# auto-proto (4e) inserts a boot_get_kbd prototype into the HOSTCC-visible
# section of image.c (after the last #include, before any #ifdef). bd_t is not
# defined under USE_HOSTCC -> HOSTCC build of tools/common/image.o fails with
# "image.c:75:35: unknown type name 'bd_t'". image.h:563-565 already declares
# boot_get_kbd under #ifdef CONFIG_SYS_BOOT_GET_KBD, so drop the redundant
# prototype; also guard the definition so HOSTCC skips it.
imgc = p("bootloader/u-boot15/common/image.c")
if os.path.exists(imgc):
    with open(imgc, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    changed = False
    proto = "int boot_get_kbd(struct lmb *lmb, bd_t **kbd);\n"
    if proto in d:
        d = d.replace(proto, "", 1)
        changed = True
        print("[OK] image.c removed redundant boot_get_kbd prototype")
    old = "int boot_get_kbd(struct lmb *lmb, bd_t **kbd)\n{\n\t*kbd = (bd_t *)(ulong)lmb_alloc_base(lmb, sizeof(bd_t), 0xf,"
    if old in d:
        if "#ifndef USE_HOSTCC\nint boot_get_kbd" in d:
            print("[SKIP] image.c boot_get_kbd HOSTCC guard already present")
        else:
            d = d.replace(old, "#ifndef USE_HOSTCC\n" + old)
            d = d.replace("}\n#endif /* CONFIG_SYS_BOOT_GET_KBD */",
                          "}\n#endif /* USE_HOSTCC */\n#endif /* CONFIG_SYS_BOOT_GET_KBD */", 1)
            changed = True
            print("[OK] image.c boot_get_kbd HOSTCC guard")
    else:
        print("[WARN] image.c boot_get_kbd definition pattern not found")
    if changed:
        with open(imgc, "w", newline="") as f:
            f.write(d)

# ---------- 4j. drivers/Makefile: gate ufs/ on CONFIG_UFS ----------
# drivers/ufs/Makefile only builds objects under "ifdef CONFIG_UFS". Platforms
# without UFS (eMMC-only s9863a/sl8541e/ums512) have no CONFIG_UFS in
# autoconf.mk, so the ufs/ dir produces no built-in.o and linking
# drivers/built-in.o fails: "cannot find drivers/ufs/built-in.o".
patch_file("bootloader/u-boot15/drivers/Makefile", [
    ("obj-y += ufs/", "obj-$(CONFIG_UFS) += ufs/"),
], "drivers ufs conditional on CONFIG_UFS")

# ---------- 4k. qogirn6pro (ums7520) efuse stub ----------
# sec_efuse_api.o is built unconditionally (needed by sprd_verify.o's
# sprd_set_version/sprd_get_version) but qogirn6pro has no efuse header/driver
# (sec_efuse.h only maps WHALE/SHARK/ROC1/ORCA/PIKE2 chips). Provide a
# lightweight header with the block macros (roc1 layout) plus a stub driver so
# the SECURE_BOOT=NONE haps/zebu targets compile and link.
def ensure_file(rel, content):
    fp = p(rel)
    if os.path.exists(fp):
        print(f"[SKIP] ensure_file {rel} (exists)")
        return
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", newline="") as f:
        f.write(content)
    print(f"[OK] ensure_file {rel}")

ensure_file("bootloader/chipram/include/security/sec_efuse_qogirn6pro_drv.h", r'''#ifndef _SEC_EFUSE_QOGIRN6PRO_DRV_H_
#define _SEC_EFUSE_QOGIRN6PRO_DRV_H_

/* Minimal driver header for ums7520 (qogirn6pro). SECURE_BOOT=NONE sim
 * platforms only need the efuse API to link; there is no real efuse driver. */
typedef enum {
	EFUSE_RESULT_SUCCESS = 0,
	EFUSE_RD_ERROR,
	EFUSE_WR_ERROR,
	EFUSE_PARAM_ERROR
} Efuse_Result_Ret;

#endif
''')

ensure_file("bootloader/chipram/include/security/sec_efuse_qogirn6pro.h", r'''#ifndef _SEC_EFUSE_QOGIRN6PRO_H_
#define _SEC_EFUSE_QOGIRN6PRO_H_

#include "sec_efuse_qogirn6pro_drv.h"

/* EFUSE block mapping (mirrors roc1 layout; unused on NONE-secure sim). */
#define NONE			(255)
#define HUK_BLOCK_START		(0)
#define HUK_BLOCK_END		(7)
#define KCE_BLOCK_START		(8)
#define KCE_BLOCK_END		(15)
#define ROTPK0_BLOCK_START	(16)
#define ROTPK0_BLOCK_END	(23)
#define SEC_VERSION_BLOCK	(24)
#define ROTPK1_BLOCK_START	(25)
#define ROTPK1_BLOCK_END	(32)
#define CYCLE_STATE_BLOCK	(33)
#define LOCK_BIT_BLOCK		(34)
#define NSEC_VER_BLOCK_START	(36)
#define NSEC_VER_BLOCK_END	(42)
#define RESERVED_BLOCK_START	(NONE)
#define RESERVED_BLOCK_END	(NONE)
#define ENDORKEY_BLOCK_START	(NONE)
#define ENDORKEY_BLOCK_END	(NONE)
#define PUBLIC_EFUSE_BLOCK2	(66)
#define RMA_MODE_BIT		(0)

extern Efuse_Result_Ret sprd_ce_efuse_huk_program(void);
extern Efuse_Result_Ret sprd_get_lock_bits(unsigned int start_id, unsigned int end_id, unsigned int *bits_data, unsigned int *bits_data1);
extern Efuse_Result_Ret sprd_ce_efuse_read(unsigned int block_id, unsigned int *read_ptr);
extern Efuse_Result_Ret sprd_ce_efuse_program(unsigned int block_id, unsigned int WriteData);
extern unsigned int sprd_get_secure_boot_enable(void);

#endif
''')

ensure_file("bootloader/chipram/secure/efuse/sec_efuse_qogirn6pro.c", r'''#include <security/sec_efuse_qogirn6pro.h>

/* Stub implementations for ums7520 (SECURE_BOOT=NONE). No real efuse exists
 * on the haps/zebu sim targets; the API only needs to link for sprd_verify. */

Efuse_Result_Ret sprd_ce_efuse_read(unsigned int block_id, unsigned int *read_ptr)
{
	if (read_ptr)
		*read_ptr = 0;
	return EFUSE_RESULT_SUCCESS;
}

Efuse_Result_Ret sprd_ce_efuse_program(unsigned int block_id, unsigned int WriteData)
{
	return EFUSE_RESULT_SUCCESS;
}

Efuse_Result_Ret sprd_get_lock_bits(unsigned int start_id, unsigned int end_id,
				    unsigned int *bits_data, unsigned int *bits_data1)
{
	if (bits_data)
		*bits_data = 0;
	if (bits_data1)
		*bits_data1 = 0;
	return EFUSE_RESULT_SUCCESS;
}

Efuse_Result_Ret sprd_ce_efuse_huk_program(void)
{
	return EFUSE_RESULT_SUCCESS;
}

unsigned int sprd_get_secure_boot_enable(void)
{
	return 0;
}
''')

patch_file("bootloader/chipram/include/security/sec_efuse.h", [
    ('#ifdef CONFIG_SOC_ORCA\n#include "sec_efuse_orca.h"\n#endif',
     '#ifdef CONFIG_SOC_ORCA\n#include "sec_efuse_orca.h"\n#endif\n\n#ifdef CONFIG_SOC_QOGIRN6PRO\n#include "sec_efuse_qogirn6pro.h"\n#endif'),
], "sec_efuse.h add qogirn6pro")
patch_file("bootloader/chipram/secure/efuse/Makefile", [
    ("obj-$(CONFIG_SOC_ORCA) += sec_efuse_orca.o sec_efuse_orca_drv.o",
     "obj-$(CONFIG_SOC_ORCA) += sec_efuse_orca.o sec_efuse_orca_drv.o\nobj-$(CONFIG_SOC_QOGIRN6PRO) += sec_efuse_qogirn6pro.o"),
], "efuse Makefile add qogirn6pro")

# ---------- 4h. exfat.h union missing semicolon ----------
# include/exfat.h has a trailing anonymous union whose closing '}' lacks ';'
# -> clang "expected member name or ';' after declaration specifiers".
exfat = p("bootloader/u-boot15/include/exfat.h")
if os.path.exists(exfat):
    with open(exfat, "rb") as f:
        d = f.read().decode("utf-8", errors="replace")
    nd = d.replace("\t}\n}exfat_file_entry;", "\t};\n}exfat_file_entry;")
    if nd != d:
        with open(exfat, "w", newline="") as f:
            f.write(nd)
        print("[OK] exfat.h union semicolon fixed")
    else:
        print("[SKIP] exfat.h pattern not found")

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
new_reg = ("#define BSL_CMD_UNLOCK_BL 0x500\n"
           "#define BSL_CMD_DISABLE_AVB 0x502\n\n"
           "extern int dl_cmd_unlock_bl(dl_packet_t *packet, void *arg);\n"
           "extern int dl_cmd_disable_avb(dl_packet_t *packet, void *arg);\n\n" +
           old_reg + "\n\tdl_cmd_register(BSL_CMD_UNLOCK_BL, dl_cmd_unlock_bl);\n\tdl_cmd_register(BSL_CMD_DISABLE_AVB, dl_cmd_disable_avb);")
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
