from __future__ import annotations
import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Set, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from modbus_common import AppConfig, DeviceConfig
from modbus_ctrl_contracts import WriteBatchRequest, TelemetryDeltaResponse
from modbus_ctrl_core import ModbusControlEngine, resolve_schema, config
import typer


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("modbus-ctrl-center")

# Path to the shared devices YAML file
def get_devices_yaml_path() -> Path:
    return config.MODBUS_DEVICES_YAML

# Global caches and tasks
active_websockets: Set[WebSocket] = set()
device_caches: Dict[str, Dict[str, Any]] = {}
polling_tasks: Dict[str, asyncio.Task] = {}
device_status: Dict[str, Dict[str, Any]] = {}
unsupported_registers: Dict[str, Set[str]] = {}
# Per-device semaphore: limits concurrent TCP connections to MAX_CONNECTIONS_PER_DEVICE
device_semaphores: Dict[str, asyncio.Semaphore] = {}


def _get_semaphore(device_name: str) -> asyncio.Semaphore:
    if device_name not in device_semaphores:
        device_semaphores[device_name] = asyncio.Semaphore(
            config.MAX_CONNECTIONS_PER_DEVICE
        )
    return device_semaphores[device_name]


async def _broadcast(payload: str):
    """Send a JSON payload to all connected WebSocket clients."""
    dead_sockets: Set[WebSocket] = set()
    for ws in active_websockets:
        try:
            await ws.send_text(payload)
        except Exception:
            dead_sockets.add(ws)
    if dead_sockets:
        active_websockets.difference_update(dead_sockets)


async def update_device_values_and_broadcast(device_name: str, new_vals: dict[str, Any]):
    """Update the cache for a device with new values, and broadcast deltas over WebSocket."""
    cache = device_caches.setdefault(device_name, {})
    deltas = {}
    for k, v in new_vals.items():
        if k not in cache or cache[k] != v:
            deltas[k] = v
            cache[k] = v

    if deltas and active_websockets:
        msg = TelemetryDeltaResponse(
            device_name=device_name,
            timestamp=time.time(),
            deltas=deltas
        )
        await _broadcast(msg.model_dump_json())
        logger.debug("Broadcasting delta update for %s: %s", device_name, deltas)


async def poll_device(device_name: str):
    """Background loop polling a device and broadcasting deltas via WebSockets."""
    logger.info("Starting polling loop for device: %s", device_name)
    device_caches.setdefault(device_name, {})
    engine: Optional[ModbusControlEngine] = None
    current_host: Optional[str] = None
    current_port: Optional[int] = None

    try:
        while True:
            try:
                # Reload config each iteration to pick up interval/active changes
                config_path = get_devices_yaml_path()
                app_config = AppConfig.load_from_yaml(config_path)
                device = next(
                    (d for d in app_config.devices if d.name == device_name),
                    None,
                )
                if not device or not device.active:
                    logger.info(
                        "Device '%s' is no longer active. Stopping poll loop.",
                        device_name,
                    )
                    break

                # Reuse engine across iterations; only recreate on host/port change
                device_unsupported = unsupported_registers.get(device_name)
                if (
                    engine is None
                    or device.host != current_host
                    or device.port != current_port
                ):
                    if engine is not None:
                        await engine.client.close()
                    engine = ModbusControlEngine(
                        device,
                        unsupported_registers=device_unsupported,
                    )
                    current_host = device.host
                    current_port = device.port
                else:
                    engine.unsupported_registers = device_unsupported or set()

                new_vals = await engine.read_all()

                if engine.newly_failed_registers:
                    if device_name not in unsupported_registers:
                        unsupported_registers[device_name] = set()
                    unsupported_registers[device_name].update(
                        engine.newly_failed_registers
                    )
                    logger.info(
                        "Device '%s' new unsupported registers: %s",
                        device_name,
                        engine.newly_failed_registers,
                    )

                now = time.time()
                prev_online = device_status.get(device_name, {}).get("online")
                device_status[device_name] = {
                    "online": True,
                    "last_poll": now,
                    "error": None,
                }
                if prev_online is not True:
                    await _broadcast_status(device_name)

                await update_device_values_and_broadcast(device_name, new_vals)
                await asyncio.sleep(device.polling_interval)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # pymodbus can swallow CancelledError and re-raise it as a
                # plain Exception ("Request cancelled outside library").
                # Detect this via Task.cancelling() and propagate correctly.
                curr = asyncio.current_task()
                if curr is not None and curr.cancelling() > 0:
                    raise asyncio.CancelledError() from e
                logger.error(
                    "Error in polling loop for device '%s': %s", device_name, e
                )
                prev_online = device_status.get(device_name, {}).get("online")
                device_status[device_name] = {
                    "online": False,
                    "last_poll": device_status.get(
                        device_name, {}
                    ).get("last_poll"),
                    "error": str(e),
                }
                if prev_online is not False:
                    await _broadcast_status(device_name)
                # Connection broke — discard engine so a fresh one is built
                if engine is not None and not engine.client.connected:
                    await engine.client.close()
                    engine = None
                    current_host = None
                    current_port = None
                await asyncio.sleep(5.0)

    except asyncio.CancelledError:
        logger.info("Polling loop for device '%s' cancelled.", device_name)
    finally:
        if engine is not None:
            await engine.client.close()


