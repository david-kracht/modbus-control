#!/bin/bash
# You run this script on your local development machine.

set -e

# Determine absolute paths automatically
BUILD_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
MODBUS_CTRL_DIR=$(realpath "$BUILD_DIR/..")
GIT_ROOT_DIR=$(realpath "$MODBUS_CTRL_DIR/..")

# Name of the project folder (e.g., "modbus-control")
PROJECT_DIR_NAME=$(basename "$MODBUS_CTRL_DIR")

echo "🧹 Cleaning up previous wheelhouse..."
rm -rf "$BUILD_DIR/wheelhouse"

# Define architectures as "Docker Platform|TARGET_ARCH|BASE_IMAGE"
#
# armv7: runs on linux/amd64 (x86 host) and cross-compiles for arm-linux-gnueabi.
#   Router ELF interpreter: /lib/ld-linux.so.3 (gnueabi/softfloat).
#   linux/arm/v7 would produce gnueabihf → wrong linker → cannot load on router.
#   rustup-init for armel is a gnueabihf binary → cannot run under armel QEMU.
#   Solution: x86 host + gcc-arm-linux-gnueabi cross-compiler + Rust cross-target.
#
# arm64: runs natively on linux/arm64.
ARCHS=(
    "linux/amd64|armv7|python:3.11-slim-bookworm"
    "linux/arm64|arm64|python:3.11-slim-bookworm"
)

for ARCH_INFO in "${ARCHS[@]}"; do
    IFS='|' read -r PLATFORM TARGET_ARCH BASE_IMAGE <<< "$ARCH_INFO"
    IMAGE_NAME="uv-builder-$TARGET_ARCH"

    echo "=========================================================="
    echo "🔧 Phase 1: Building Docker image for $TARGET_ARCH ($PLATFORM)..."
    echo "=========================================================="

    docker build \
        --progress plain \
        --platform "$PLATFORM" \
        --build-arg BASE_IMAGE="$BASE_IMAGE" \
        --build-arg TARGET_ARCH="$TARGET_ARCH" \
        --load \
        -t "$IMAGE_NAME" \
        -f "$BUILD_DIR/Dockerfile" "$BUILD_DIR"

    echo "=========================================================="
    echo "🚀 Phase 2: Starting build container for $TARGET_ARCH..."
    echo "=========================================================="

    docker run --rm -it \
        --platform "$PLATFORM" \
        --user "$(id -u):$(id -g)" \
        -e HOME="/tmp" \
        -e TARGET_ARCH="$TARGET_ARCH" \
        -v "$GIT_ROOT_DIR:/workspace" \
        -w "/workspace/$PROJECT_DIR_NAME" \
        "$IMAGE_NAME"
done

echo "🎉 All multi-architecture builds completed successfully!"
echo "👉 You can find the output in: $BUILD_DIR/wheelhouse"
