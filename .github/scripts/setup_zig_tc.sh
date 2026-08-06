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

# Delegate GCC -print-* queries to real system GCC (Zig cc doesn't support them)
# Only pass the -print-* flag itself; CFLAGS may contain 32-bit ARM flags
# that aarch64 GCC would reject.
for arg in "$@"; do
    case "$arg" in
        -print-libgcc-file-name)
            exec /usr/bin/aarch64-linux-gnu-gcc -print-libgcc-file-name ;;
        -print-file-name=*)
            exec /usr/bin/aarch64-linux-gnu-gcc "$arg" ;;
        -print-search-dirs)
            exec /usr/bin/aarch64-linux-gnu-gcc -print-search-dirs ;;
        -print-prog-name=*)
            exec /usr/bin/aarch64-linux-gnu-gcc "$arg" ;;
    esac
done

ARGS=()
SKIP_NEXT=0
DEPFILE=""
HAS_S=0
OUTFILE=""
SRCFILE=""
EXPECT_OUT=0

for arg in "$@"; do
    if [[ "$SKIP_NEXT" == "1" ]]; then SKIP_NEXT=0; continue; fi
    if [[ "$EXPECT_OUT" == "1" ]]; then
        OUTFILE="$arg"
        ARGS+=("$arg")
        EXPECT_OUT=0
        continue
    fi
    case "$arg" in
        -mabi=lp64|-mgeneral-regs-only|-mthumb-interwork|-mno-thumb-interwork|-marm|-msoft-float|-mshort-load-bytes|-malignment-traps|-ffixed-r8|-ffixed-r9|-mno-unaligned-access|-mlittle-endian|-mbig-endian|-fno-stack-protector|-fverbose-asm)
            ;;  # skip GCC-specific / unsupported flags
        -march=*)
            ;;  # zig targets aarch64 already, -march=armv8-a confuses it
        -mthumb)
            SKIP_NEXT=1 ;;  # skip -mthumb <arg>
        -Wp,-MD,*)
            DEPFILE="${arg#-Wp,-MD,}" ;;
        -Wp,-MMD,*)
            DEPFILE="${arg#-Wp,-MMD,}" ;;
        -Wp,-MT,*)
            ARGS+=("-MT" "${arg#-Wp,-MT,}") ;;
        -S)
            HAS_S=1
            ARGS+=("$arg") ;;
        -o)
            ARGS+=("$arg")
            EXPECT_OUT=1 ;;
        *.c|*.S|*.s)
            SRCFILE="$arg"
            ARGS+=("$arg") ;;
        *)
            ARGS+=("$arg") ;;
    esac
done

# Add -MD -MF only when NOT -S (Zig cc has a bug with -S + dep generation → FileNotFound)
if [[ "$HAS_S" != "1" && -n "$DEPFILE" ]]; then
    ARGS+=("-MD" "-MF" "$DEPFILE")
fi

# Run zig cc (conservative flags: keep unsigned-wrap/aliasing/overflow semantics like GCC to
# avoid clang folding UB in DDR training code differently from the original GCC 2015 build)
zig cc -target aarch64-linux-gnu "${ARGS[@]}" \
    -fwrapv -fno-strict-aliasing -fno-strict-overflow -fno-delete-null-pointer-checks \
    -Wno-error -Wno-implicit-function-declaration -Wno-implicit-int \
    -Wno-return-type -Wno-int-conversion -Wno-incompatible-pointer-types \
    -Wno-deprecated-non-prototype -Wno-deprecated-declarations
RC=$?

# When -S was used, Zig cc doesn't generate the depfile.
# Create a minimal one so Kbuild's fixdep doesn't fail.
if [[ "$HAS_S" == "1" && -n "$DEPFILE" && ! -f "$DEPFILE" ]]; then
    echo "${OUTFILE:-output.s}: ${SRCFILE:-unknown}" > "$DEPFILE"
fi

# Diagnostics: print what zig cc failed on (helps when make swallows output)
if [[ $RC -ne 0 ]]; then
    echo "[zig-cc] failed (rc=$RC): zig cc -target aarch64-linux-gnu ${ARGS[*]}" >&2
fi

exit $RC
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
