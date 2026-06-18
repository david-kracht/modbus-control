# Modbus Control - Multi-Arch Build Toolchain

This directory contains a completely isolated Docker toolchain designed to build the `modbus-control` project (including all sibling dependencies like `efoy-modbus-config`) for both **ARMv7 (32-bit)** and **ARM64 (64-bit)** architectures.

## Why this setup?

Some Python dependencies (especially those with C/Rust extensions) are not always available as precompiled multi-architecture wheels (`.whl`) on PyPI. Installing these directly on a resource-constrained edge device would force the device to compile them from source – which usually fails due to missing compilers or insufficient RAM.

This toolchain iterates through target architectures, using Docker and QEMU to simulate the environments. It downloads all dependencies and precompiles missing C-extensions. The target device thus receives only ready-to-install, architecture-specific wheels.

## Prerequisites

Your local development machine must have Docker installed and be capable of emulating ARM architectures.

* **Mac / Windows (Docker Desktop):** QEMU emulation is enabled by default.
* **Linux:** If emulation is not active, register the QEMU binaries once using:
  ```bash
  docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
  ```

## Usage

Navigate to this build directory and execute the wrapper script:

```bash
cd modbus-control/build
chmod +x build-project.sh  # Only required the first time
./build-project.sh
```

### What happens in the background?

1. The script initiates a build loop for `linux/arm/v7` and `linux/arm64`.
2. An emulated Docker container starts for each architecture (equipped with `gcc`, `python3-dev`, etc.).
3. It mounts the shared root folder (`~/git`) so workspace references to `../efoy-modbus-config` resolve correctly.
4. It uses `uv` to download **all** dependencies specific to the current architecture.
5. It sorts out already compiled wheels from PyPI.
6. It **compiles** all packages that are only available as source code (`.tar.gz`).
7. It builds the main project and places it in a shared workspace folder.

## Results and Directory Structure

After a successful run, the script outputs a consolidated `wheelhouse` directory containing all artifacts:

```text
build/wheelhouse/
├── my_project_whls/          # Platform-independent (Any) wheels for your own code
├── own_build_arm64_whls/     # Manually compiled C-extensions for ARM64
├── own_build_armv7_whls/     # Manually compiled C-extensions for ARMv7
├── prebuild_arm64_whls/      # Precompiled PyPI wheels tailored for ARM64
└── prebuild_armv7_whls/      # Precompiled PyPI wheels tailored for ARMv7
```

## Deployment to the Target Device

Choose the installation option that fits your edge router's internet connectivity. **(Example given for ARMv7)**

### Option A: The target device HAS internet access

You only need to transfer `my_project_whls` and your locally compiled `own_build_armv7_whls`.

```bash
# On the ARMv7 target device:
uv pip install ./my_project_whls/*.whl --find-links ./own_build_armv7_whls
```

*Explanation: `uv` installs your project. If it needs a heavy C-package, it takes it from your local build folder. Simple standard packages are quickly downloaded from the internet.*

### Option B: The target device is completely OFFLINE (Air-gapped)

Copy `my_project_whls`, `own_build_armv7_whls`, and `prebuild_armv7_whls` to the device.

```bash
# On the ARMv7 target device:
uv pip install ./my_project_whls/*.whl \
  --no-index \
  --find-links ./own_build_armv7_whls \
  --find-links ./prebuild_armv7_whls
```

or

```bash
scp wheelhouse/my_project_whls/*.whl \
    wheelhouse/own_build_armv7_whls/*.whl \
    wheelhouse/prebuild_armv7_whls/*.whl \
    <user>@<host>:/tmp/

# On the router:
rm -rf /mnt/ext1/modbus-ctrl-venv
python3 -m venv --without-pip /mnt/ext1/modbus-ctrl-venv

# Bootstrap pip via system pip3 into venv site-packages (cd / avoids "folder not found")
cd / && /usr/bin/pip3 install \
    --find-links /tmp \
    --target /mnt/ext1/modbus-ctrl-venv/lib/python3.11/site-packages \
    pip setuptools wheel packaging

# Install all packages fully offline (use -m pip, not bin/pip — --target skips bin/ scripts)
/mnt/ext1/modbus-ctrl-venv/bin/python3 -m pip install \
    --no-index --find-links /tmp \
    modbus-ctrl-cli modbus-ctrl-center
```

*Explanation: By appending `--no-index`, pip uses only the local `/tmp/wheels` folder. The `PYTHONPATH` trick works because a `.whl` file is a valid zip archive that Python can import from directly — no extraction needed.*