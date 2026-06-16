# Modbus Control Software Suite

A modular, schema-driven monorepo workspace for simulating, monitoring, controlling, and visualizing Modbus TCP devices.

---

## Architecture & Services

The suite consists of two core control components and integrates with a standalone simulator:
1. **Control Center Backend (`modbus-ctrl-center`)**: FastAPI server managing device database (`devices.yaml`), websocket telemetry delta streams, and serving the static React SPA frontend.
2. **Control CLI (`modbus-ctrl`)**: CLI tool for device management and ad-hoc register reads/writes.

*Note:* The **Mock Simulator (`modbus-sim`)** has been extracted to the upstream schema registry repository (`efoy-modbus-config`) as it relies heavily on the `modbus-schema-common` parser.

---
## Configuration

Both services and CLI support environment variables (loaded from `.env`) and CLI flag overrides:

| Environment Variable | Default | Description | CLI Override |
|---|---|---|---|
| `MODBUS_DEVICES_YAML` | `devices.yaml` | Path to device registry file | `--devices-yaml` / `-d` |
| `CTRL_CENTER_HOST` | `0.0.0.0` | Backend bind host | `--host` |
| `CTRL_CENTER_PORT` | `8000` | Backend bind port | `--port` |
| `SIM_HOST` | `0.0.0.0` | Simulator bind host | `--host` |
| `SIM_PORT` | `5020` | Simulator bind port | `--port` |
| `SIM_SCHEMA` | `modbus_config/latest` | Simulator schema template | `--schema` |
| `DEFAULT_MODBUS_HOST` | `127.0.0.1` | Ad-hoc fallback host (when no yaml) | `--default-host` |
| `DEFAULT_MODBUS_PORT` | `502` | Ad-hoc fallback port | `--default-port` |
| `DEFAULT_MODBUS_UNIT_ID`| `1` | Ad-hoc fallback unit ID | `--default-unit-id` |
| `DEFAULT_MODBUS_SCHEMA` | `modbus_config/latest` | Ad-hoc fallback schema | `--default-schema` |
| `SUITE_TITLE` | `Modbus Control Suite` | Main title/branding of the suite | None |
| `SUITE_LOGO_PATH` | (empty) | Absolute path to a custom SVG/PNG logo file | None |

### Branding & Customization
You can change the visual branding and console output text across the entire suite:
- **Title Customization**: Set `SUITE_TITLE` in `.env` or your env variables. This dynamically updates:
  - CLI help texts (e.g. `uv run modbus-sim --help` displays `<SUITE_TITLE> - Mock Simulator Server`, etc.).
  - FastAPI Server title and console log headers on startup.
  - React Control Center sidebar/header title (e.g. `<SUITE_TITLE> - Control Center`).
- **Logo Customization**: Set `SUITE_LOGO_PATH` to the absolute path of a custom logo image (SVG or PNG). The React frontend will dynamically load it; if unset or the file loading fails, it gracefully falls back to the default packaged logo or standard Lucide `<Cpu>` icon.

*To run the React frontend independently in dev mode (CORS enabled on backend), configure:*
- `VITE_API_URL`: Backend API URL (e.g. `http://localhost:8000`)
- `VITE_WS_URL`: Backend WebSocket telemetry URL (e.g. `ws://localhost:8000`)

---

## Build, Deploy & Run

### 1. Build & Install Workspace
```bash
# Sync all virtual environments & workspace members
uv sync --all-packages
```

### 2. Run Simulator
```bash
# Start mock simulator (requires modbus-simulator installed, e.g. from efoy-modbus-config)
uv run modbus-sim --schema v30 --port 5025
```

