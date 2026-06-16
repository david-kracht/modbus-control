#!/bin/bash
# This script runs INSIDE the Docker container

set -e # Exit on error

# Default to armv7 if TARGET_ARCH is not provided by the host script
TARGET_ARCH=${TARGET_ARCH:-armv7}

echo "🚀 Starting $TARGET_ARCH build process inside container..."
echo "Current directory: $(pwd)"

# Ensure we are in modbus-control
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Error: No pyproject.toml found in the current directory!"
    exit 1
fi

# Paths relative to the project folder (modbus-control)
WHEELHOUSE_DIR="./build/wheelhouse"
DOWNLOADS_DIR="/tmp/downloads"

# Architecture specific folders
PREBUILT_DIR="$WHEELHOUSE_DIR/prebuild_${TARGET_ARCH}_whls"
OWNBUILD_DIR="$WHEELHOUSE_DIR/own_build_${TARGET_ARCH}_whls"
MYPROJECT_DIR="$WHEELHOUSE_DIR/my_project_whls"

# Clean up temporary downloads and old architecture specific folders
rm -rf "$PREBUILT_DIR" "$OWNBUILD_DIR" "$DOWNLOADS_DIR"
mkdir -p "$PREBUILT_DIR" "$OWNBUILD_DIR" "$MYPROJECT_DIR" "$DOWNLOADS_DIR"

# ---------------------------------------------------------
# PHASE 1: Export Lockfile & Download Dependencies
# ---------------------------------------------------------
echo "📄 Exporting strict dependencies from uv.lock..."
# --all-packages: Includes modbus-ctrl-core, modbus-config, etc.
# --no-dev: Excludes pytest and testing tools from the edge router
uv export --all-packages --no-dev --format requirements-txt --no-emit-workspace --no-hashes > /tmp/requirements.txt

echo "📥 Downloading dependencies for $TARGET_ARCH (including standalone pip)..."
# We explicitly download pip, setuptools, and wheel alongside your requirements
python3 -m pip download -r /tmp/requirements.txt pip setuptools wheel -d "$DOWNLOADS_DIR"

# ---------------------------------------------------------
# PHASE 2: Sorting and building missing packages
# ---------------------------------------------------------
echo "🗂️ Sorting out precompiled wheels..."
shopt -s nullglob
PREBUILT_WHEELS=("$DOWNLOADS_DIR"/*.whl)
if [ ${#PREBUILT_WHEELS[@]} -gt 0 ]; then
    mv "$DOWNLOADS_DIR"/*.whl "$PREBUILT_DIR/"
fi

echo "🔍 Searching for missing wheels (Source packages)..."
SOURCES=("$DOWNLOADS_DIR"/*.tar.gz "$DOWNLOADS_DIR"/*.zip "$DOWNLOADS_DIR"/*.tgz)
shopt -u nullglob

if [ ${#SOURCES[@]} -eq 0 ]; then
    echo "   🎉 Perfect! No custom builds needed for $TARGET_ARCH."
else
    echo "   🔨 Building ${#SOURCES[@]} packages from source for $TARGET_ARCH:"
    for sdist in "${SOURCES[@]}"; do
        echo "      -> Compiling $(basename "$sdist")..."
        python3 -m pip wheel "$sdist" --no-deps -w "$OWNBUILD_DIR"
    done
fi

# ---------------------------------------------------------
# PHASE 3: Build own project (Workspace Members)
# ---------------------------------------------------------
echo "🏗️ Building workspace packages..."
# FIX: Added --all-packages so uv builds all members (modbus-core, efoy-config, etc.)
uv build --all-packages --out-dir "$MYPROJECT_DIR"

# ---------------------------------------------------------
# PHASE 4: Provide Standalone 'uv' Binary for Target Device
# ---------------------------------------------------------
echo "🧰 Fetching standalone 'uv' binary for target execution..."
if [ "$TARGET_ARCH" = "armv7" ]; then
    UV_RELEASE="uv-arm-unknown-linux-musleabihf"
elif [ "$TARGET_ARCH" = "arm64" ]; then
    UV_RELEASE="uv-aarch64-unknown-linux-musl"
fi

curl -LsSf "https://github.com/astral-sh/uv/releases/latest/download/${UV_RELEASE}.tar.gz" -o "/tmp/uv.tar.gz"
tar -xzf /tmp/uv.tar.gz -C /tmp
mv "/tmp/${UV_RELEASE}/uv" "$WHEELHOUSE_DIR/uv_$TARGET_ARCH"
chmod +x "$WHEELHOUSE_DIR/uv_$TARGET_ARCH"

echo "✅ Build for $TARGET_ARCH successful!"