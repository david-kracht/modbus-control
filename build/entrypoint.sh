#!/bin/bash
# Runs INSIDE the Docker container (linux/amd64 for armv7, linux/arm64 for arm64).
# Produces platform-correct wheels in build/wheelhouse/.
set -euo pipefail

TARGET_ARCH=${TARGET_ARCH:-armv7}

echo "🚀 Starting $TARGET_ARCH build..."

[ -f "pyproject.toml" ] || { echo "❌ No pyproject.toml found"; exit 1; }

WHEELHOUSE="./build/wheelhouse"
PREBUILT="$WHEELHOUSE/prebuild_${TARGET_ARCH}_whls"
OWNBUILD="$WHEELHOUSE/own_build_${TARGET_ARCH}_whls"
MYPROJECT="$WHEELHOUSE/my_project_whls"

rm -rf "$PREBUILT" "$OWNBUILD"
mkdir -p "$PREBUILT" "$OWNBUILD" "$MYPROJECT"

# ---------------------------------------------------------
# Target platform constants (derived from router inspection)
#   armv7 : /lib/ld-linux.so.3         → armv7-unknown-linux-gnueabi
#   arm64 : /lib/ld-linux-aarch64.so.1 → aarch64-unknown-linux-gnu
# ---------------------------------------------------------
if [ "$TARGET_ARCH" = "armv7" ]; then
    PIP_PLATFORM="linux_armv7l"
    HOST_PLATFORM="linux-armv7l"
    RUST_TARGET="armv7-unknown-linux-gnueabi"
    CROSS_GCC="arm-linux-gnueabi-gcc"
    CROSS_GXX="arm-linux-gnueabi-g++"
    CROSS_AR="arm-linux-gnueabi-ar"

    # Rust: cross-compile natively on x86
    export CARGO_BUILD_TARGET="$RUST_TARGET"
    export CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABI_LINKER="$CROSS_GCC"
    # cc-rs: C/C++ inside Cargo crates (e.g. zstd-sys) uses ARM cross-compiler
    export CC_armv7_unknown_linux_gnueabi="$CROSS_GCC"
    export CXX_armv7_unknown_linux_gnueabi="$CROSS_GXX"
    export AR_armv7_unknown_linux_gnueabi="$CROSS_AR"
    # Host CC stays as native gcc (for build-time tools like Cython that run on x86)
    export CC=gcc
    export CXX=g++
    export AR=ar
else
    PIP_PLATFORM="linux_aarch64"
    HOST_PLATFORM="linux-aarch64"
    CROSS_GCC=gcc
    CROSS_GXX=g++
    CROSS_AR=ar
fi

# ---------------------------------------------------------
# PHASE 1 – Export lockfile
# ---------------------------------------------------------
echo "📄 Exporting lockfile..."
uv export \
    --all-packages --no-dev \
    --format requirements-txt \
    --no-emit-workspace --no-hashes \
    > /tmp/requirements.txt

# ---------------------------------------------------------
# PHASE 2 – Download: binary wheels first, sdists for the rest
# ---------------------------------------------------------
echo "📥 Downloading wheels for $TARGET_ARCH ($PIP_PLATFORM)..."

WHEEL_CACHE="/tmp/wheel_cache"
SDIST_CACHE="/tmp/sdist_cache"
rm -rf "$WHEEL_CACHE" "$SDIST_CACHE"
mkdir -p "$WHEEL_CACHE" "$SDIST_CACHE"

# Per-package: try binary wheel first, fall back to sdist.
# Batching with -r requirements.txt causes the entire command to fail if ONE
# package has no binary wheel (e.g. pydantic-core on linux_armv7l) → WHEEL_CACHE
# stays empty → everything falls through to sdist. Per-package loop avoids this.
while IFS= read -r line; do
    trimmed="${line#"${line%%[! ]*}"}"
    [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue
    pkg_spec=$(echo "$trimmed" | sed 's/[[:space:]]*;.*//')
    pkg_name=$(echo "$pkg_spec" | cut -d= -f1 | tr '[:upper:]' '[:lower:]' | tr '-' '_')

    if [ "$TARGET_ARCH" = "armv7" ]; then
        if python3.11 -m pip download \
                --only-binary=:all: --no-deps \
                --platform "$PIP_PLATFORM" \
                --python-version 311 --implementation cp --abi cp311 \
                "$pkg_spec" -d "$WHEEL_CACHE" 2>/dev/null; then
            echo "   ✓ wheel  $pkg_name"
        else
            echo "   → sdist  $pkg_name"
            python3.11 -m pip download \
                --no-binary=:all: --no-deps \
                "$pkg_spec" -d "$SDIST_CACHE" 2>&1 || true
        fi
    else
        # arm64: native build host, download normally (no platform restriction)
        python3.11 -m pip download --no-deps "$pkg_spec" -d "$WHEEL_CACHE" 2>&1 || true
    fi
done < /tmp/requirements.txt

# Also get pip/setuptools/wheel for the venv bootstrap on the router
python3.11 -m pip download --no-deps pip setuptools wheel -d "$WHEEL_CACHE" 2>/dev/null || true

# Move all downloaded wheels to PREBUILT
mv "$WHEEL_CACHE"/*.whl "$PREBUILT/" 2>/dev/null || true

# ---------------------------------------------------------
# PHASE 3 – Build sdists
# ---------------------------------------------------------
shopt -s nullglob
SOURCES=("$SDIST_CACHE"/*.tar.gz "$SDIST_CACHE"/*.zip "$SDIST_CACHE"/*.tgz)
shopt -u nullglob

if [ ${#SOURCES[@]} -eq 0 ]; then
    echo "✅ No sdists to build for $TARGET_ARCH"
else
    echo "🔨 Building ${#SOURCES[@]} package(s) from source..."
    for sdist in "${SOURCES[@]}"; do
        name=$(basename "$sdist")
        echo "   → $name"

        # General case:
        #   _PYTHON_HOST_PLATFORM → wheel tagged linux_armv7l / linux_aarch64
        #   CC/CXX/AR             → C extensions compiled for target
        #   CARGO_BUILD_TARGET    → Rust crates compiled for target (set above)
        # PyYAML: no libyaml-dev in container → auto-falls back to pure Python
        _PYTHON_HOST_PLATFORM="$HOST_PLATFORM" \
        CC="$CROSS_GCC" \
        CXX="$CROSS_GXX" \
        AR="$CROSS_AR" \
        CARGO_HOME=/tmp/cargo-cache \
        python3.11 -m pip wheel "$sdist" \
            --no-deps \
            -w "$OWNBUILD" -v
    done
fi

# ---------------------------------------------------------
# PHASE 4 – Build own workspace packages
# ---------------------------------------------------------
echo "🏗️  Building workspace packages..."
uv build --all-packages --out-dir "$MYPROJECT"

echo ""
echo "✅ Build for $TARGET_ARCH complete."
echo "   prebuilt : $(ls "$PREBUILT"/*.whl 2>/dev/null | wc -l) wheels"
echo "   own-build: $(ls "$OWNBUILD"/*.whl 2>/dev/null | wc -l) wheels"
echo "   project  : $(ls "$MYPROJECT"/*.whl 2>/dev/null | wc -l) wheels"
