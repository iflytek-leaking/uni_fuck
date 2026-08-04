#!/bin/bash
# Build FDL1+FDL2 for all 18 platforms
# Must run from BSP root after: source build/envsetup.sh
# Export CROSS_COMPILE_TOOLCHAIN=/path/to/zig-wrapper before calling

set -e

ROOT="${BSP_ROOT_DIR:-$PWD}"
OUT="$ROOT/out/target/product"
TC="${CROSS_COMPILE_TOOLCHAIN:-}"

if [[ -z "$TC" ]]; then
    echo "ERROR: set CROSS_COMPILE_TOOLCHAIN=/path/to/zig-tc/"
    exit 1
fi

export BSP_CHIPRAM_TOOLCHAIN="$TC"
export BSP_UBOOT_TOOLCHAIN="$TC"
export BSP_OBJ="${BSP_OBJ:-$(nproc)}"

echo "=== Building all platforms ==="
echo "Toolchain: $TC"
echo "Jobs: $BSP_OBJ"
echo ""

# Platform → product_name mapping (includes variant)
declare -A TARGETS=(
    [s9863a1c10]="s9863a1c10_Natv"
    [s9863a1h10]="s9863a1h10_Natv"
    [s9863a2h10]="s9863a2h10_Natv"
    [s9863a3c10]="s9863a3c10_Natv"
    [ums512_1h10]="ums512_1h10_Natv"
    [ums512_20c10]="ums512_20c10_native"
    [ums512_2h10]="ums512_2h10_Natv"
    [sl8541e_1h10]="sl8541e_1h10_Natv"
    [ud710_10h10]="ud710_10h10_native"
    [ud710_20c11]="ud710_20c11_native"
    [ud710_2c11]="ud710_2c11_native"
    [ud710_2h10]="ud710_2h10_native"
    [ud710_2h10u]="ud710_2h10u_native"
    [ud710_3h10u]="ud710_3h10u_native"
    [ud710_7h10]="ud710_7h10_native"
    [ud710_9h10u]="ud710_9h10u_native"
    [ums7520_haps]="ums7520_haps_native"
    [ums7520_zebu]="ums7520_zebu_native"
)

# Build order: short-name list
ORDER=(
    s9863a1c10 s9863a1h10 s9863a2h10 s9863a3c10
    ums512_1h10 ums512_20c10 ums512_2h10
    sl8541e_1h10
    ud710_10h10 ud710_20c11 ud710_2c11 ud710_2h10
    ud710_2h10u ud710_3h10u ud710_7h10 ud710_9h10u
    ums7520_haps ums7520_zebu
)

TOTAL=0
PASS=0
FAIL=0

ARTIFACT_DIR="$ROOT/fdl_output"
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR"

for sfx in "${ORDER[@]}"; do
    pname="${TARGETS[$sfx]}"
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "=== [$TOTAL/18] $sfx (product=$pname) ==="

    lunch "$pname" 2>&1 | tail -1

    fdl1_ok=0
    fdl2_ok=0

    # Build FDL1 (chipram)
    echo "  -> chipram (FDL1)..."
    if make chipram -j"$BSP_OBJ" 2>&1 | tail -5; then
        fdl1_ok=1
    else
        echo "  FAIL: chipram build error"
    fi

    # Build FDL2 (bootloader/u-boot)
    echo "  -> bootloader (FDL2)..."
    if make bootloader -j"$BSP_OBJ" 2>&1 | tail -5; then
        fdl2_ok=1
    else
        echo "  FAIL: bootloader build error"
    fi

    # Collect artifacts
    DIST="$OUT/$sfx/dist"
    if [[ "$fdl1_ok" == "1" ]]; then
        cp "$DIST/chipram/fdl1.bin" "$ARTIFACT_DIR/${sfx}_fdl1.bin" 2>/dev/null && \
            echo "  FDL1: $ARTIFACT_DIR/${sfx}_fdl1.bin ($(stat -c%s "$ARTIFACT_DIR/${sfx}_fdl1.bin" 2>/dev/null || echo 0) bytes)"
    fi
    if [[ "$fdl2_ok" == "1" ]]; then
        cp "$DIST/bootloader/fdl2.bin" "$ARTIFACT_DIR/${sfx}_fdl2.bin" 2>/dev/null && \
            echo "  FDL2: $ARTIFACT_DIR/${sfx}_fdl2.bin ($(stat -c%s "$ARTIFACT_DIR/${sfx}_fdl2.bin" 2>/dev/null || echo 0) bytes)"
    fi

    if [[ "$fdl1_ok" == "1" && "$fdl2_ok" == "1" ]]; then
        PASS=$((PASS + 1))
        echo "  OK"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL (FDL1=$fdl1_ok FDL2=$fdl2_ok)"
    fi
done

echo ""
echo "=== BUILD SUMMARY ==="
echo "Total: $TOTAL | Pass: $PASS | Fail: $FAIL"
echo "Artifacts: $ARTIFACT_DIR"
ls -la "$ARTIFACT_DIR/" 2>/dev/null || echo "(empty)"
