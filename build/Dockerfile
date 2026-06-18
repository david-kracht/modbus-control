# syntax=docker/dockerfile:1
#
# armv7: BASE_IMAGE=python:3.11-slim-bookworm, PLATFORM=linux/amd64 (x86 host).
#   Cross-compiles for armv7-unknown-linux-gnueabi (router ELF: /lib/ld-linux.so.3).
#   gcc-arm-linux-gnueabi + Rust cross-target replace the original apt cargo/rustc.
#
# arm64: BASE_IMAGE=python:3.11-slim-bookworm, PLATFORM=linux/arm64. Native build.

ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

ARG TARGET_ARCH=armv7
ARG RUST_VERSION="1.96.0"

# System deps: cross-compile toolchain added only for armv7 (x86 build host).
# libc6-armel-cross: provides /usr/arm-linux-gnueabi/lib/ld-linux.so.3 so QEMU
# can run ARM test binaries spawned by maturin during pep517 metadata generation.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential python3-dev libffi-dev libssl-dev curl git \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        apt-get install -y --no-install-recommends \
            gcc-arm-linux-gnueabi g++-arm-linux-gnueabi \
            binutils-arm-linux-gnueabi libc6-armel-cross; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Rust via rustup (apt cargo on Bookworm is too old for edition 2024).
# On x86: adds armv7-unknown-linux-gnueabi cross-target for armv7 builds.
# QEMU_LD_PREFIX: ARM sysroot for maturin's pep517 ARM test binary.
# PYO3_PYTHON: explicit versioned path so maturin can parse the Python version.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:${PATH} \
    QEMU_LD_PREFIX=/usr/arm-linux-gnueabi \
    PYO3_PYTHON=/usr/local/bin/python3.11

RUN set -eux; \
    dpkgArch="$(dpkg --print-architecture)"; \
    case "${dpkgArch##*-}" in \
        amd64) rustArch='x86_64-unknown-linux-gnu' ;; \
        arm64) rustArch='aarch64-unknown-linux-gnu' ;; \
        armhf) rustArch='armv7-unknown-linux-gnueabihf' ;; \
        *) echo >&2 "unsupported arch: ${dpkgArch}"; exit 1 ;; \
    esac; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --profile minimal \
          --default-toolchain "${RUST_VERSION}" --default-host "${rustArch}"; \
    if [ "$dpkgArch" = "amd64" ]; then \
        rustup target add armv7-unknown-linux-gnueabi; \
    fi; \
    chmod -R a+w $RUSTUP_HOME $CARGO_HOME; \
    rustc --version; cargo --version

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip wheel setuptools

# uv for workspace export and building own packages
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx \
    && rm -rf /root/.local

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