async def _broadcast_status(device_name: str):
    """Broadcast a device status update message to all WebSocket clients."""
    status = device_status.get(device_name, {})
    payload = {
        "type": "status_update",
        "device_name": device_name,
        "online": status.get("online", False),
        "last_poll": status.get("last_poll"),
        "error": status.get("error"),
    }
    import json
    await _broadcast(json.dumps(payload))


def start_device_polling(device_name: str):
    if device_name in polling_tasks and not polling_tasks[device_name].done():
        return
    task = asyncio.create_task(poll_device(device_name))
    polling_tasks[device_name] = task


def stop_device_polling(device_name: str):
    task = polling_tasks.get(device_name)
    if task and not task.done():
        task.cancel()


def restart_device_polling(device_name: str):
    """Stop any existing task and start a fresh one. Also clears the value cache."""
    stop_device_polling(device_name)
    polling_tasks.pop(device_name, None)
    device_caches.pop(device_name, None)
    unsupported_registers.pop(device_name, None)
    device_semaphores.pop(device_name, None)  # reset semaphore on restart
    # Mark as unknown/offline until first successful poll
    device_status[device_name] = {
        "online": False,
        "last_poll": None,
        "error": "Restarting...",
    }
    start_device_polling(device_name)


def sync_polling_tasks():
    """Start or stop polling loops based on current devices.yaml."""
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)
    active_device_names = {d.name for d in app_config.devices if d.active}

    # Stop tasks for removed/inactive devices
    for name in list(polling_tasks.keys()):
        if name not in active_device_names:
            logger.info("Stopping polling task for %s", name)
            stop_device_polling(name)
            polling_tasks.pop(name, None)

    # Start tasks for new/active devices
    for name in active_device_names:
        if name not in polling_tasks or polling_tasks[name].done():
            logger.info("Starting polling task for %s", name)
            start_device_polling(name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: sync and launch polling
    sync_polling_tasks()
    yield
    # Shutdown: cancel all tasks, wait at most 15 s so Ctrl+C is never stuck
    for name, task in list(polling_tasks.items()):
        task.cancel()
    if polling_tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *polling_tasks.values(), return_exceptions=True
                ),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Polling tasks did not finish within shutdown timeout.")

app = FastAPI(title=f"{config.SUITE_TITLE} - API Server", lifespan=lifespan)

# Enable CORS for independent frontend/tool clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_suite_config():
    return {
        "suite_title": config.SUITE_TITLE,
        "default_schema": config.DEFAULT_MODBUS_SCHEMA,
    }


