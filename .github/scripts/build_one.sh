#!/bin/bash
# Build FDL1+FDL2 for a single platform (called from GitHub Actions matrix)
# Usage: build_one.sh <short_name> <product_name>
#   e.g. build_one.sh ud710_2h10 ud710_2h10_native
# Must run from BSP root after: source build/envsetup.sh

set -e

SHORT="$1"
PRODUCT="$2"
ROOT="${BSP_ROOT_DIR:-$PWD}"
TC="${CROSS_COMPILE_TOOLCHAIN:-}"

if [[ -z "$SHORT" || -z "$PRODUCT" ]]; then
    echo "Usage: build_one.sh <short_name> <product_name>"
    exit 1
fi

if [[ -z "$TC" ]]; then
    echo "ERROR: set CROSS_COMPILE_TOOLCHAIN=/path/to/zig-tc/"
    exit 1
fi

export BSP_CHIPRAM_TOOLCHAIN="$TC"
export BSP_UBOOT_TOOLCHAIN="$TC"
export BSP_OBJ="${BSP_OBJ:-$(nproc)}"

echo "============================================"
echo "  Platform : $SHORT"
echo "  Product  : $PRODUCT"
echo "  Toolchain: $TC"
echo "  Jobs     : $BSP_OBJ"
echo "============================================"

echo ""
echo "--- lunch $PRODUCT ---"
lunch "$PRODUCT" 2>&1 | tail -3

echo ""
echo "--- chipram (FDL1) ---"
make chipram -j"$BSP_OBJ"

echo ""
echo "--- bootloader (FDL2) ---"
make bootloader -j"$BSP_OBJ"

# Collect artifacts (signed via build_tool_and_sign_images -> packimage.sh -> sprd_sign)
echo ""
echo "--- collecting artifacts ---"
ARTIFACT_DIR="$ROOT/fdl_output"
mkdir -p "$ARTIFACT_DIR"
DIST="$ROOT/out/target/product/$SHORT/dist"

FDL1="$DIST/chipram/fdl1.bin"
FDL2="$DIST/bootloader/fdl2.bin"

if [[ -f "$FDL1" ]]; then
    cp "$FDL1" "$ARTIFACT_DIR/${SHORT}_fdl1.bin"
    echo "  FDL1: $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl1.bin") bytes"
else
    echo "  WARN: fdl1.bin not found at $FDL1"
fi

if [[ -f "$FDL2" ]]; then
    cp "$FDL2" "$ARTIFACT_DIR/${SHORT}_fdl2.bin"
    echo "  FDL2: $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl2.bin") bytes"
else
    echo "  WARN: fdl2.bin not found at $FDL2"
fi

echo ""
echo "=== $SHORT done ==="
