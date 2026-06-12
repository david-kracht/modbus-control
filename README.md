# Modbus Control Software Suite

A highly extensible, contract-validated Modbus TCP monorepo workspace for simulating, polling, controlling, and visualizing Modbus-capable devices using schema-driven dynamic widgets.

---

## Workspace Quick Start

### 1. Installation & Dependency Sync
Ensure you have `uv` installed, then synchronize the Python virtual environment and link all monorepo workspace packages:
```bash
uv sync --all-packages
```

---

## Applications & Services

### 1. Modbus TCP Mock Simulator Server
Start the simulation server which mimics live register telemetry and processes commands. It defaults to port `5020` and loads device schemas (e.g. `v10` registry template) to initialize memory blocks:
```bash
uv run modbus-sim --schema v10 --port 5020
```

* **Features**:
  * **On-Read Jitter**: Simulates realistic sensor fluctuations on `float32` input registers.
  * **Auto-Reset Triggers**: Automatically resets write-only (WO) coils (e.g. `SystemOn` trigger action) back to `False` after `500ms`.
  * **Gap Prepopulation**: Automatically populates empty address gaps with zeros to permit unified block-reading by production clients.

---

### 2. Modbus Control Center (Web UI Backend)
Launch the FastAPI backend service which hosts WebSocket telemetry delta streams, manages the local device database (`devices.yaml`), and serves the compiled React single page application (SPA):
```bash
uv run modbus-ctrl-center
```
* **Host & Port**: `http://localhost:8000`
* **Real-time Engine**: Starts active background polling workers for all active registered devices, broadcasting real-time changes directly to connected browsers via WebSockets (`/ws/telemetry`).

---

### 3. Command Line Interface (CLI Tool)
Manage devices and perform raw register read/write operations directly from the terminal.

#### Register a Device
Add the simulated device to your local database:
```bash
uv run modbus-ctrl device add "127.0.0.1" --name "SimDevice" --port 5020 --unit-id 1 --schema v10
```

#### List Configured Devices
```bash
uv run modbus-ctrl device list
```

#### Read Register Value
Supports PascalCase fuzzy matching and decimal address lookups:
```bash
uv run modbus-ctrl read "LogUBat" --target SimDevice
uv run modbus-ctrl read "ClusterRoleConfig" --target SimDevice
```

#### Write Register Value
Strictly ordinal writes validating data types:
```bash
uv run modbus-ctrl write "ClusterRoleConfig" "3" --target SimDevice
uv run modbus-ctrl write "SystemOn" "1" --target SimDevice
```

#### List Schema Metadata
Outputs schema register maps in terminal tables, YAML, JSON, CSV, or INI formats:
```bash
uv run modbus-ctrl list --target SimDevice --view table
uv run modbus-ctrl list --target SimDevice --view json
```

---

## Web Dashboard Access

Once the simulator and control center are running, open your web browser to access the control panel:
👉 **[http://localhost:8000/](http://localhost:8000/)**

* **Telemetry Panel**: Dynamic tiles displaying real-time WebSocket updates.
* **Control Actions**: Triggers write-only actions (like resets and starts).
* **Configuration Form**: Staged settings change editor with live highlights, allowing batch configuration writes to the device registry.