@app.get("/api/logo")
async def get_suite_logo():
    logo_path = config.SUITE_LOGO_PATH
    if logo_path:
        p = Path(logo_path).resolve()
        if p.is_file():
            return FileResponse(p)
            
    # Serve default packaged logo
    default_p = Path(__file__).parent / "logo.png"
    if default_p.is_file():
        return FileResponse(default_p)
        
    raise HTTPException(status_code=404, detail="Logo not found")

@app.get("/api/devices")
async def get_devices():
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)
    return app_config.devices


@app.get("/api/devices/status")
async def get_all_device_status():
    """Return the current online/offline status for all known devices."""
    return device_status


@app.post("/api/devices")
async def add_device(device: DeviceConfig):
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)

    if any(d.name == device.name for d in app_config.devices):
        raise HTTPException(status_code=400, detail=f"Device name '{device.name}' already exists.")

    app_config.devices.append(device)
    app_config.save_to_yaml(config_path)
    sync_polling_tasks()
    return {"status": "ok"}


@app.put("/api/devices/{name}")
async def update_device(name: str, device: DeviceConfig):
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)

    idx_to_edit = None
    for idx, d in enumerate(app_config.devices):
        if d.name == name:
            idx_to_edit = idx
            break

    if idx_to_edit is None:
        raise HTTPException(status_code=404, detail=f"Device '{name}' not found.")

    if any(i != idx_to_edit and d.name == device.name for i, d in enumerate(app_config.devices)):
        raise HTTPException(status_code=400, detail=f"Device name '{device.name}' already exists.")

    app_config.devices[idx_to_edit] = device
    app_config.save_to_yaml(config_path)

    # Always restart the polling task so the new host/port/interval takes effect immediately.
    # If the name changed, stop the old task first.
    if device.name != name:
        stop_device_polling(name)
        polling_tasks.pop(name, None)
        device_caches.pop(name, None)
        device_status.pop(name, None)
        unsupported_registers.pop(name, None)

    if device.active:
        restart_device_polling(device.name)
    else:
        stop_device_polling(device.name)
        polling_tasks.pop(device.name, None)

    return {"status": "ok", "device": device}


@app.delete("/api/devices/{name}")
async def delete_device(name: str):
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)

    found = False
    for idx, d in enumerate(app_config.devices):
        if d.name == name:
            app_config.devices.pop(idx)
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"Device '{name}' not found.")

    app_config.save_to_yaml(config_path)
    stop_device_polling(name)
    polling_tasks.pop(name, None)
    device_caches.pop(name, None)
    device_status.pop(name, None)
    unsupported_registers.pop(name, None)
    device_semaphores.pop(name, None)
    return {"status": "ok"}


@app.get("/api/devices/{name}/schema")
async def get_device_schema(name: str):
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)
    device = next((d for d in app_config.devices if d.name == name), None)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{name}' not found.")
    try:
        spec = resolve_schema(device.schema_name)
        if device.registers:
            spec = spec.model_copy(deep=True)
            reg_map = {r.name: r for r in spec.registers}
            filtered_regs = [reg_map[rname] for rname in device.registers if rname in reg_map]
            if filtered_regs:
                spec.registers = filtered_regs
        return spec
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load schema: {e}")


@app.get("/api/schemas/available")
async def get_available_schemas_list():
    try:
        from modbus_schema_common.registry import get_available_schemas
        return get_available_schemas()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load available schemas: {e}")


@app.get("/api/schemas/{schema_name:path}")
async def get_schema_by_name(schema_name: str):
    """Resolve and return a schema specification directly by its schema name (e.g. v20, v30)."""
    try:
        spec = resolve_schema(schema_name)
        return spec
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Schema '{schema_name}' not found: {e}")


@app.get("/api/devices/{name}/values")
async def get_device_values(name: str):
    # Return from our fast cache
    if name not in device_caches:
        raise HTTPException(status_code=404, detail=f"Device '{name}' values not available.")
    # Sanitize non-finite floats (nan/inf) to None so json.dumps doesn't crash.
    # Holding registers with no value configured return 0x7FC00000 (float NaN).
    def _clean(v: Any) -> Any:
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v
    return {k: _clean(v) for k, v in device_caches[name].items()}


