import asyncio
import csv
from datetime import datetime
import io
import json
import os
import re
import sys
import select
import tty
import termios
from pathlib import Path
import time
from typing import List, Optional
from zoneinfo import ZoneInfo

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

from modbus_ctrl_contracts import AppConfig, DeviceConfig
from modbus_ctrl_core import ModbusControlEngine, resolve_schema, config
from modbus_schema_common.models import ModbusRegisterType, ModbusDataType

app = typer.Typer(help=f"{config.SUITE_TITLE} - CLI")
console_output = Console()

@app.callback()
def main_callback(
    devices_yaml: Optional[Path] = typer.Option(None, "--devices-yaml", "-d", envvar="MODBUS_DEVICES_YAML", help="Path to devices.yaml configuration file"),
    default_host: Optional[str] = typer.Option(None, "--default-host", envvar="DEFAULT_MODBUS_HOST", help="Default Ad-Hoc Modbus host override"),
    default_port: Optional[int] = typer.Option(None, "--default-port", envvar="DEFAULT_MODBUS_PORT", help="Default Ad-Hoc Modbus port override"),
    default_unit_id: Optional[int] = typer.Option(None, "--default-unit-id", envvar="DEFAULT_MODBUS_UNIT_ID", help="Default Ad-Hoc slave unit ID override"),
    default_schema: Optional[str] = typer.Option(None, "--default-schema", envvar="DEFAULT_MODBUS_SCHEMA", help="Default Ad-Hoc schema override"),
):
    if devices_yaml is not None:
        config.MODBUS_DEVICES_YAML = devices_yaml.resolve()
    if default_host is not None:
        config.DEFAULT_MODBUS_HOST = default_host
    if default_port is not None:
        config.DEFAULT_MODBUS_PORT = default_port
    if default_unit_id is not None:
        config.DEFAULT_MODBUS_UNIT_ID = default_unit_id
    if default_schema is not None:
        config.DEFAULT_MODBUS_SCHEMA = default_schema

def get_devices_yaml_path() -> Path:
    return config.MODBUS_DEVICES_YAML

def load_app_config(config_path: Path) -> AppConfig:
    try:
        return AppConfig.load_from_yaml(config_path)
    except ValueError as e:
        console_output.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

def resolve_device(
    target: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    unit_id: Optional[int] = None,
    schema_name: Optional[str] = None,
) -> DeviceConfig:
    # If a specific host is provided, that is an ad-hoc device connection
    if host:
        p = port if port is not None else config.DEFAULT_MODBUS_PORT
        u = unit_id if unit_id is not None else config.DEFAULT_MODBUS_UNIT_ID
        s = schema_name if schema_name is not None else config.DEFAULT_MODBUS_SCHEMA
        return DeviceConfig(
            name=f"AdHoc_{host}",
            host=host,
            port=p,
            unit_id=u,
            schema_name=s,
        )

    config_path = get_devices_yaml_path()
    app_config = None
    if config_path.exists():
        try:
            app_config = load_app_config(config_path)
        except Exception:
            pass

    selected_dev = None

    if target is not None:
        if not app_config or not app_config.devices:
            raise typer.BadParameter(f"Target device '{target}' requested, but devices.yaml is empty or could not be loaded.")
        # Check by list index
        try:
            idx = int(target)
            if 0 <= idx < len(app_config.devices):
                selected_dev = app_config.devices[idx]
        except ValueError:
            pass

        if selected_dev is None:
            # Check by name
            for dev in app_config.devices:
                if dev.name == target:
                    selected_dev = dev
                    break
        
        if selected_dev is None:
            raise typer.BadParameter(f"Target device '{target}' not found by index or name in devices.yaml.")
    else:
        # Default to first device in config if available
        if app_config and app_config.devices:
            selected_dev = app_config.devices[0]

    if selected_dev is not None:
        # Apply CLI overrides if explicitly provided (i.e. not None)
        updated_data = selected_dev.model_dump()
        if host is not None:
            updated_data["host"] = host
        if port is not None:
            updated_data["port"] = port
        if unit_id is not None:
            updated_data["unit_id"] = unit_id
        if schema_name is not None:
            updated_data["schema_name"] = schema_name
        return DeviceConfig(**updated_data)

    # Fallback to configured default Modbus host if set in environment
    if config.DEFAULT_MODBUS_HOST:
        return DeviceConfig(
            name=f"AdHoc_{config.DEFAULT_MODBUS_HOST}",
            host=config.DEFAULT_MODBUS_HOST,
            port=port if port is not None else config.DEFAULT_MODBUS_PORT,
            unit_id=unit_id if unit_id is not None else config.DEFAULT_MODBUS_UNIT_ID,
            schema_name=schema_name if schema_name is not None else config.DEFAULT_MODBUS_SCHEMA,
        )

    raise typer.BadParameter("No devices configured in devices.yaml, and no DEFAULT_MODBUS_HOST environment fallback set.")

