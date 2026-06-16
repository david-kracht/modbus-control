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

# Define architectures as "Docker Platform|Folder Name suffix"
ARCHS=("linux/arm/v7|armv7" "linux/arm64|arm64")

for ARCH_INFO in "${ARCHS[@]}"; do
    PLATFORM="${ARCH_INFO%%|*}"
    TARGET_ARCH="${ARCH_INFO##*|}"
    IMAGE_NAME="uv-builder-$TARGET_ARCH"

    echo "=========================================================="
    echo "🔧 Phase 1: Building Docker image for $TARGET_ARCH ($PLATFORM)..."
    echo "=========================================================="
    
    docker build \
        --platform "$PLATFORM" \
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