@app.post("/api/devices/{name}/write")
async def write_device_registers(name: str, request: WriteBatchRequest):
    config_path = get_devices_yaml_path()
    app_config = AppConfig.load_from_yaml(config_path)
    device = next((d for d in app_config.devices if d.name == name), None)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{name}' not found.")
    sem = _get_semaphore(name)
    async with sem:
        try:
            engine = ModbusControlEngine(device)
            results = await engine.write_registers(request.writes)
            try:
                device_unsupported = unsupported_registers.get(name)
                engine_read = ModbusControlEngine(
                    device, unsupported_registers=device_unsupported
                )
                read_vals = await engine_read.read_all()
                await update_device_values_and_broadcast(name, read_vals)
                device_status[name] = {
                    "online": True,
                    "last_poll": time.time(),
                    "error": None,
                }
                await _broadcast_status(name)
            except Exception as read_err:  # noqa: BLE001
                logger.warning(
                    "Read-back after write failed for %s: %s", name, read_err
                )
            return results
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"Failed to execute writes: {e}"
            )


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    logger.info("WebSocket client connected. Total connected: %d", len(active_websockets))

    # Send current status snapshot to the newly connected client
    import json
    for dev_name, status in device_status.items():
        snapshot = {
            "type": "status_update",
            "device_name": dev_name,
            "online": status.get("online", False),
            "last_poll": status.get("last_poll"),
            "error": status.get("error"),
        }
        try:
            await websocket.send_text(json.dumps(snapshot))
        except Exception:
            break

    try:
        while True:
            # Keep socket alive and receive frames
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_websockets.discard(websocket)
        logger.info("WebSocket client disconnected. Total connected: %d", len(active_websockets))
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
        active_websockets.discard(websocket)

# ---------------------------------------------------------------------------
# SPA static files serving & fallback routing
# ---------------------------------------------------------------------------

static_dir = Path(__file__).parent / "static"

if static_dir.exists():
    # Serve static assets
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Prevent intercepting API or WS calls
        if full_path.startswith("api/") or full_path == "ws/telemetry":
            raise HTTPException(status_code=404)

        # Check if file exists in static folder (e.g. assets, favicon)
        target = static_dir / full_path
        if target.is_file():
            return FileResponse(target)

        # Fallback to index.html for client-side React Routing
        return FileResponse(static_dir / "index.html")
else:
    logger.warning("SPA static directory not found at: %s. Serve API only mode.", static_dir)

    @app.get("/")
    async def index():
        return {"message": f"{config.SUITE_TITLE} - API Server (API only mode - static frontend not built)"}


cli_app = typer.Typer(help=f"{config.SUITE_TITLE} - API Server")


@cli_app.callback(invoke_without_command=True)
def main_cli(
    host: Optional[str] = typer.Option(None, "--host", help="Bind host (overrides CTRL_CENTER_HOST)"),
    port: Optional[int] = typer.Option(None, "--port", help="Bind port (overrides CTRL_CENTER_PORT)"),
    devices_yaml: Optional[Path] = typer.Option(None, "--devices-yaml", help="Path to devices.yaml configuration file (overrides MODBUS_DEVICES_YAML)"),
):
    import uvicorn
    if devices_yaml is not None:
        config.MODBUS_DEVICES_YAML = devices_yaml.resolve()
        
    resolved_host = host if host is not None else config.CTRL_CENTER_HOST
    resolved_port = port if port is not None else config.CTRL_CENTER_PORT
    
    logger.info("Starting %s - API Server on %s:%d...", config.SUITE_TITLE, resolved_host, resolved_port)
    uvicorn.run("modbus_ctrl_center.server:app", host=resolved_host, port=resolved_port, reload=False)


def main():
    cli_app()


if __name__ == "__main__":
    main()
