#!/bin/bash
# Setup Zig as aarch64 cross-compiler toolchain
# Creates wrapper scripts so the build system sees a normal CROSS_COMPILE prefix

set -e

TC_DIR="${1:-/opt/zig-aarch64-tc}"
ZIG="$(which zig)"
BINUTILS_PREFIX="aarch64-linux-gnu"

echo "=== Setting up Zig AArch64 toolchain at $TC_DIR ==="

mkdir -p "$TC_DIR"

# ---------- gcc wrapper: zig cc ----------
cat > "$TC_DIR/${BINUTILS_PREFIX}-gcc" << 'ZIGWRAP'
#!/bin/bash
# zig cc: drop flags that clang/zig don't understand
ARGS=()
SKIP_NEXT=0
for arg in "$@"; do
    if [[ "$SKIP_NEXT" == "1" ]]; then SKIP_NEXT=0; continue; fi
    case "$arg" in
        -mabi=lp64|-mgeneral-regs-only|-mthumb-interwork|-mno-thumb-interwork|-marm|-msoft-float|-mshort-load-bytes|-malignment-traps|-ffixed-r8|-ffixed-r9|-mno-unaligned-access|-mlittle-endian|-mbig-endian|-fno-stack-protector)
            ;;  # skip GCC-specific ARM flags
        -march=*)
            ;;  # zig targets aarch64 already, -march=armv8-a confuses it
        -mthumb)
            SKIP_NEXT=1 ;;  # skip -mthumb <arg>
        *)
            ARGS+=("$arg") ;;
    esac
done
exec zig cc -target aarch64-linux-gnu "${ARGS[@]}" -Wno-error -Wno-implicit-function-declaration -Wno-implicit-int -Wno-return-type -Wno-int-conversion -Wno-incompatible-pointer-types -Wno-deprecated-non-prototype -Wno-deprecated-declarations
ZIGWRAP
chmod +x "$TC_DIR/${BINUTILS_PREFIX}-gcc"

# ---------- symlink binutils from system (including ld) ----------
for tool in ar objcopy objdump nm ranlib strip readelf ld; do
    if command -v "${BINUTILS_PREFIX}-${tool}" &>/dev/null; then
        ln -sf "$(command -v ${BINUTILS_PREFIX}-${tool})" "$TC_DIR/${BINUTILS_PREFIX}-${tool}"
    else
        # fallback: use host binutils (ok for ELF operations on cross binaries)
        ln -sf "$(command -v ${tool})" "$TC_DIR/${BINUTILS_PREFIX}-${tool}"
    fi
done

echo "Toolchain ready: $(ls "$TC_DIR" | wc -l) wrappers"
echo "Zig version: $($ZIG version)"
