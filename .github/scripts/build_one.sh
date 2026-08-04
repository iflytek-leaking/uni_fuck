#!/bin/bash
# Build FDL1+FDL2 for a single platform (called from GitHub Actions matrix)
# Usage: build_one.sh <short_name> <product_name>
#   e.g. build_one.sh ud710_2h10 ud710_2h10_native
# Must run from BSP root

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

# Source BSP env (defines make(), source_configuration, etc.)
# but we bypass lunch() because its named-input path is broken
cd "$ROOT"
source build/envsetup.sh

# Manually find product in device tree (structure: device/<chip>/<os>/<board>/<variant>)
PRODUCT_PATH=$(find device -maxdepth 4 -mindepth 4 -type d -name "$PRODUCT" 2>/dev/null | head -1)
if [[ -z "$PRODUCT_PATH" ]]; then
    echo "ERROR: product '$PRODUCT' not found in device tree"
    exit 1
fi

# Parse path components: device/<BSP_SYSTEM_VERSION>/<BSP_PLATFORM_VERSION>/<BSP_BOARD_NAME>/<BSP_PRODUCT_NAME>
BSP_SYSTEM_VERSION=$(echo "$PRODUCT_PATH" | cut -d/ -f2)
BSP_PLATFORM_VERSION=$(echo "$PRODUCT_PATH" | cut -d/ -f3)
BSP_BOARD_NAME=$(echo "$PRODUCT_PATH" | cut -d/ -f4)

export BSP_PRODUCT_NAME="$PRODUCT"
export BSP_PRODUCT_PATH="$ROOT/$PRODUCT_PATH"
export BSP_BOARD_NAME
export BSP_BOARD_PATH="$ROOT/device/$BSP_SYSTEM_VERSION/$BSP_PLATFORM_VERSION/$BSP_BOARD_NAME"
export BSP_BOARD_BASE_PATH="$BSP_BOARD_PATH/${BSP_BOARD_NAME}_base"
export BSP_SYSTEM_VERSION
export BSP_SYSTEM_COMMON="$ROOT/device/$BSP_SYSTEM_VERSION/$BSP_PLATFORM_VERSION"
export BSP_BUILD_VARIANT="userdebug"
export BSP_PLATFORM_VERSION

export BSP_CHIPRAM_TOOLCHAIN="$TC"
export BSP_UBOOT_TOOLCHAIN="$TC"
export BSP_OBJ="${BSP_OBJ:-$(nproc)}"

echo "============================================"
echo "  Platform : $SHORT"
echo "  Product  : $PRODUCT"
echo "  Path     : $BSP_PRODUCT_PATH"
echo "  Chip     : $BSP_SYSTEM_VERSION"
echo "  OS       : $BSP_PLATFORM_VERSION"
echo "  Toolchain: $TC"
echo "  Jobs     : $BSP_OBJ"
echo "============================================"

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