### 3. Run Control Center (API Backend & UI)
```bash
# Build React Frontend SPA
cd packages/modbus-ctrl-center/frontend && npm run build && cd ../../..

# Start FastAPI server
uv run modbus-ctrl-center
```
Open 👉 **[http://localhost:8000/](http://localhost:8000/)** to access the Dashboard.
Open 👉 **[http://localhost:8000/docs](http://localhost:8000/docs)** to access the Swagger API Explorer.

### 4. Use CLI (`modbus-ctrl`)
```bash
# Add device to devices.yaml
uv run modbus-ctrl device add 127.0.0.1 --name "Device1" --port 5025 --schema v30

# Read registers (Fuzzy name and address matches supported)
uv run modbus-ctrl read "BatIdConfig" --target Device1

# Write value
uv run modbus-ctrl write "BatIdConfig" "1" --target Device1

# Ad-hoc query (No devices.yaml needed, falls back to env defaults)
uv run modbus-ctrl read "BatIdConfig" --host 127.0.0.1 --port 5025 --schema v30
```

---

## Deployment & Dependency Resolution

### 1. Development vs. Release Modes

The workspace supports two modes of dependency resolution via `uv`:

#### A. Local Dev Mode (Workspace Resolution)
For local development, dependencies are resolved dynamically across workspace packages or relative local directories. This is configured in `pyproject.toml` under `[tool.uv.sources]`:
```toml
# Sibling workspace resolution
modbus-ctrl-contracts = { workspace = true }
# Local path resolution (e.g. referencing the local config repo during dev)
modbus-config = { path = "../efoy-modbus-config/packages/modbus-config" }
```
*Benefits:* Changes in contracts or schemas are immediately reflected in consumer packages without needing intermediary builds or uploads.

#### B. Production Release Mode (Registry Resolution)
For deployments or production installations, packages are resolved directly from a PyPI or custom registry.
1. Comment out or delete the `[tool.uv.sources]` entries in all packages.
2. Sibling references will fallback to their standard definitions in the `dependencies = [...]` array (e.g., `"modbus-ctrl-contracts>=0.1.0"`, `"modbus-config>=1.1.0"`).
3. `uv` will fetch and install the versioned packages from the registry index.

---

### 2. Packaging & Publishing
Build and publish wheels and source archives to your target registry:
```bash
# 1. Build all workspace packages
uv build --all

# 2. Configure target registry credentials
export UV_PUBLISH_URL="https://your-custom-registry.com/repository/pypi/"
export UV_PUBLISH_USERNAME="username"
export UV_PUBLISH_PASSWORD="password"

# 3. Publish to registry
uv publish
```

---

### 3. Standalone Application Installation (via `uv tool`)
The CLI and services can be installed on client machines as isolated, standalone tools without system-wide python dependencies. `uv` handles interpreter isolation automatically:
```bash
# Install CLI tool
uv tool install modbus-ctrl-cli --index https://your-custom-registry.com/repository/pypi/simple

# Install and run services on-the-fly
uvx --index https://your-custom-registry.com/repository/pypi/simple modbus-ctrl-center
```
*No system package dependencies or manual virtualenv steps are needed.*

---

### 4. Release & Update Workflows

#### Case A: Core/CLI Implementation Changes
When modifying application code (e.g. backend server, CLI layout):
1. Make your changes in the respective package.
2. Bump the package version in its `pyproject.toml` (e.g., `modbus-ctrl-center` version `0.1.0` -> `0.1.1`).
3. Rebuild and publish the updated package: `uv build -p modbus-ctrl-center && uv publish`.
*Note:* Sibling consumer packages with loose version requirements (e.g., `>=0.1.0`) do not need to be updated.

#### Case B: Schema Registry / Config Updates
When a new device schema or registry parameter is introduced (e.g. in the external `efoy-modbus-config` repo):
1. **In the Config Repository**:
   - Run the PDF extraction pipeline to generate the new schema version (e.g. `v40.json`).
   - Bump the version in `packages/modbus-config/pyproject.toml` (e.g., `1.1.0` -> `1.2.0`).
   - Build and publish the updated schema package to the registry.
2. **In the Control Repository**:
   - During development, reference the updated local path in `[tool.uv.sources]` to implement and test the suite against the new schema features.
   - Once verified, update the dependency requirement in `pyproject.toml` files to enforce the new schema version (e.g. `"modbus-config>=1.2.0"`).
   - Build and release the updated control packages to the registry.

#### Case C: Installing Additional Schema Plugins (Post-Release)
The Modbus Schema system leverages a decoupled, dynamic **Python Entry Point** architecture. This means the Control Suite has **no hardcoded dependencies** on specific schema packages. 

You can dynamically add new schema definitions (e.g., for a new device manufacturer) to an existing, deployed Control Suite *without* needing a new release of the control application:

1. **Develop the Schema Package**: Build a Python package (e.g., `wago-modbus-config`) that utilizes `modbus-schema-common` and registers itself via the `[project.entry-points."modbus.schema"]` hook.
2. **Install the Plugin**: Install the new package into the same Python environment where the Control Suite backend is running (e.g., `pip install wago-modbus-config` or `uv pip install ...`).
3. **Automatic Discovery**: Restart the FastAPI Backend (`modbus-ctrl-center`). Python will automatically discover the new plugin via its entry point.
4. **UI Integration**:
   - **Frontend Dashboard**: The React SPA dynamically fetches `/api/schemas/available`. The new schema (e.g., `wago/latest`) will instantly appear in the schema `<select>` dropdown when adding or editing a device.
   - **CLI TUI**: The `modbus-ctrl dashboard` also dynamically evaluates available schemas. The new package will appear in the interactive Schema Selection modal (accessed by pressing `[Enter]` on the Schema Name field).

---

## Potential Suite Extensions

The suite's modular structure allows easy expansion:
- **Edge Daemons**: Package services into lightweight Docker containers for deployment on gateway hardware.
- **Timeseries Store**: Hook a timeseries database adapter (e.g., InfluxDB/TimescaleDB) into the backend telemetry stream.
- **Multi-Protocol Gateways**: Expand the core package with BACnet, OPC-UA, or EtherNet/IP clients, using equivalent schema specifications.
- **Multi-Tenant Authorization**: Add OAuth2/OIDC auth wrappers to secure API access and filter views based on user organizations.
