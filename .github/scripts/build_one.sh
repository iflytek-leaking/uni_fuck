#!/bin/bash
# Build FDL1+FDL2 for a single platform (called from GitHub Actions matrix)
# Usage: build_one.sh <short_name> <product_name>
#   e.g. build_one.sh ud710_2h10 ud710_2h10_native
# Must run from BSP root

# NOTE: no set -e — _base/common.cfg has readlink -f on deleted toolchain/
# paths which returns non-zero and would kill the script

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

# Toolchain prefix: CROSS_COMPILE is used as CC = $(CROSS_COMPILE)gcc
# So it must end with the prefix, not just the directory
TC_PREFIX="${TC}aarch64-linux-gnu-"

# Source BSP env (defines make(), source_configuration, etc.)
cd "$ROOT"
source build/envsetup.sh

# Manually find product in device tree (structure: device/<chip>/<os>/<board>/<variant>)
PRODUCT_PATH=$(find device -maxdepth 4 -mindepth 4 -type d -name "$PRODUCT" 2>/dev/null | head -1)
if [[ -z "$PRODUCT_PATH" ]]; then
    echo "ERROR: product '$PRODUCT' not found in device tree"
    exit 1
fi

# Parse path: device/<BSP_SYSTEM_VERSION>/<BSP_PLATFORM_VERSION>/<BSP_BOARD_NAME>/<BSP_PRODUCT_NAME>
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

# Toolchain: must be a prefix like /tmp/zig-tc/aarch64-linux-gnu-
# BspChipram.mk: LOCAL_TOOLCHAIN := $(BSP_CHIPRAM_TOOLCHAIN)
# config.mk:     CC = $(CROSS_COMPILE)gcc  →  /tmp/zig-tc/aarch64-linux-gnu-gcc
export BSP_CHIPRAM_TOOLCHAIN="$TC_PREFIX"
export BSP_UBOOT_TOOLCHAIN="$TC_PREFIX"
export BSP_OBJ="${BSP_OBJ:-$(nproc)}"

# Make libgcc.a findable by GNU ld (Makefile has -L . -lgcc)
LIBGCC=$(aarch64-linux-gnu-gcc -print-libgcc-file-name 2>/dev/null)
if [[ -n "$LIBGCC" && -f "$LIBGCC" ]]; then
    ln -sf "$LIBGCC" bootloader/chipram/libgcc.a
    ln -sf "$LIBGCC" bootloader/u-boot15/libgcc.a
fi

echo "============================================"
echo "  Platform : $SHORT"
echo "  Product  : $PRODUCT"
echo "  Path     : $BSP_PRODUCT_PATH"
echo "  Chip     : $BSP_SYSTEM_VERSION"
echo "  OS       : $BSP_PLATFORM_VERSION"
echo "  Board    : $BSP_BOARD_NAME"
echo "  Toolchain: $TC_PREFIX"
echo "  Jobs     : $BSP_OBJ"
echo "============================================"

# Build chipram (FDL1) — make is a shell function from envsetup.sh
# It calls make_chipram() → source_configuration chipram → bash make.sh bsp
# Then build_tool_and_sign_images() → packimage.sh → sprd_sign + imgheaderinsert
echo ""
echo "--- chipram (FDL1) ---"
make chipram
CHIPRAM_RC=$?
if [[ $CHIPRAM_RC -ne 0 ]]; then
    echo "ERROR: chipram build failed (rc=$CHIPRAM_RC)"
    exit 1
fi

# Build bootloader (FDL2)
echo ""
echo "--- bootloader (FDL2) ---"
make bootloader
BOOTLOADER_RC=$?
if [[ $BOOTLOADER_RC -ne 0 ]]; then
    echo "ERROR: bootloader build failed (rc=$BOOTLOADER_RC)"
    exit 1
fi

# Collect artifacts
# BSP dist paths: out/$BSP_BOARD_NAME/dist/<chipram|u-boot15>/
# Signed files: fdl1-sign.bin / fdl2-sign.bin (SPRD secure boot)
# Unsigned files: fdl1.bin / fdl2.bin (SECURE_BOOT=NONE, e.g. ums7520)
echo ""
echo "--- collecting artifacts ---"
ARTIFACT_DIR="$ROOT/fdl_output"
mkdir -p "$ARTIFACT_DIR"

CHIPRAM_DIST="$ROOT/out/$BSP_BOARD_NAME/dist/chipram"
UBOOT_DIST="$ROOT/out/$BSP_BOARD_NAME/dist/u-boot15"

# FDL1: prefer signed, fallback to unsigned
FDL1_SIGNED="$CHIPRAM_DIST/fdl1-sign.bin"
FDL1_UNSIGNED="$CHIPRAM_DIST/fdl1.bin"
if [[ -f "$FDL1_SIGNED" ]]; then
    cp "$FDL1_SIGNED" "$ARTIFACT_DIR/${SHORT}_fdl1.bin"
    echo "  FDL1 (signed): $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl1.bin") bytes"
elif [[ -f "$FDL1_UNSIGNED" ]]; then
    cp "$FDL1_UNSIGNED" "$ARTIFACT_DIR/${SHORT}_fdl1.bin"
    echo "  FDL1 (unsigned): $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl1.bin") bytes"
else
    echo "  WARN: fdl1.bin not found (checked $FDL1_SIGNED and $FDL1_UNSIGNED)"
fi

# FDL2: prefer signed, fallback to unsigned
FDL2_SIGNED="$UBOOT_DIST/fdl2-sign.bin"
FDL2_UNSIGNED="$UBOOT_DIST/fdl2.bin"
if [[ -f "$FDL2_SIGNED" ]]; then
    cp "$FDL2_SIGNED" "$ARTIFACT_DIR/${SHORT}_fdl2.bin"
    echo "  FDL2 (signed): $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl2.bin") bytes"
elif [[ -f "$FDL2_UNSIGNED" ]]; then
    cp "$FDL2_UNSIGNED" "$ARTIFACT_DIR/${SHORT}_fdl2.bin"
    echo "  FDL2 (unsigned): $(stat -c%s "$ARTIFACT_DIR/${SHORT}_fdl2.bin") bytes"
else
    echo "  WARN: fdl2.bin not found (checked $FDL2_SIGNED and $FDL2_UNSIGNED)"
fi

echo ""
echo "=== $SHORT done ==="
