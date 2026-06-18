#!/bin/bash
# build_wheels.sh

TARGET=$1 # "armv7-gnueabi" oder "aarch64"
WHEEL_DIR="$(pwd)/wheels"

# Hole die aktuelle User- und Gruppen-ID vom Host
HOST_UID=$(id -u)
HOST_GID=$(id -g)

mkdir -p "$WHEEL_DIR"

if [ "$TARGET" == "armv7-gnueabi" ]; then
    DOCKER_PLATFORM="linux/arm/v5"
    DOCKER_IMAGE="debian:bookworm-slim"
    RUST_TARGET="armv7-unknown-linux-gnueabi"
elif [ "$TARGET" == "aarch64" ]; then
    DOCKER_PLATFORM="linux/arm64"
    DOCKER_IMAGE="arm64v8/python:3.11-slim"
    RUST_TARGET="aarch64-unknown-linux-gnu"
else
    echo "Unsupported architecture. Use 'armv7-gnueabi' or 'aarch64'"
    exit 1
fi

echo "Starte Build für $TARGET auf Plattform $DOCKER_PLATFORM..."

docker run --rm --platform "$DOCKER_PLATFORM" \
    -e HOST_UID="$HOST_UID" \
    -e HOST_GID="$HOST_GID" \
    -v "$WHEEL_DIR:/wheels" \
    "$DOCKER_IMAGE" /bin/bash -c "
    set -e
    
    echo '1. System-Abhängigkeiten installieren...'
    apt-get update -qq
    apt-get install -y -qq gcc g++ python3 python3-dev python3-pip python3-venv curl libyaml-dev pkg-config

    echo '2. Virtuelles Environment für den Build vorbereiten...'
    python3 -m venv /build-env
    export PATH=\"/build-env/bin:\$PATH\"

    echo '3. Rust Toolchain installieren...'
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-target $RUST_TARGET
    source \$HOME/.cargo/env

    echo '4. uv installieren...'
    pip install uv

    export CARGO_BUILD_TARGET=$RUST_TARGET
    
    echo '5. Baue/Lade Wheels für pyyaml und pydantic-core...'
    uv pip wheel --wheel-dir /wheels pyyaml==6.0.3 pydantic-core==2.46.4
    
    echo '6. Berechtigungen für den Host anpassen...'
    # Ändert den Besitzer aller Dateien in /wheels auf den Host-User
    chown -R \$HOST_UID:\$HOST_GID /wheels
    
    echo 'Build abgeschlossen! Schau in deinen ./wheels Ordner.'
"