def extract_pascal_case_words(text: str) -> List[str]:
    return re.findall(r'\b[A-Z][a-zA-Z0-9]*\b', text)

def parse_ordinal_value(val_str: str) -> float | int:
    # Remove delimiters at the start if any, but regex handles it
    m = re.match(r'^-?\d+(?:\.\d+)?', val_str.strip())
    if not m:
        raise typer.BadParameter(f"Value '{val_str}' is not a valid numeric value.")
    num_str = m.group(0)
    if "." in num_str:
        return float(num_str)
    return int(num_str)

# ---------------------------------------------------------------------------
# Device Management Commands
# ---------------------------------------------------------------------------

device_app = typer.Typer(help="Manage local Modbus devices in devices.yaml")
app.add_typer(device_app, name="device")

@device_app.command("add")
def device_add(
    host: str = typer.Argument(..., help="Host IP or address"),
    name: Optional[str] = typer.Option(None, "--name", help="Unique name of the device. Defaults to {host}_{port} if omitted."),
    port: Optional[int] = typer.Option(None, help="Modbus TCP Port"),
    unit_id: Optional[int] = typer.Option(None, help="Slave Unit ID"),
    schema: Optional[str] = typer.Option(None, help="Schema register template"),
    interval: Optional[float] = typer.Option(None, help="Polling interval in seconds"),
    active: Optional[bool] = typer.Option(None, "--active/--no-active", help="Whether the device is active"),
    registers: Optional[str] = typer.Option(None, "--registers", help="Comma-separated list of register names to whitelist"),
):
    """Add a new device to the local devices.yaml configuration."""
    config_path = get_devices_yaml_path()
    app_config = load_app_config(config_path)

    # Build kwargs to only pass parameters that were explicitly specified
    kwargs = {"host": host}
    if name is not None:
        kwargs["name"] = name
    if port is not None:
        kwargs["port"] = port
    if unit_id is not None:
        kwargs["unit_id"] = unit_id
    if schema is not None:
        kwargs["schema_name"] = schema
    if interval is not None:
        kwargs["polling_interval"] = interval
    if active is not None:
        kwargs["active"] = active
    if registers is not None:
        if registers.strip() == "":
            kwargs["registers"] = None
        else:
            kwargs["registers"] = [r.strip() for r in registers.split(",") if r.strip()]

    try:
        new_device = DeviceConfig(**kwargs)
    except ValidationError as e:
        console_output.print("[red]Validation Error:[/red]")
        for error in e.errors():
            msg = error["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            console_output.print(f"  [red]- {msg}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console_output.print(f"[red]Validation Error: {e}[/red]")
        raise typer.Exit(code=1)

    # Check if duplicate name
    for dev in app_config.devices:
        if dev.name == new_device.name:
            console_output.print(f"[red]Error: Device with name '{new_device.name}' already exists.[/red]")
            raise typer.Exit(code=1)

    app_config.devices.append(new_device)
    app_config.save_to_yaml(config_path)
    console_output.print(f"[green]Successfully added device '{new_device.name}' ({new_device.host}:{new_device.port}) to {config_path}[/green]")

@device_app.command("edit")
def device_edit(
    name_or_index: str = typer.Argument(..., help="Name or index of the device to edit"),
    new_name: Optional[str] = typer.Option(None, "--name", help="New unique name of the device"),
    host: Optional[str] = typer.Option(None, "--host", help="New Host IP or address"),
    port: Optional[int] = typer.Option(None, "--port", help="New Modbus TCP Port"),
    unit_id: Optional[int] = typer.Option(None, "--unit-id", help="New Slave Unit ID"),
    schema: Optional[str] = typer.Option(None, "--schema", help="New schema register template"),
    interval: Optional[float] = typer.Option(None, "--interval", help="New polling interval in seconds"),
    active: Optional[bool] = typer.Option(None, "--active/--no-active", help="Enable or disable polling for the device"),
    registers: Optional[str] = typer.Option(None, "--registers", help="Comma-separated list of registers to overwrite the whitelist (use empty string to clear)"),
    add_register: Optional[List[str]] = typer.Option(None, "--add-register", help="Register name(s) to add to the whitelist (can be specified multiple times or as comma-separated list)"),
    remove_register: Optional[List[str]] = typer.Option(None, "--remove-register", help="Register name(s) to remove from the whitelist (can be specified multiple times or as comma-separated list)"),
):
    """Edit an existing device in the local devices.yaml configuration."""
    config_path = get_devices_yaml_path()
    app_config = load_app_config(config_path)

    idx_to_edit = None
    try:
        idx = int(name_or_index)
        if 0 <= idx < len(app_config.devices):
            idx_to_edit = idx
    except ValueError:
        pass

    if idx_to_edit is None:
        for idx, dev in enumerate(app_config.devices):
            if dev.name == name_or_index:
                idx_to_edit = idx
                break

    if idx_to_edit is None:
        console_output.print(f"[red]Error: Device '{name_or_index}' not found.[/red]")
        raise typer.Exit(code=1)

    target_device = app_config.devices[idx_to_edit]

    # Dump the current device config to dict, update with only set cli values
    current_data = target_device.model_dump()
    if new_name is not None:
        current_data["name"] = new_name if new_name != "" else None
    if host is not None:
        current_data["host"] = host
    if port is not None:
        current_data["port"] = port
    if unit_id is not None:
        current_data["unit_id"] = unit_id
    if schema is not None:
        current_data["schema_name"] = schema
    if interval is not None:
        current_data["polling_interval"] = interval
    if active is not None:
        current_data["active"] = active

    # Handle registers whitelist options
    if registers is not None:
        if registers.strip() == "":
            current_regs = None
        else:
            current_regs = [r.strip() for r in registers.split(",") if r.strip()]
    else:
        current_regs = current_data.get("registers")

    # Parse add/remove list options (handle both multiple calls and comma-separated string)
    add_list = []
    if add_register:
        for val in add_register:
            for part in val.split(","):
                part_stripped = part.strip()
                if part_stripped:
                    add_list.append(part_stripped)

    remove_list = []
    if remove_register:
        for val in remove_register:
            for part in val.split(","):
                part_stripped = part.strip()
                if part_stripped:
                    remove_list.append(part_stripped)

    # Apply add/remove changes
    if add_list or remove_list:
        if current_regs is None:
            if add_list:
                current_regs = list(add_list)
            if remove_list:
                try:
                    schema_to_use = current_data.get("schema_name") or "v10"
                    spec = resolve_schema(schema_to_use)
                    all_regs = [r.name for r in spec.registers]
                    current_regs = [r for r in all_regs if r not in remove_list]
                except Exception as e:
                    console_output.print(f"[red]Error: Cannot remove registers from empty whitelist (all active) because schema could not be loaded: {e}[/red]")
                    raise typer.Exit(code=1)
        else:
            new_regs = list(current_regs)
            for r in add_list:
                if r not in new_regs:
                    new_regs.append(r)
            for r in remove_list:
                if r in new_regs:
                    new_regs.remove(r)
            current_regs = new_regs

    # Save it back if any changes occurred
    if registers is not None or add_list or remove_list:
        current_data["registers"] = current_regs

    try:
        updated_device = DeviceConfig(**current_data)
    except ValidationError as e:
        console_output.print("[red]Validation Error:[/red]")
        for error in e.errors():
            msg = error["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            console_output.print(f"  [red]- {msg}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console_output.print(f"[red]Validation Error: {e}[/red]")
        raise typer.Exit(code=1)

    # Check name collisions
    for i, dev in enumerate(app_config.devices):
        if i != idx_to_edit and dev.name == updated_device.name:
            console_output.print(f"[red]Error: Device with name '{updated_device.name}' already exists.[/red]")
            raise typer.Exit(code=1)

    app_config.devices[idx_to_edit] = updated_device
    app_config.save_to_yaml(config_path)
    console_output.print(f"[green]Successfully updated device '{target_device.name}' -> '{updated_device.name}'[/green]")

@device_app.command("list")
def device_list():
    """List all configured Modbus devices."""
    config_path = get_devices_yaml_path()
    app_config = load_app_config(config_path)

    if not app_config.devices:
        console_output.print(f"[yellow]No devices configured in {config_path}.[/yellow]")
        return

    table = Table(title=f"Configured Modbus Devices ({config_path.name})")
    table.add_column("Index", justify="right", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Host:Port", style="magenta")
    table.add_column("Unit ID", justify="right")
    table.add_column("Schema", style="blue")
    table.add_column("Interval", justify="right")
    table.add_column("Active", justify="center")
    table.add_column("Registers", style="yellow")

    for idx, dev in enumerate(app_config.devices):
        registers_str = ", ".join(dev.registers) if dev.registers else "All"
        table.add_row(
            str(idx),
            dev.name,
            f"{dev.host}:{dev.port}",
            str(dev.unit_id),
            dev.schema_name,
            f"{dev.polling_interval}s",
            "[green]Yes[/green]" if dev.active else "[red]No[/red]",
            registers_str
        )
    console_output.print(table)

@device_app.command("remove")
def device_remove(
    name: str = typer.Argument(..., help="Name or index of the device to remove")
):
    """Remove a device from the local devices.yaml configuration."""
    config_path = get_devices_yaml_path()
    app_config = load_app_config(config_path)

    found = None
    # Try by index first
    try:
        idx = int(name)
        if 0 <= idx < len(app_config.devices):
            found = app_config.devices.pop(idx)
    except ValueError:
        pass

    # Try by name if not popped by index
    if not found:
        for idx, dev in enumerate(app_config.devices):
            if dev.name == name:
                found = app_config.devices.pop(idx)
                break

    if found:
        app_config.save_to_yaml(config_path)
        console_output.print(f"[green]Successfully removed device '{found.name}'[/green]")
    else:
        console_output.print(f"[red]Error: Device '{name}' not found.[/red]")
        raise typer.Exit(code=1)

# ---------------------------------------------------------------------------
# Metadata Listing Command
# ---------------------------------------------------------------------------

@app.command("list")
def list_registers(
    search: Optional[str] = typer.Argument(None, help="Optional search filter substring"),
    target: Optional[str] = typer.Option(None, "--target", "-t", help="YAML device index or name"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Ad-hoc Modbus IP override"),
    port: Optional[int] = typer.Option(None, help="Ad-hoc port override"),
    unit_id: Optional[int] = typer.Option(None, help="Ad-hoc Slave Unit ID override"),
    schema: Optional[str] = typer.Option(None, help="Ad-hoc schema override"),
    view: str = typer.Option("table", help="View format: table, grouped, csv, ini, json, yaml"),
):
    """List metadata descriptions of Modbus registers in the device schema."""
    try:
        device = resolve_device(target, host, port, unit_id, schema)
        spec = resolve_schema(device.schema_name)
    except Exception as e:
        console_output.print(f"[red]Error resolving schema: {e}[/red]")
        raise typer.Exit(code=1)

    # Filter and sort by allowed registers if specified in config
    if device.registers:
        reg_map = {r.name: r for r in spec.registers}
        regs = [reg_map[name] for name in device.registers if name in reg_map]
    else:
        regs = spec.registers
    if search:
        search_lower = search.lower()
        regs = [
            r for r in regs
            if search_lower in r.name.lower() or
               search_lower in str(r.address_dec) or
               (r.description and search_lower in r.description.lower())
        ]

    if view == "json":
        print(json.dumps([r.model_dump() for r in regs], indent=2))
        return

    if view == "yaml":
        print(yaml.dump([r.model_dump() for r in regs], sort_keys=False))
        return

    if view == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "AddressDec", "AddressHex", "DataType", "Access", "Unit", "Description"])
        for r in regs:
            unit = getattr(r, "unit", "")
            writer.writerow([r.name, r.address_dec, r.address_hex, r.data_type.value, r.access, unit, r.description])
        print(output.getvalue())
        return

    if view == "ini":
        for r in regs:
            print(f"[{r.name}]")
            print(f"address = {r.address_dec}")
            print(f"type = {r.data_type.value}")
            print(f"access = {r.access}")
            if hasattr(r, "unit") and r.unit:
                print(f"unit = {r.unit}")
            print(f"description = {r.description}")
            print()
        return

    if view == "grouped":
        # Group by register type
        groups = {}
        for r in regs:
            groups.setdefault(r.register_type.value, []).append(r)
        
        for gname, glist in sorted(groups.items()):
            table = Table(title=f"Register Type: {gname}")
            table.add_column("Address Dec (Hex)", style="cyan")
            table.add_column("Name", style="bold")
            table.add_column("Data Type", style="magenta")
            table.add_column("Access")
            table.add_column("Unit")
            table.add_column("Description")
            
            for r in glist:
                unit = getattr(r, "unit", "") or ""
                table.add_row(
                    f"{r.address_dec} ({r.address_hex})",
                    r.name,
                    r.data_type.value,
                    r.access,
                    unit,
                    r.description or ""
                )
            console_output.print(table)
        return

    # Default: table
    table = Table(title=f"Schema Registers: {spec.device_name} (Firmware: {spec.firmware})")
    table.add_column("Address", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Data Type", style="magenta")
    table.add_column("Access", justify="center")
    table.add_column("Unit", justify="center")
    table.add_column("Description")

    for r in regs:
        unit = getattr(r, "unit", "") or ""
        table.add_row(
            f"{r.address_dec} ({r.address_hex})",
            r.name,
            r.data_type.value,
            r.access,
            unit,
            r.description or ""
        )
    console_output.print(table)

# ---------------------------------------------------------------------------
# Read Command
# ---------------------------------------------------------------------------

def parse_newline(nl_str: str) -> str:
    if nl_str in ("/r/n", "\\r\\n", "\r\n"):
        return "\r\n"
    if nl_str in ("/n", "\\n", "\n"):
        return "\n"
    if nl_str in ("/r", "\\r", "\r"):
        return "\r"
    return nl_str

@app.command("read")
def read_registers(
    ctx: typer.Context,
    queries: List[str] = typer.Argument(None, help="Register names, addresses, or free text"),
    target: Optional[str] = typer.Option(None, "--target", "-t", help="YAML device index or name"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Ad-hoc Modbus IP override"),
    port: Optional[int] = typer.Option(None, help="Ad-hoc port override"),
    unit_id: Optional[int] = typer.Option(None, help="Ad-hoc Slave Unit ID override"),
    schema: Optional[str] = typer.Option(None, help="Ad-hoc schema override"),
    format: str = typer.Option("csv", help="Output format: console, json, yaml, csv, ini, markdown, influx, prometheus, jsonl"),
    enum_mode: str = typer.Option("ordinal", help="Enum formatting: literal (string), ordinal (number)"),
    show_time: bool = typer.Option(True, "--time/--no-time", help="Include timestamp in the output"),
    time_format: str = typer.Option("%Y-%m-%d %H:%M:%S.%f", "--time-format", help="Timestamp format string"),
    timezone: str = typer.Option("UTC", "--timezone", help="Timezone for the timestamp (e.g. UTC, local, Europe/Berlin)"),
    interval: Optional[float] = typer.Option(None, "--interval", help="Continuously read registers every N seconds"),
    delimiter: str = typer.Option(";", "--delimiter", help="CSV field delimiter", rich_help_panel="CSV Options"),
    vertical: bool = typer.Option(False, "--vertical/--horizontal", help="Output layout orientation", rich_help_panel="CSV Options"),
    header: bool = typer.Option(False, "--header/--no-header", help="Include headers in output", rich_help_panel="CSV Options"),
    file: Optional[Path] = typer.Option(None, "--file", help="Write output to this file instead of printing to stdout"),
    newline: str = typer.Option("/r/n", "--newline", help="Line ending style for the output file: /r/n or /n"),
):
    """Read register values from a Modbus device."""
    if format not in ("csv", "markdown"):
        for opt_name in ["vertical", "header"]:
            source = ctx.get_parameter_source(opt_name)
            if source is not None and source.name == "COMMANDLINE":
                raise typer.BadParameter(
                    f"Option --{opt_name} is only available when --format is csv or markdown.",
                    param_hint=f"--{opt_name}"
                )
    if format != "csv":
        source = ctx.get_parameter_source("delimiter")
        if source is not None and source.name == "COMMANDLINE":
            raise typer.BadParameter(
                "Option --delimiter is only available when --format is csv.",
                param_hint="--delimiter"
            )

    try:
        if timezone.lower() == "local":
            tz = None
        else:
            tz = ZoneInfo(timezone)
    except Exception:
        raise typer.BadParameter(
            f"Invalid timezone: {timezone}",
            param_hint="--timezone"
        )

    try:
        device = resolve_device(target, host, port, unit_id, schema)
        engine = ModbusControlEngine(device)
    except Exception as e:
        console_output.print(f"[red]Error initializing Modbus engine: {e}[/red]")
        raise typer.Exit(code=1)

    # Resolve target registers to read
    # Combine query arguments and look for PascalCase words
    text_query = " ".join(queries or [])
    extracted_names = extract_pascal_case_words(text_query)
    
    # If no PascalCase found but direct arguments provided, try exact match
    if not extracted_names and queries:
        extracted_names = queries

    target_regs = []
    if extracted_names:
        for name in extracted_names:
            reg = engine.registers_by_name.get(name)
            if not reg:
                # Try by numeric decimal address
                try:
                    addr = int(name)
                    reg = engine.registers_by_addr.get(addr)
                except ValueError:
                    pass
            if reg:
                target_regs.append(reg)
    else:
        # Default: read all registers
        target_regs = engine.schema.registers

    if not target_regs:
        console_output.print("[red]Error: No registers resolved from query.[/red]")
        raise typer.Exit(code=1)

    # Perform Modbus Read
    async def perform_read():
        # Temporarily configure engine schema registers to read only target subset
        original_regs = engine.schema.registers
        engine.schema.registers = target_regs
        try:
            return await engine.read_all()
        finally:
            engine.schema.registers = original_regs
            try:
                await engine.client.close()
            except Exception:
                pass
            engine.client.client = None

    first_write = True
    try:
        while True:
            try:
                raw_results = asyncio.run(perform_read())
            except Exception as e:
                console_output.print(f"[red]Error executing Modbus read: {e}[/red]")
                raise typer.Exit(code=1)

            # Format values based on enum_mode
            formatted_results = {}
            for reg in target_regs:
                name = reg.name
                val = raw_results.get(name)
                if val is None:
                    continue
                # Enum mapping: enum_values keys are int (dict[int, str])
                if enum_mode == "literal" and reg.enum_values and val is not None:
                    try:
                        int_val = int(val)
                        formatted_results[name] = reg.enum_values.get(int_val, val)
                    except (TypeError, ValueError):
                        formatted_results[name] = val
                else:
                    formatted_results[name] = val

            now_str = None
            if show_time:
                now_str = datetime.now(tz).strftime(time_format)

            # Render output formats
            content = ""

            if format == "json":
                output_dict = {}
                if show_time:
                    output_dict["Time"] = now_str
                output_dict.update(formatted_results)
                content = json.dumps(output_dict, indent=2)

            elif format == "jsonl":
                output_dict = {}
                if show_time:
                    output_dict["Time"] = now_str
                output_dict.update(formatted_results)
                content = json.dumps(output_dict)

            elif format == "yaml":
                class QuotedString(str):
                    pass

                class CustomDumper(yaml.SafeDumper):
                    pass

                CustomDumper.add_representer(QuotedString, lambda d, data: d.represent_scalar('tag:yaml.org,2002:str', data, style='"'))

                yaml_results = {}
                if show_time:
                    yaml_results["Time"] = QuotedString(now_str)
                for k, v in formatted_results.items():
                    if isinstance(v, str):
                        yaml_results[k] = QuotedString(v)
                    else:
                        yaml_results[k] = v
                content = yaml.dump(yaml_results, Dumper=CustomDumper, sort_keys=False)

            elif format == "csv":
                def format_csv_cell(key: str, val) -> str:
                    if isinstance(val, bool):
                        return str(val)
                    if isinstance(val, (int, float)):
                        return str(val)
                    if key == "Time":
                        return str(val)
                    if isinstance(val, str):
                        escaped = val.replace('"', '""')
                        return f'"{escaped}"'
                    return str(val)

                fields = []
                if show_time:
                    fields.append(("Time", now_str))
                for k, v in formatted_results.items():
                    fields.append((k, v))

                if vertical:
                    csv_lines = []
                    if header:
                        csv_lines.append(f"Register{delimiter}Value")
                    for k, v in fields:
                        csv_lines.append(f"{k}{delimiter}{format_csv_cell(k, v)}")
                    content = "\n".join(csv_lines)
                else:
                    csv_lines = []
                    if header:
                        csv_lines.append(delimiter.join([f[0] for f in fields]))
                    csv_lines.append(delimiter.join([format_csv_cell(f[0], f[1]) for f in fields]))
                    content = "\n".join(csv_lines)

            elif format == "ini":
                def format_ini_value(v) -> str:
                    if isinstance(v, bool):
                        return str(v)
                    if isinstance(v, (int, float)):
                        return str(v)
                    if isinstance(v, str):
                        escaped = v.replace('"', '\\"')
                        return f'"{escaped}"'
                    return str(v)

                ini_lines = []
                if show_time:
                    ini_lines.append(f"Time={format_ini_value(now_str)}")
                for k, v in formatted_results.items():
                    ini_lines.append(f"{k}={format_ini_value(v)}")
                content = "\n".join(ini_lines)

            elif format == "markdown":
                def format_markdown_cell(key: str, val) -> str:
                    if isinstance(val, bool):
                        return str(val)
                    if isinstance(val, (int, float)):
                        return str(val)
                    if key == "Time":
                        return str(val)
                    if isinstance(val, str):
                        escaped = val.replace('|', '\\|').replace('"', '""')
                        return f'"{escaped}"'
                    return str(val)

                fields = []
                if show_time:
                    fields.append(("Time", now_str))
                for k, v in formatted_results.items():
                    fields.append((k, v))

                if vertical:
                    md_lines = []
                    if header:
                        md_lines.append("| Register | Value |")
                        md_lines.append("| --- | --- |")
                    for k, v in fields:
                        md_lines.append(f"| {k} | {format_markdown_cell(k, v)} |")
                    content = "\n".join(md_lines)
                else:
                    md_lines = []
                    if header:
                        md_lines.append("| " + " | ".join([f[0] for f in fields]) + " |")
                        md_lines.append("| " + " | ".join(["---"] * len(fields)) + " |")
                    md_lines.append("| " + " | ".join([format_markdown_cell(f[0], f[1]) for f in fields]) + " |")
                    content = "\n".join(md_lines)

            elif format == "influx":
                def format_influx_field(val) -> str:
                    if isinstance(val, bool):
                        return "true" if val else "false"
                    if isinstance(val, int):
                        return f"{val}i"
                    if isinstance(val, float):
                        return str(val)
                    if isinstance(val, str):
                        escaped = val.replace('"', '\\"')
                        return f'"{escaped}"'
                    return f'"{val}"'

                fields_str = []
                for k, v in formatted_results.items():
                    fields_str.append(f"{k}={format_influx_field(v)}")

                device_tag = f",device={device.name}" if getattr(device, "name", None) else ""
                fields_part = ",".join(fields_str)
                
                if show_time:
                    ns_timestamp = int(time.time_ns())
                    content = f"modbus{device_tag} {fields_part} {ns_timestamp}"
                else:
                    content = f"modbus{device_tag} {fields_part}"

            elif format == "prometheus":
                prom_lines = []
                ms_timestamp = f" {int(time.time() * 1000)}" if show_time else ""
                device_lbl = f'device="{device.name}"' if getattr(device, "name", None) else ""
                
                for k, v in formatted_results.items():
                    metric_name = re.sub(r'[^a-zA-Z0-9_]', '_', k)
                    if isinstance(v, bool):
                        val_str = "1" if v else "0"
                        labels_str = f"{{{device_lbl}}}" if device_lbl else ""
                    elif isinstance(v, (int, float)):
                        val_str = str(v)
                        labels_str = f"{{{device_lbl}}}" if device_lbl else ""
                    else:
                        val_str = "1"
                        escaped_val = str(v).replace('"', '\\"')
                        if device_lbl:
                            labels_str = f'{{{device_lbl},value="{escaped_val}"}}'
                        else:
                            labels_str = f'{{value="{escaped_val}"}}'
                    prom_lines.append(f"{metric_name}{labels_str} {val_str}{ms_timestamp}")
                content = "\n".join(prom_lines)

            else:
                # Default format: console
                console_lines = []
                if show_time:
                    console_lines.append(f"{'Time':<35}: {now_str}")
                for name in [r.name for r in target_regs]:
                    val = formatted_results.get(name)
                    if val is None:
                        if file is None:
                            console_lines.append(f"{name:<35}: [red]Error / Offline[/red]")
                        else:
                            console_lines.append(f"{name:<35}: Error / Offline")
                        continue
                    reg = engine.registers_by_name[name]
                    unit = getattr(reg, "unit", "") or ""
                    unit_str = f" {unit}" if unit else ""
                    console_lines.append(f"{name:<35}: {val}{unit_str}")
                content = "\n".join(console_lines)

            if file is not None:
                content = content.rstrip("\r\n")
                content = content.replace("\r\n", "\n").replace("\r", "\n")
                lines = content.split("\n")
                eol = parse_newline(newline)
                file_content = eol.join(lines) + eol

                try:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    mode = "w" if first_write else "a"
                    with open(file, mode, encoding="utf-8", newline="") as f:
                        f.write(file_content)
                    first_write = False
                except Exception as e:
                    console_output.print(f"[red]Error writing to file: {e}[/red]")
                    raise typer.Exit(code=1)
            else:
                if format == "console":
                    console_output.print(content)
                else:
                    print(content)

            if interval is None:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass

# ---------------------------------------------------------------------------
# Write Command
# ---------------------------------------------------------------------------

@app.command("write")
def write_registers(
    register_name: str = typer.Argument(..., help="Register name or address string to write to"),
    value: str = typer.Argument(..., help="Ordinal numeric value to write"),
    target: Optional[str] = typer.Option(None, "--target", "-t", help="YAML device index or name"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Ad-hoc Modbus IP override"),
    port: Optional[int] = typer.Option(None, help="Ad-hoc port override"),
    unit_id: Optional[int] = typer.Option(None, help="Ad-hoc Slave Unit ID override"),
    schema: Optional[str] = typer.Option(None, help="Ad-hoc schema override"),
):
    """Write an ordinal numeric value to a register."""
    try:
        device = resolve_device(target, host, port, unit_id, schema)
        engine = ModbusControlEngine(device)
    except Exception as e:
        console_output.print(f"[red]Error initializing Modbus engine: {e}[/red]")
        raise typer.Exit(code=1)

    # Parse ordinal numeric value
    try:
        parsed_val = parse_ordinal_value(value)
    except Exception as e:
        console_output.print(f"[red]Error parsing value: {e}[/red]")
        raise typer.Exit(code=1)

    # Perform write
    try:
        res = asyncio.run(engine.write_registers({register_name: parsed_val}))
    except Exception as e:
        console_output.print(f"[red]Error executing Modbus write: {e}[/red]")
        raise typer.Exit(code=1)

    status = res.get(register_name)
    # Match back to resolved name for clean feedback
    resolved_name = register_name
    reg = engine.registers_by_name.get(register_name)
    if not reg:
        try:
            addr = int(register_name)
            reg = engine.registers_by_addr.get(addr)
        except ValueError:
            pass
    if reg:
        resolved_name = reg.name
        status = res.get(reg.name, status)

    if status == "Success":
        # Check if we can display an enum literal representation
        literal_part = ""
        if reg and reg.enum_values:
            try:
                literal_part = f' ("{reg.enum_values.get(int(parsed_val), "")}")'
                if literal_part == ' ("")':
                    literal_part = ""
            except (TypeError, ValueError):
                pass
        console_output.print(f"[green]Success: {resolved_name} set to {parsed_val}{literal_part}[/green]")
    else:
        console_output.print(f"[red]Failed: {resolved_name} - {status}[/red]")
        raise typer.Exit(code=1)

@app.command("dashboard")
def run_tui_dashboard(
    target: Optional[str] = typer.Option(None, "--target", "-t", help="YAML device index or name"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Ad-hoc Modbus IP override"),
    port: Optional[int] = typer.Option(None, help="Ad-hoc port override"),
    unit_id: Optional[int] = typer.Option(None, help="Ad-hoc Slave Unit ID override"),
    schema: Optional[str] = typer.Option(None, help="Ad-hoc schema override"),
    interval: float = typer.Option(1.0, "--interval", help="Refresh interval in seconds"),
    timezone: str = typer.Option("UTC", "--timezone", help="Timezone for the timestamp (e.g. UTC, local, Europe/Berlin)"),
    time_format: str = typer.Option("%Y-%m-%d %H:%M:%S", "--time-format", help="Timestamp format string"),
):
    """Monitor register values in real-time with an interactive TUI dashboard."""
    from .dashboard import run_tui_dashboard_impl
    run_tui_dashboard_impl(
        target=target,
        host=host,
        port=port,
        unit_id=unit_id,
        schema=schema,
        interval=interval,
        timezone=timezone,
        time_format=time_format,
    )

if __name__ == "__main__":
    app()
