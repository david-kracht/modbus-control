import asyncio
from datetime import datetime
import math
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
import logging

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box
from rich.markup import escape

from modbus_common import AppConfig, DeviceConfig
from modbus_ctrl_core import ModbusControlEngine, resolve_schema, config
from modbus_schema_common.models import ModbusRegisterType

console_output = Console()

class RawTerminal:
    def __enter__(self):
        self.old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())
        return self

    def __exit__(self, type, value, traceback):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)

_key_queue = []

def get_key(timeout=0.05) -> str:
    global _key_queue
    if not _key_queue:
        fd = sys.stdin.fileno()
        rlist, _, _ = select.select([fd], [], [], timeout)
        if rlist:
            try:
                data = os.read(fd, 1024)
                text = data.decode('utf-8', errors='ignore')
                i = 0
                while i < len(text):
                    if text[i] == '\x1b':
                        if i + 5 < len(text) and text[i:i+6] in ('\x1b[1;2A', '\x1b[1;2B'):
                            _key_queue.append(text[i:i+6])
                            i += 6
                        elif i + 2 < len(text) and text[i+1] in ('[', 'O'):
                            _key_queue.append(text[i:i+3])
                            i += 3
                        else:
                            _key_queue.append(text[i])
                            i += 1
                    else:
                        _key_queue.append(text[i])
                        i += 1
            except Exception:
                pass
    if _key_queue:
        return _key_queue.pop(0)
    return ""

def parse_ordinal_value(val_str: str) -> float | int:
    m = re.match(r'^-?\d+(?:\.\d+)?', val_str.strip())
    if not m:
        raise ValueError(f"Value '{val_str}' is not a valid numeric value.")
    num_str = m.group(0)
    if "." in num_str:
        return float(num_str)
    return int(num_str)


def is_holding_register_sentinel(val, data_type) -> bool:
    """Returns True when a holding-register value is the 'unset' sentinel:
    - None         (backend serialises NaN floats as null/None)
    - float('nan') (engine.read_all() may return nan for unset float32 HRs)
    - -1           (default for integer holding registers)
    """
    if val is None:
        return True
    dt_str = data_type.value if hasattr(data_type, "value") else str(data_type)
    if dt_str == "float32":
        return isinstance(val, float) and math.isnan(val)
    return val == -1

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

    config_path = config.MODBUS_DEVICES_YAML
    app_config = None
    if config_path.exists():
        try:
            app_config = AppConfig.load_from_yaml(config_path)
        except Exception:
            pass

    selected_dev = None

    if target is not None:
        if not app_config or not app_config.devices:
            raise ValueError(f"Target device '{target}' requested, but devices.yaml is empty or could not be loaded.")
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
            raise ValueError(f"Target device '{target}' not found by index or name in devices.yaml.")
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

    raise ValueError("No devices configured in devices.yaml, and no DEFAULT_MODBUS_HOST environment fallback set.")

def run_tui_dashboard_impl(
    target: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    unit_id: Optional[int] = None,
    schema: Optional[str] = None,
    interval: float = 1.0,
    timezone: str = "UTC",
    time_format: str = "%Y-%m-%d %H:%M:%S",
):
    """
    Initializes and runs the rich-based Terminal User Interface (TUI) for the Modbus dashboard.
    
    This function handles the main event loop, layout building, asynchronous polling 
    tasks, and keyboard event processing to provide an interactive dashboard.
    
    Args:
        target: Optional name or index of a pre-configured device in devices.yaml.
        host: Optional override for the Modbus IP address.
        port: Optional override for the Modbus Port.
        unit_id: Optional override for the Modbus Unit ID (Slave ID).
        schema: Optional override for the schema package/version to use.
        interval: Polling interval in seconds if not defined by the device.
        timezone: Timezone for the display clock (e.g., "UTC" or "local").
        time_format: Display format for the clock.
    """
    try:
        if timezone.lower() == "local":
            tz = None
        else:
            tz = ZoneInfo(timezone)
    except Exception:
        console_output.print(f"[red]Error: Invalid timezone: {timezone}[/red]")
        sys.exit(1)

    # Silence logging output during dashboard run to prevent screen corruption
    original_log_level = logging.getLogger().getEffectiveLevel()
    logging.getLogger().setLevel(logging.CRITICAL)
    core_logger = logging.getLogger("modbus_ctrl_core")
    original_core_level = core_logger.getEffectiveLevel()
    core_logger.setLevel(logging.CRITICAL)
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

    show_sidebar = (host is None)
    devices = []
    selected_device_index = 0
    focused_panel = "registers"
    config_path = config.MODBUS_DEVICES_YAML
    
    if show_sidebar:
        try:
            app_config = AppConfig.load_from_yaml(config_path)
            devices = app_config.devices
        except Exception:
            devices = []
            
        if devices:
            # Resolve index if target is specified
            if target is not None:
                for idx, dev in enumerate(devices):
                    if dev.name == target or str(idx) == target:
                        selected_device_index = idx
                        break
            selected_device_index = max(0, min(selected_device_index, len(devices) - 1))
            device = devices[selected_device_index]
            try:
                engine = ModbusControlEngine(device)
            except Exception as e:
                console_output.print(f"[red]Error initializing Modbus engine: {e}[/red]")
                sys.exit(1)
        else:
            device = None
            engine = None
            focused_panel = "devices"
    else:
        try:
            device = resolve_device(target, host, port, unit_id, schema)
            engine = ModbusControlEngine(device)
        except Exception as e:
            console_output.print(f"[red]Error initializing Modbus engine: {e}[/red]")
            sys.exit(1)

    # Interactive TUI states
    selected_index = 0
    filter_text = ""
    filter_focused = False
    active_modal = None  # None, "write", "write_enum", "add_device", "edit_device", "select_registers", "help"
    previous_device_modal = None
    write_value_buffer = ""
    write_enum_options: list = []   # list of (int_code, label_str) for enum picker
    write_enum_index = 0
    write_enum_selected: "int | None" = None  # currently toggled code
    device_form_fields = {"name": "", "host": "", "port": "502", "unit_id": "1", "schema_name": config.DEFAULT_MODBUS_SCHEMA, "polling_interval": "1.0", "registers": ""}
    device_form_cursor = 0
    paused = False
    status_message = ""
    last_poll_time = 0.0
    raw_results = {}
    online = False
    error_msg = None
    staged_changes = {}
    last_successful_poll_time = None

    # Registers multi-select dropdown TUI states
    register_select_list = []
    register_select_checked = set()
    register_select_index = 0
    register_select_filter = ""

    schema_select_list = []
    schema_select_index = 0

    # Background polling states
    poll_task = None
    poll_updated = False

    layout = Layout()
    layout.split(
        Layout(name="header", size=1),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=1)
    )

    layout["body"].split_row(
        Layout(name="main_table", ratio=1)
    )

    async def poll_data():
        if engine is None:
            return {}, False, "No device connected"
        try:
            try:
                results = await engine.read_all()
                is_online = True
                err = None
            finally:
                try:
                    await engine.client.close()
                except Exception:
                    pass
                engine.client.client = None
        except Exception as e:
            results = {}
            is_online = False
            err = str(e)
        return results, is_online, err

    async def polling_loop():
        nonlocal raw_results, online, error_msg, last_successful_poll_time, last_poll_time, poll_updated
        while True:
            try:
                if not paused and active_modal is None and engine is not None:
                    current_time = time.time()
                    res, is_on, err = await poll_data()
                    raw_results = res
                    online = is_on
                    error_msg = err
                    last_poll_time = current_time
                    if online:
                        last_successful_poll_time = current_time
                    poll_updated = True
            except asyncio.CancelledError:
                break
            except Exception as pe:
                error_msg = str(pe)
                online = False
                poll_updated = True

            sleep_int = device.polling_interval if device else interval
            try:
                await asyncio.sleep(sleep_int)
            except asyncio.CancelledError:
                break

    def start_polling():
        nonlocal poll_task
        if poll_task:
            poll_task.cancel()
        poll_task = asyncio.create_task(polling_loop())

    def select_device(idx: int):
        nonlocal selected_device_index, device, engine, selected_index, staged_changes, raw_results, online, error_msg, last_successful_poll_time, last_poll_time
        selected_device_index = idx
        if 0 <= selected_device_index < len(devices):
            device = devices[selected_device_index]
            try:
                engine = ModbusControlEngine(device)
                error_msg = None
            except Exception as e:
                engine = None
                error_msg = str(e)
        else:
            device = None
            engine = None
            error_msg = "No device configured"
        
        selected_index = 0
        staged_changes.clear()
        raw_results.clear()
        online = False
        last_successful_poll_time = None
        last_poll_time = 0.0
        
        # Restart the background polling loop for the new device
        start_polling()

    def update_tui(filtered_regs):
        nonlocal register_select_index
        # Header Panel
        if last_successful_poll_time is None:
            poll_time_part = "never"
        else:
            elapsed = max(0, int(time.time() - last_successful_poll_time))
            if elapsed < 60:
                poll_time_part = f"{elapsed}s ago"
            else:
                poll_time_part = f"{elapsed // 60}m ago"

        status_text = "[bold green]🟢 CONNECTED[/bold green]" if online else "[bold red]🔴 OFFLINE[/bold red]"
        error_part = f" | [red]Error: {error_msg}[/red]" if error_msg else ""
        
        if device is None:
            header_text = Text.from_markup(
                f"[bold blue]⚡ {config.SUITE_TITLE}[/bold blue] | No Device Configured"
            )
        else:
            header_text = Text.from_markup(
                f"[bold blue]⚡ {config.SUITE_TITLE}[/bold blue] | "
                f"Target: [yellow]{device.host}:{device.port}[/yellow] | "
                f"ID: [cyan]{device.unit_id}[/cyan] | "
                f"Status: {status_text} ([cyan]{poll_time_part}[/cyan] / [yellow]{device.polling_interval}s[/yellow]){error_part}"
            )
        layout["header"].update(Align.center(header_text, vertical="middle"))

        # Split body dynamically
        if show_sidebar:
            layout["body"].split_row(
                Layout(name="devices_sidebar", ratio=1),
                Layout(name="main_content", ratio=8)
            )
        else:
            layout["body"].split_row(
                Layout(name="main_content", ratio=1)
            )

        if active_modal in ("add_device", "edit_device"):
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=2),
                Layout(name="modal_view", ratio=3)
            )
            # Render device form in modal_view
            form_lines = []
            fields_list = ["name", "host", "port", "unit_id", "schema_name", "polling_interval", "registers"]
            labels = ["Name", "Host IP *", "Port", "Unit ID", "Schema Name", "Poll Interval (s)", "Registers"]
            
            for f_idx, field in enumerate(fields_list):
                label = labels[f_idx]
                val = device_form_fields[field]
                is_field_focused = (f_idx == device_form_cursor)
                
                # Format registers list value nicely
                if field == "registers":
                    if not val.strip():
                        display_val = f"Press {escape('[Enter]')} to select (All)..."
                    else:
                        num_selected = len([r.strip() for r in val.split(",") if r.strip()])
                        display_val = f"{num_selected} selected (Press {escape('[Enter]')} to select)"
                else:
                    display_val = val

                if is_field_focused:
                    form_lines.append(f"[bold yellow]> {label}: [bold green]{display_val}[/bold green]▎[/bold yellow]")
                else:
                    form_lines.append(f"  {label}: {display_val}")
            
            form_text = Text.from_markup("\n".join(form_lines) + f"\n\n   [bold]{escape('[Enter]')} Confirm[/bold]    [dim]{escape('[Esc]')} Cancel[/dim]")
            title = "Add Device" if active_modal == "add_device" else "Edit Device"
            layout["modal_view"].update(
                Panel(
                    Align.left(form_text),
                    title=title,
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        elif active_modal == "select_registers":
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=2),
                Layout(name="modal_view", ratio=3)
            )
            # Render multi-select registers viewport list
            schema_dict = {r["name"]: r for r in register_select_list}
            selected_items = []
            for name in register_select_checked:
                if name in schema_dict:
                    selected_items.append(schema_dict[name])
            
            unselected_items = [r for r in register_select_list if r["name"] not in register_select_checked]
            
            # Add section headers
            all_items = []
            if selected_items:
                all_items.append({"header": "Selected Registers"})
                all_items.extend(selected_items)
            if unselected_items:
                all_items.append({"header": "Available Registers"})
                all_items.extend(unselected_items)
                
            visible_sel_regs = []
            for item in all_items:
                if "header" in item:
                    visible_sel_regs.append(item)
                elif register_select_filter in item["name"]:
                    visible_sel_regs.append(item)
                    
            # Ensure index does not rest on a header
            if visible_sel_regs and "header" in visible_sel_regs[register_select_index % len(visible_sel_regs)]:
                for _ in range(len(visible_sel_regs)):
                    register_select_index = (register_select_index + 1) % len(visible_sel_regs)
                    if "header" not in visible_sel_regs[register_select_index]:
                        break
                        
            total_sel = len(visible_sel_regs)
            
            max_sel_rows = max(5, console_output.height - 10)
            if total_sel > max_sel_rows:
                sel_start = max(0, register_select_index - max_sel_rows // 2)
                sel_end = min(total_sel, sel_start + max_sel_rows)
                if sel_end - sel_start < max_sel_rows:
                    sel_start = max(0, sel_end - max_sel_rows)
                sliced_sel = visible_sel_regs[sel_start:sel_end]
                sel_title = f"Select Registers ({sel_start + 1}-{sel_end} of {total_sel})"
            else:
                sliced_sel = visible_sel_regs
                sel_start = 0
                sel_title = f"Select Registers ({total_sel})"
                
            sel_lines = []
            sel_lines.append(f"🔍 Search: [bold white]{register_select_filter}[/bold white]▎ (Shift+Up/Down to reorder)")
            sel_lines.append("")
            
            for s_offset, item in enumerate(sliced_sel):
                s_idx = sel_start + s_offset
                is_highlighted = (s_idx == register_select_index)
                
                if "header" in item:
                    line = f"[bold cyan]--- {item['header']} ---[/bold cyan]"
                    if is_highlighted:
                        line = f"[bold yellow]> {line}[/bold yellow]"
                    sel_lines.append(line)
                    continue
                    
                rname = item["name"]
                raddr = item["addr"]
                is_checked = rname in register_select_checked
                
                check_char = f"✨ [bold yellow]{escape('[x]')}[/bold yellow]" if is_checked else f"  [dim]{escape('[ ]')}[/dim]"
                display_text = f"{rname} ({raddr})"
                
                if is_highlighted:
                    line = f"[bold yellow]> {check_char} {display_text}[/bold yellow]"
                else:
                    line = f"  {check_char} {display_text}"
                sel_lines.append(line)
                
            sel_text = Text.from_markup(
                "\n".join(sel_lines) + 
                f"\n\n  [bold]{escape('[Space]')} Toggle[/bold]   [bold]{escape('[Enter]')} Confirm[/bold]   [dim]{escape('[Esc]')} Back[/dim]"
            )
            layout["modal_view"].update(
                Panel(
                    Align.left(sel_text),
                    title=sel_title,
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        elif active_modal == "select_schema":
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=2),
                Layout(name="modal_view", ratio=3)
            )
            sel_lines = []
            for i, sch in enumerate(schema_select_list):
                is_highlighted = (i == schema_select_index)
                is_selected = (sch == device_form_fields.get("schema_name", ""))
                
                check_char = f"✨ [bold yellow]{escape('[x]')}[/bold yellow]" if is_selected else f"  [dim]{escape('[ ]')}[/dim]"
                
                if is_highlighted:
                    line = f"[bold yellow]> {check_char} {sch}[/bold yellow]"
                else:
                    line = f"  {check_char} {sch}"
                sel_lines.append(line)
            
            sel_text = Text.from_markup(
                "\n".join(sel_lines) + 
                f"\n\n   [bold]{escape('[Enter]')} Select[/bold]   [dim]{escape('[Esc]')} Back[/dim]"
            )
            layout["modal_view"].update(
                Panel(
                    Align.left(sel_text),
                    title="Select Schema",
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        elif active_modal == "help":
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=1),
                Layout(name="modal_view", ratio=1)
            )
            help_markup = (
                "[bold yellow]TUI Dashboard Keybindings[/bold yellow]\n\n"
                "[bold white]Navigation & View[/bold white]\n"
                f"  [cyan]{escape('[Tab]')} / {escape('[←]')} / {escape('[→]')}[/cyan]   Switch focus between Devices & Registers\n"
                f"  [cyan]{escape('[↑]')} / {escape('[↓]')}[/cyan]           Navigate lists / sidebars\n"
                f"  [cyan]{escape('[Space]')}[/cyan]           Pause / Resume polling\n"
                f"  [cyan]{escape('[f]')}[/cyan]               Focus Filter input\n"
                f"  [cyan]{escape('[r]')}[/cyan]               Trigger immediate refresh\n\n"
                "[bold white]Modbus Writes[/bold white]\n"
                f"  [cyan]{escape('[Enter]')} (Coil)[/cyan]   Toggle value / trigger WO coil\n"
                f"  [cyan]{escape('[Enter]')} (HR)[/cyan]     Open write register modal (stages change)\n"
                f"  [cyan]{escape('[a]')}[/cyan]               Apply all staged changes (batch write)\n"
                f"  [cyan]{escape('[c]')}[/cyan]               Clear/discard staged changes\n\n"
                "[bold white]Device Configuration[/bold white]\n"
                f"  [cyan]{escape('[n]')}[/cyan]               Add new device configuration\n"
                f"  [cyan]{escape('[e]')}[/cyan]               Edit selected device configuration\n"
                f"  [cyan]{escape('[d]')} / {escape('[–]')}[/cyan]           Delete selected device configuration\n\n"
                "[bold white]General[/bold white]\n"
                f"  [cyan]{escape('[h]')} / {escape('[?]')}[/cyan]           Toggle this Help popup\n"
                f"  [cyan]{escape('[q]')} / Ctrl+C[/cyan]      Quit TUI\n\n"
                "[dim]Press any key to close this popup.[/dim]"
            )
            layout["modal_view"].update(
                Panel(
                    Align.left(Text.from_markup(help_markup)),
                    title="Help & Keybindings",
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        elif active_modal == "write" and filtered_regs:
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=3),
                Layout(name="modal_view", ratio=2)
            )
            # Render free-text write modal
            selected_reg = filtered_regs[selected_index]
            cur_val = raw_results.get(selected_reg.name)
            is_sentinel = is_holding_register_sentinel(cur_val, selected_reg.data_type)
            if is_sentinel:
                cur_val_str = "[dim]\u2014 not configured \u2014[/dim]"
                prompt_str = "[dim]Not configured \u2014 enter a value to set:[/dim]"
            else:
                dt_str = (
                    selected_reg.data_type.value
                    if hasattr(selected_reg.data_type, "value")
                    else str(selected_reg.data_type)
                )
                if dt_str == "float32" and isinstance(cur_val, float):
                    cur_val_str = f"[bold]{cur_val:.2f}[/bold]"
                else:
                    cur_val_str = f"[bold]{cur_val}[/bold]"
                prompt_str = "[dim]Enter new value:[/dim]"
            unit_str = f" {selected_reg.unit}" if selected_reg.unit else ""
            modal_content = [
                Text.from_markup(
                    f"Name: [bold cyan]{escape(selected_reg.name)}[/bold cyan]\n"
                ),
                Text.from_markup(
                    f"Addr: [yellow]{selected_reg.address_dec}[/yellow]"
                    f" ({selected_reg.register_type.value})\n"
                ),
                Text.from_markup(
                    f"Type: [magenta]{selected_reg.data_type.value if hasattr(selected_reg.data_type, 'value') else selected_reg.data_type}[/magenta]"
                    f"{unit_str}\n\n"
                ),
                Text.from_markup(f"Current: {cur_val_str}\n"),
                Text.from_markup(f"{prompt_str}\n"),
                Text.from_markup(
                    f"[bold green]{write_value_buffer}[/bold green]\u258e\n\n"
                ),
                Text.from_markup(
                    f"   [bold]{escape('[Enter]')} Stage[/bold]"
                    f"    [dim]{escape('[Esc]')} Cancel[/dim]"
                ),
            ]
            layout["modal_view"].update(
                Panel(
                    Align.left(Text.join(Text("\n"), modal_content)),
                    title="Edit Register",
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        elif active_modal == "write_enum" and filtered_regs:
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=3),
                Layout(name="modal_view", ratio=2)
            )
            # Render enum picker modal
            selected_reg = filtered_regs[selected_index]
            cur_val = raw_results.get(selected_reg.name)
            is_sentinel = is_holding_register_sentinel(cur_val, selected_reg.data_type)
            if is_sentinel:
                cur_val_str = "[dim]\u2014 not configured \u2014[/dim]"
            else:
                try:
                    cur_label = (
                        selected_reg.enum_values.get(int(cur_val), str(cur_val))
                        if selected_reg.enum_values else str(cur_val)
                    )
                    cur_val_str = f"[bold]{escape(cur_label)} ({cur_val})[/bold]"
                except (TypeError, ValueError):
                    cur_val_str = f"[bold]{cur_val}[/bold]"
            enum_lines = [
                f"Name: [bold cyan]{escape(selected_reg.name)}[/bold cyan]",
                f"Addr: [yellow]{selected_reg.address_dec}[/yellow]"
                f" ({selected_reg.register_type.value})",
                "",
                f"Current: {cur_val_str}",
                "",
            ]
            safe_idx = (
                write_enum_index % len(write_enum_options)
                if write_enum_options else 0
            )
            for i, (code, label) in enumerate(write_enum_options):
                is_current = (
                    not is_sentinel
                    and cur_val is not None
                    and int(cur_val) == code
                )
                is_highlighted = (i == safe_idx)
                is_toggled = (write_enum_selected == code)
                if is_toggled:
                    check = "[bold green]x[/bold green]"
                elif is_current:
                    check = "[dim]◆[/dim]"
                else:
                    check = " "
                if is_highlighted:
                    enum_lines.append(
                        f"[bold yellow]> [{check}] {escape(label)} ({code})[/bold yellow]"
                    )
                else:
                    enum_lines.append(f"  [{check}] {escape(label)} ({code})")
            enum_lines += [
                "",
                f"   [bold]{escape('[Space]')} Toggle[/bold]"
                f"   [bold]{escape('[Enter]')} Confirm[/bold]"
                f"    [dim]{escape('[Esc]')} Cancel[/dim]",
            ]
            layout["modal_view"].update(
                Panel(
                    Align.left(Text.from_markup("\n".join(enum_lines))),
                    title="Select Value",
                    box=box.ROUNDED,
                    style="yellow"
                )
            )
        else:
            layout["main_content"].split_row(
                Layout(name="main_table", ratio=1)
            )

        # Render Devices Sidebar
        if show_sidebar:
            sidebar_lines = []
            for d_idx, dev in enumerate(devices):
                is_selected = (d_idx == selected_device_index)
                is_focused = (focused_panel == "devices" and is_selected)
                
                if is_focused:
                    prefix = "[bold yellow]> [/bold yellow]"
                elif is_selected:
                    prefix = "[bold cyan]* [/bold cyan]"
                else:
                    prefix = "  "
                
                active_str = "" if dev.active else " [dim](inactive)[/dim]"
                style = "bold white on blue" if is_selected else ""
                sidebar_lines.append(Text.from_markup(f"{prefix}{dev.name}{active_str}", style=style))
            
            if not devices:
                sidebar_text = Text.from_markup(f"[dim]No devices.\nPress {escape('[n]')}ew.[/dim]")
            else:
                sidebar_text = Text.join(Text("\n"), sidebar_lines)
                
            layout["devices_sidebar"].update(
                Panel(
                    Align.left(sidebar_text),
                    title="Devices",
                    box=box.ROUNDED,
                    style="blue" if focused_panel == "devices" else "dim"
                )
            )

        # Dynamic viewport scrolling configuration
        max_rows = max(5, console_output.height - 8)
        total_regs = len(filtered_regs)
        if total_regs > max_rows:
            start_idx = max(0, selected_index - max_rows // 2)
            end_idx = min(total_regs, start_idx + max_rows)
            if end_idx - start_idx < max_rows:
                start_idx = max(0, end_idx - max_rows)
            sliced_regs = filtered_regs[start_idx:end_idx]
            title_text = f"Registers ({start_idx + 1}-{end_idx} of {total_regs})"
        else:
            sliced_regs = filtered_regs
            start_idx = 0
            title_text = "Registers"

        # Registers Table
        name_header = "Name"
        if filter_text or filter_focused:
            cursor = "▎" if filter_focused else ""
            name_header = f"Name / [bold white]{escape(filter_text)}[/bold white]{cursor}"

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column(name_header, style="bold cyan")
        table.add_column("Address", justify="center", style="dim")
        table.add_column("Type", justify="center", style="dim")
        table.add_column("Value", justify="left")
        table.add_column("Unit", justify="center", style="dim")
        table.add_column("Action", justify="center")

        for idx_offset, reg in enumerate(sliced_regs):
            actual_idx = start_idx + idx_offset
            is_active = (actual_idx == selected_index)
            prefix = "[bold yellow]> [/bold yellow]" if is_active else "  "

            addr_dec = reg.address_dec
            reg_type_name = reg.register_type.value if hasattr(reg.register_type, 'value') else str(reg.register_type)
            
            # Resolve value: check if staged
            is_staged = reg.name in staged_changes
            val = staged_changes[reg.name] if is_staged else raw_results.get(reg.name)
            unit = getattr(reg, "unit", "") or ""

            if reg.register_type in (ModbusRegisterType.COIL, "coil", ModbusRegisterType.DISCRETE_INPUT, "discrete_input"):
                action_str = "[Trigger]" if reg.access == "WO" else ("[Toggle]" if reg.access == "RW" else "[Read]")
            elif reg.register_type in (ModbusRegisterType.HOLDING_REGISTER, "holding_register"):
                action_str = "[Edit]" if reg.access in ("RW", "WO") else "[Read]"
            else:
                action_str = "[Read]"

            _is_hr = reg.register_type in (ModbusRegisterType.HOLDING_REGISTER, "holding_register")
            if not online and not is_staged:
                val_markup = "[bold red]Offline / Error[/bold red]"
            elif not is_staged and _is_hr and is_holding_register_sentinel(val, reg.data_type):
                val_markup = ""  # blank — sentinel / not yet configured
            elif val is None:
                val_markup = "[bold red]Offline / Error[/bold red]"
            else:
                literal_val = val
                if reg.enum_values:
                    try:
                        int_val = int(val)
                        literal_val = reg.enum_values.get(int_val, val)
                    except (TypeError, ValueError):
                        pass

                if isinstance(literal_val, bool):
                    val_markup = f"[bold green]True[/bold green]" if literal_val else f"[bold red]False[/bold red]"
                elif isinstance(literal_val, (int, float)):
                    val_markup = f"[bold yellow]{literal_val}[/bold yellow]"
                else:
                    val_markup = f"[bold cyan]\"{literal_val}\"[/bold cyan]"

                if is_staged:
                    val_markup = f"{val_markup} [bold yellow on black]✨ STAGED[/bold yellow on black]"

            if is_active:
                style = "bold white on blue"
            elif is_staged:
                style = "bold yellow"
            else:
                style = ""

            table.add_row(
                Text.from_markup(f"{prefix}{reg.name}", style=style),
                Text(str(addr_dec), style=style),
                Text(reg_type_name, style=style),
                Text.from_markup(val_markup, style=style),
                Text(unit or "-", style=style),
                Text(action_str, style=style)
            )

        style_table_panel = "blue" if (focused_panel == "registers" and device is not None) else "dim"
        if device is None:
            layout["main_table"].update(
                Panel(
                    Align.center(Text.from_markup(f"\n\n[bold yellow]No Device Configured[/bold yellow]\n\nPress [bold green]{escape('[n]')}ew[/bold green] in the sidebar to add a new device."), vertical="middle"),
                    title="Registers",
                    style=style_table_panel
                )
            )
        else:
            layout["main_table"].update(Panel(table, title=title_text, style=style_table_panel))

        # Footer Panel
        now_str = datetime.now(tz).strftime(time_format)
        paused_str = " [bold yellow](PAUSED)[/bold yellow]" if paused else ""
        status_part = f" | {status_message}" if status_message else ""

        if active_modal in ("add_device", "edit_device"):
            help_str = f"{escape('[Esc]')} cancel | {escape('[Enter]')} confirm | {escape('[↑/↓]')} change field"
        elif active_modal == "select_registers":
            help_str = f"{escape('[Space]')} toggle | {escape('[Enter]')} confirm | {escape('[Esc]')} cancel"
        elif active_modal == "help":
            help_str = "Press any key to close help"
        elif active_modal == "write":
            help_str = f"{escape('[Esc]')} cancel | {escape('[Enter]')} stage value"
        elif active_modal == "write_enum":
            help_str = (
                f"{escape('[↑/↓]')} navigate"
                f" | {escape('[Space]')} toggle"
                f" | {escape('[Enter]')} confirm"
                f" | {escape('[Esc]')} cancel"
            )
        elif filter_focused:
            help_str = f"{escape('[Esc]')} exit filter | {escape('[Enter]')} confirm filter | Type search"
        elif focused_panel == "devices":
            help_str = f"{escape('[Tab/→]')} focus table | {escape('[n]')}ew | {escape('[e]')}dit | {escape('[d]')}elete | {escape('[↑/↓]')} select device | {escape('[h]')}elp"
        else:
            pending_count = len(staged_changes)
            apply_part = f" | [bold yellow]{escape('[a]')}pply ({pending_count})[/bold yellow] | {escape('[c]')}lear" if pending_count > 0 else ""
            focus_str = f"{escape('[Tab/←]')} focus devices | " if show_sidebar else ""
            help_str = f"{focus_str}{escape('[↑/↓]')} navigate | {escape('[Enter]')} edit/toggle | {escape('[f]')}ilter | {escape('[Space]')} pause{apply_part} | {escape('[h]')}elp | {escape('[q]')}uit"

        footer_text = Text.from_markup(
            f"[dim]{help_str}[/dim] | Time: [yellow]{now_str}[/yellow]{paused_str}{status_part}"
        )
        layout["footer"].update(Align.center(footer_text, vertical="middle"))

    async def main_loop():
        nonlocal selected_index, filter_text, filter_focused, active_modal, write_value_buffer, write_enum_options, write_enum_index, write_enum_selected, paused, status_message, last_poll_time, raw_results, online, error_msg, last_successful_poll_time
        nonlocal device, engine, devices, selected_device_index, focused_panel, device_form_cursor, device_form_fields
        nonlocal previous_device_modal, register_select_list, register_select_checked, register_select_index, register_select_filter
        nonlocal schema_select_list, schema_select_index
        nonlocal poll_updated
        
        # Start background polling loop
        start_polling()

        last_time_str = ""
        last_epoch_sec = 0
        last_size = console_output.size
        while True:
            # 1. Filter registers
            filtered_regs = []
            if engine and engine.schema:
                for reg in engine.schema.registers:
                    if filter_text.strip():
                        if filter_text not in reg.name:
                            continue
                    filtered_regs.append(reg)

            if filtered_regs:
                selected_index = min(selected_index, len(filtered_regs) - 1)
                selected_index = max(0, selected_index)
            else:
                selected_index = 0

            # 2. Read Key Input (non-blocking)
            key = get_key(0.0)

            # 3. Handle Key Input (if any)
            key_pressed = False
            if key:
                key_pressed = True
                
                # Help popup closes on any keypress
                if active_modal == "help":
                    active_modal = None
                    continue

                if key == "\x03" or (not filter_focused and not active_modal and key == "q"):
                    break

                if filter_focused:
                    if key in ("\r", "\n"):
                        filter_focused = False
                    elif key == "\x1b":  # Escape
                        filter_focused = False
                    elif key in ("\x7f", "\x08"):  # Backspace
                        filter_text = filter_text[:-1]
                    elif len(key) == 1 and key.isprintable():
                        filter_text += key

                elif active_modal == "select_registers":
                    schema_dict = {r["name"]: r for r in register_select_list}
                    selected_items = []
                    for name in register_select_checked:
                        if name in schema_dict:
                            selected_items.append(schema_dict[name])
                    unselected_items = [r for r in register_select_list if r["name"] not in register_select_checked]
                    
                    all_items = []
                    if selected_items:
                        all_items.append({"header": "Selected Registers"})
                        all_items.extend(selected_items)
                    if unselected_items:
                        all_items.append({"header": "Available Registers"})
                        all_items.extend(unselected_items)
                        
                    visible_sel_regs = []
                    for item in all_items:
                        if "header" in item:
                            visible_sel_regs.append(item)
                        elif register_select_filter in item["name"]:
                            visible_sel_regs.append(item)
                            
                    # Ensure index does not rest on a header
                    if visible_sel_regs and "header" in visible_sel_regs[register_select_index % len(visible_sel_regs)]:
                        for _ in range(len(visible_sel_regs)):
                            register_select_index = (register_select_index + 1) % len(visible_sel_regs)
                            if "header" not in visible_sel_regs[register_select_index]:
                                break
                            
                    if key == "\x1b":  # Escape
                        active_modal = previous_device_modal
                    elif key in ("\x1b[A", "\x1bOA"):  # Up
                        if visible_sel_regs:
                            for _ in range(len(visible_sel_regs)):
                                register_select_index = (register_select_index - 1) % len(visible_sel_regs)
                                if "header" not in visible_sel_regs[register_select_index]:
                                    break
                    elif key in ("\x1b[B", "\x1bOB"):  # Down
                        if visible_sel_regs:
                            for _ in range(len(visible_sel_regs)):
                                register_select_index = (register_select_index + 1) % len(visible_sel_regs)
                                if "header" not in visible_sel_regs[register_select_index]:
                                    break
                    elif key == "\x1b[1;2A":  # Shift+Up
                        if visible_sel_regs:
                            item = visible_sel_regs[register_select_index]
                            if "name" in item and item["name"] in register_select_checked:
                                rname = item["name"]
                                idx = register_select_checked.index(rname)
                                if idx > 0:
                                    register_select_checked[idx], register_select_checked[idx-1] = register_select_checked[idx-1], register_select_checked[idx]
                                    register_select_index -= 1
                    elif key == "\x1b[1;2B":  # Shift+Down
                        if visible_sel_regs:
                            item = visible_sel_regs[register_select_index]
                            if "name" in item and item["name"] in register_select_checked:
                                rname = item["name"]
                                idx = register_select_checked.index(rname)
                                if idx < len(register_select_checked) - 1:
                                    register_select_checked[idx], register_select_checked[idx+1] = register_select_checked[idx+1], register_select_checked[idx]
                                    register_select_index += 1
                    elif key == " ":  # Space (Toggle Checkbox)
                        if visible_sel_regs:
                            item = visible_sel_regs[register_select_index]
                            if "name" in item:
                                rname = item["name"]
                                if rname in register_select_checked:
                                    register_select_checked.remove(rname)
                                else:
                                    register_select_checked.append(rname)
                    elif key in ("\x7f", "\x08"):  # Backspace
                        register_select_filter = register_select_filter[:-1]
                        register_select_index = 0
                    elif len(key) == 1 and key.isprintable() and key != " ":
                        register_select_filter += key
                        register_select_index = 0
                    elif key in ("\r", "\n"):  # Enter
                        device_form_fields["registers"] = ", ".join(register_select_checked)
                        active_modal = previous_device_modal
                    
                    # Ensure index is not on a header after filter change or open
                    if visible_sel_regs and "header" in visible_sel_regs[register_select_index % len(visible_sel_regs)]:
                        for _ in range(len(visible_sel_regs)):
                            register_select_index = (register_select_index + 1) % len(visible_sel_regs)
                            if "header" not in visible_sel_regs[register_select_index]:
                                break

                elif active_modal == "select_schema":
                    if key == "\x1b":  # Escape
                        active_modal = previous_device_modal
                    elif key in ("\x1b[A", "\x1bOA"):  # Up
                        schema_select_index = max(0, schema_select_index - 1)
                    elif key in ("\x1b[B", "\x1bOB"):  # Down
                        schema_select_index = min(len(schema_select_list) - 1, schema_select_index + 1)
                    elif key in ("\r", "\n"):  # Enter
                        if schema_select_list:
                            device_form_fields["schema_name"] = schema_select_list[schema_select_index]
                        active_modal = previous_device_modal

                elif active_modal in ("add_device", "edit_device"):
                    fields_list = ["name", "host", "port", "unit_id", "schema_name", "polling_interval", "registers"]
                    if key == "\x1b":  # Escape
                        active_modal = None
                        status_message = ""
                    elif key in ("\x1b[A", "\x1bOA"):  # Up (Arrow navigation ONLY)
                        device_form_cursor = (device_form_cursor - 1) % len(fields_list)
                    elif key in ("\x1b[B", "\x1bOB"):  # Down (Arrow navigation ONLY)
                        device_form_cursor = (device_form_cursor + 1) % len(fields_list)
                    elif key in ("\x7f", "\x08"):  # Backspace
                        field_name = fields_list[device_form_cursor]
                        device_form_fields[field_name] = device_form_fields[field_name][:-1]
                    elif key in ("\r", "\n"):
                        field_name = fields_list[device_form_cursor]
                        if field_name == "schema_name":
                            try:
                                from modbus_schema_common.registry import get_available_schemas
                                schema_select_list = get_available_schemas()
                                schema_select_index = 0
                                if device_form_fields["schema_name"] in schema_select_list:
                                    schema_select_index = schema_select_list.index(device_form_fields["schema_name"])
                                previous_device_modal = active_modal
                                active_modal = "select_schema"
                                status_message = ""
                            except Exception as esch:
                                status_message = f"[red]Error loading available schemas: {esch}[/red]"
                        elif field_name == "registers":
                            # Open dynamic registers list select sub-modal
                            try:
                                schema_name_val = device_form_fields["schema_name"].strip() or config.DEFAULT_MODBUS_SCHEMA
                                spec = resolve_schema(schema_name_val)
                                register_select_list = [{"name": r.name, "addr": r.address_dec} for r in spec.registers]
                                register_select_checked = []
                                cur_regs_str = device_form_fields["registers"].strip()
                                if cur_regs_str:
                                    register_select_checked = [r.strip() for r in cur_regs_str.split(",") if r.strip()]
                                register_select_index = 0
                                register_select_filter = ""
                                previous_device_modal = active_modal
                                active_modal = "select_registers"
                                status_message = ""
                            except Exception as esch:
                                status_message = f"[red]Error loading schema registers: {esch}[/red]"
                        else:
                            # Save/Confirm form
                            try:
                                name_val = device_form_fields["name"].strip() or None
                                host_val = device_form_fields["host"].strip()
                                if not host_val:
                                    raise ValueError("Host IP is required")
                                
                                try:
                                    port_val = int(device_form_fields["port"].strip())
                                except ValueError:
                                    raise ValueError("Port must be an integer")
                                
                                try:
                                    unit_id_val = int(device_form_fields["unit_id"].strip())
                                except ValueError:
                                    raise ValueError("Unit ID must be an integer")
                                
                                schema_name_val = device_form_fields["schema_name"].strip()
                                if not schema_name_val:
                                    raise ValueError("Schema name is required")
                                
                                try:
                                    poll_int_val = float(device_form_fields["polling_interval"].strip())
                                except ValueError:
                                    raise ValueError("Polling interval must be a number")
                                
                                # Validate schema
                                try:
                                    resolve_schema(schema_name_val)
                                except Exception as esch:
                                    raise ValueError(f"Invalid schema '{schema_name_val}': {esch}")
                                
                                registers_val = device_form_fields["registers"].strip()
                                if registers_val:
                                    regs_list = [r.strip() for r in registers_val.split(",") if r.strip()]
                                else:
                                    regs_list = []

                                temp_dev = DeviceConfig(
                                    name=name_val,
                                    host=host_val,
                                    port=port_val,
                                    unit_id=unit_id_val,
                                    schema_name=schema_name_val,
                                    polling_interval=poll_int_val,
                                    registers=regs_list,
                                )
                                
                                # Check name uniqueness
                                for d_idx, dev_item in enumerate(devices):
                                    if active_modal == "edit_device" and d_idx == selected_device_index:
                                        continue
                                    if dev_item.name == temp_dev.name:
                                        raise ValueError(f"Device name '{temp_dev.name}' already exists")
                                
                                # Load current app config to modify and save
                                app_config = AppConfig.load_from_yaml(config_path)
                                if active_modal == "add_device":
                                    app_config.devices.append(temp_dev)
                                    app_config.save_to_yaml(config_path)
                                    devices = app_config.devices
                                    select_device(len(devices) - 1)
                                    status_message = f"[green]Added device '{temp_dev.name}'[/green]"
                                else:  # edit_device
                                    app_config.devices[selected_device_index] = temp_dev
                                    app_config.save_to_yaml(config_path)
                                    devices = app_config.devices
                                    select_device(selected_device_index)
                                    status_message = f"[green]Updated device '{temp_dev.name}'[/green]"
                                
                                active_modal = None
                            except Exception as ex:
                                status_message = f"[red]Validation Error: {ex}[/red]"
                    elif len(key) == 1 and key.isprintable():
                        field_name = fields_list[device_form_cursor]
                        device_form_fields[field_name] += key

                elif active_modal == "write":
                    if key == "\x1b":  # Escape
                        active_modal = None
                        status_message = ""
                    elif key in ("\x7f", "\x08"):  # Backspace
                        write_value_buffer = write_value_buffer[:-1]
                    elif key in ("\r", "\n") and filtered_regs:
                        selected_reg = filtered_regs[selected_index]
                        try:
                            parsed_val = parse_ordinal_value(write_value_buffer)
                            original_val = raw_results.get(selected_reg.name)
                            # Only unstage if original is a real (non-sentinel) value and matches
                            if (not is_holding_register_sentinel(original_val, selected_reg.data_type)
                                    and parsed_val == original_val):
                                staged_changes.pop(selected_reg.name, None)
                                status_message = f"[grey]Cleared staged change for {selected_reg.name}[/grey]"
                            else:
                                staged_changes[selected_reg.name] = parsed_val
                                status_message = f"[yellow]Staged: {selected_reg.name} \u2192 {parsed_val}[/yellow]"
                        except Exception as ex:
                            status_message = f"[red]Error: {ex}[/red]"
                        finally:
                            active_modal = None
                    elif len(key) == 1 and key.isprintable():
                        write_value_buffer += key

                elif active_modal == "write_enum" and filtered_regs:
                    if key == "\x1b":  # Escape
                        active_modal = None
                        write_enum_selected = None
                        status_message = ""
                    elif key in ("\x1b[A", "\x1bOA"):  # Up
                        write_enum_index = max(0, write_enum_index - 1)
                    elif key in ("\x1b[B", "\x1bOB"):  # Down
                        write_enum_index = min(
                            len(write_enum_options) - 1, write_enum_index + 1
                        )
                    elif key == " " and write_enum_options:  # Space: toggle selection
                        safe_idx = write_enum_index % len(write_enum_options)
                        code, _ = write_enum_options[safe_idx]
                        # Toggle: select this code, deselect if already selected
                        write_enum_selected = code if write_enum_selected != code else None
                    elif key in ("\r", "\n") and write_enum_options:  # Enter: confirm
                        selected_reg = filtered_regs[selected_index]
                        # If nothing toggled via Space, use the highlighted row
                        safe_idx = write_enum_index % len(write_enum_options)
                        chosen_code = (
                            write_enum_selected
                            if write_enum_selected is not None
                            else write_enum_options[safe_idx][0]
                        )
                        chosen_label = next(
                            (lbl for c, lbl in write_enum_options if c == chosen_code),
                            str(chosen_code),
                        )
                        original_val = raw_results.get(selected_reg.name)
                        if (
                            not is_holding_register_sentinel(
                                original_val, selected_reg.data_type
                            )
                            and chosen_code == int(original_val)
                        ):
                            staged_changes.pop(selected_reg.name, None)
                            status_message = (
                                f"[grey]Cleared staged change for "
                                f"{selected_reg.name}[/grey]"
                            )
                        else:
                            staged_changes[selected_reg.name] = chosen_code
                            status_message = (
                                f"[yellow]Staged: {selected_reg.name} → "
                                f"{chosen_label} ({chosen_code})[/yellow]"
                            )
                        write_enum_selected = None
                        active_modal = None

                else:
                    # Focus Switching
                    if key in ("\x1b[D", "\x1bOD") and show_sidebar:  # Left Arrow
                        focused_panel = "devices"
                    elif key in ("\x1b[C", "\x1bOC") and show_sidebar:  # Right Arrow
                        focused_panel = "registers"
                    elif key == "\t" and show_sidebar:  # Tab
                        focused_panel = "registers" if focused_panel == "devices" else "devices"

                    # Add Device
                    elif key == "n" and show_sidebar:
                        active_modal = "add_device"
                        device_form_fields = {"name": "", "host": "", "port": "502", "unit_id": "1", "schema_name": config.DEFAULT_MODBUS_SCHEMA, "polling_interval": "1.0", "registers": ""}
                        device_form_cursor = 0
                        status_message = ""
                    
                    # Edit Device
                    elif key == "e" and show_sidebar:
                        if devices:
                            active_modal = "edit_device"
                            device_form_cursor = 0
                            cur_dev = devices[selected_device_index]
                            device_form_fields = {
                                "name": cur_dev.name or "",
                                "host": cur_dev.host or "",
                                "port": str(cur_dev.port),
                                "unit_id": str(cur_dev.unit_id),
                                "schema_name": cur_dev.schema_name or config.DEFAULT_MODBUS_SCHEMA,
                                "polling_interval": str(cur_dev.polling_interval),
                                "registers": ", ".join(cur_dev.registers) if cur_dev.registers else "",
                            }
                            status_message = ""
                        else:
                            status_message = "[red]No device to edit[/red]"

                    # Delete Device
                    elif key in ("d", "-") and show_sidebar and focused_panel == "devices":
                        if devices:
                            try:
                                app_config = AppConfig.load_from_yaml(config_path)
                                removed_dev = app_config.devices.pop(selected_device_index)
                                app_config.save_to_yaml(config_path)
                                devices = app_config.devices
                                status_message = f"[green]Deleted device '{removed_dev.name}'[/green]"
                                
                                new_idx = max(0, min(selected_device_index, len(devices) - 1))
                                select_device(new_idx)
                                if not devices:
                                    focused_panel = "devices"
                            except Exception as ex:
                                status_message = f"[red]Delete Error: {ex}[/red]"
                        else:
                            status_message = "[red]No device to delete[/red]"

                    # Help Modal Trigger
                    elif key in ("h", "?"):
                        active_modal = "help"
                        status_message = ""

                    # General commands
                    elif key == "f":
                        filter_focused = True
                        status_message = ""
                    elif key == " ":
                        paused = not paused
                        status_message = ""
                    elif key == "r":
                        # Trigger immediate poll by restarting task
                        start_polling()
                        status_message = "[cyan]Refreshing...[/cyan]"
                    elif key == "a":
                        # Apply staged changes (Batch Write)
                        if staged_changes and engine:
                            status_message = "[cyan]Applying staged changes...[/cyan]"
                            # Re-render immediately to show applying status
                            update_tui(filtered_regs)
                            with console_output.capture() as capture:
                                console_output.print(layout, end="")
                            tui_string = capture.get().replace("\r\n", "\n").replace("\n", "\r\n")
                            sys.stdout.write("\x1b[H" + tui_string)
                            sys.stdout.flush()
                            
                            try:
                                res = await engine.write_registers(staged_changes)
                                failures = [name for name, status in res.items() if status != "Success"]
                                if not failures:
                                    status_message = f"[bold green]Applied {len(staged_changes)} changes successfully![/bold green]"
                                    staged_changes.clear()
                                else:
                                    status_message = f"[bold red]Failed: {', '.join(failures)}[/bold red]"
                                # Trigger background poll restart to fetch updated data instantly
                                start_polling()
                            except Exception as ex:
                                status_message = f"[bold red]Error: {ex}[/bold red]"
                            finally:
                                try:
                                    await engine.client.close()
                                except Exception:
                                    pass
                                engine.client.client = None
                        else:
                            status_message = "[yellow]No pending changes to apply[/yellow]"
                    elif key == "c":
                        # Discard/Clear staged changes
                        if staged_changes:
                            staged_changes.clear()
                            status_message = "[grey]Staged changes discarded[/grey]"
                        else:
                            status_message = "[grey]No staged changes to discard[/grey]"

                    # Up Navigation
                    elif key in ("\x1b[A", "\x1bOA"):
                        if focused_panel == "devices" and devices:
                            new_idx = max(0, selected_device_index - 1)
                            select_device(new_idx)
                        elif focused_panel == "registers" and filtered_regs:
                            selected_index = max(0, selected_index - 1)
                        status_message = ""

                    # Down Navigation
                    elif key in ("\x1b[B", "\x1bOB"):
                        if focused_panel == "devices" and devices:
                            new_idx = min(len(devices) - 1, selected_device_index + 1)
                            select_device(new_idx)
                        elif focused_panel == "registers" and filtered_regs:
                            selected_index = min(len(filtered_regs) - 1, selected_index + 1)
                        status_message = ""

                    # Enter Action (Write modal or Coil toggling)
                    elif key in ("\r", "\n") and focused_panel == "registers" and filtered_regs:
                        selected_reg = filtered_regs[selected_index]
                        if selected_reg.access not in ("RW", "WO"):
                            status_message = f"[yellow]Register {selected_reg.name} is read-only[/yellow]"
                        elif selected_reg.register_type in (ModbusRegisterType.COIL, "coil", ModbusRegisterType.DISCRETE_INPUT, "discrete_input"):
                            if selected_reg.access == "WO":
                                # Trigger Action for WO Coil (Immediate, no staging)
                                if engine:
                                    try:
                                        res = await engine.write_registers({selected_reg.name: True})
                                        status = res.get(selected_reg.name)
                                        if status == "Success":
                                            status_message = f"[green]Triggered {selected_reg.name} successfully[/green]"
                                            start_polling()
                                        else:
                                            status_message = f"[red]Trigger failed: {status}[/red]"
                                    except Exception as ex:
                                        status_message = f"[red]Error: {ex}[/red]"
                                    finally:
                                        try:
                                            await engine.client.close()
                                        except Exception:
                                            pass
                                        engine.client.client = None
                            else:
                                # Toggle Coil (Immediate, no staging)
                                current_val = raw_results.get(selected_reg.name)
                                if current_val is not None and engine:
                                    new_val = not bool(current_val)
                                    try:
                                        res = await engine.write_registers({selected_reg.name: new_val})
                                        status = res.get(selected_reg.name)
                                        if status == "Success":
                                            status_message = f"[green]Toggled {selected_reg.name} to {new_val}[/green]"
                                            start_polling()
                                        else:
                                            status_message = f"[red]Toggle failed: {status}[/red]"
                                    except Exception as ex:
                                        status_message = f"[red]Error: {ex}[/red]"
                                    finally:
                                        try:
                                            await engine.client.close()
                                        except Exception:
                                            pass
                                        engine.client.client = None
                                else:
                                    status_message = "[red]Error: Could not read coil to toggle[/red]"
                        else:
                            # Open Edit Modal for Holding Registers (will be staged on confirm)
                            cur_val = raw_results.get(selected_reg.name)
                            if selected_reg.enum_values:
                                # Enum register: open picker modal
                                write_enum_options = sorted(
                                    [(int(k), v) for k, v in selected_reg.enum_values.items()],
                                    key=lambda x: x[0]
                                )
                                write_enum_index = 0
                                write_enum_selected = None
                                if not is_holding_register_sentinel(
                                    cur_val, selected_reg.data_type
                                ):
                                    try:
                                        cur_code = int(cur_val)
                                        for _ei, (_ec, _) in enumerate(write_enum_options):
                                            if _ec == cur_code:
                                                write_enum_index = _ei
                                                write_enum_selected = cur_code
                                                break
                                    except (TypeError, ValueError):
                                        pass
                                active_modal = "write_enum"
                            else:
                                # Numeric/text register: open free-text modal
                                write_value_buffer = ""
                                if not is_holding_register_sentinel(
                                    cur_val, selected_reg.data_type
                                ):
                                    write_value_buffer = str(cur_val)
                                active_modal = "write"
                            write_enum_selected = None
                            status_message = ""

            # 4. Check if clock time changed
            now_str = datetime.now(tz).strftime(time_format)
            current_epoch_sec = int(time.time())
            time_changed = (current_epoch_sec != last_epoch_sec)

            # Check if console size changed
            current_size = console_output.size
            size_changed = (current_size != last_size)
            if size_changed:
                last_size = current_size
                # Clear entire screen to clean up any old rendering residues
                sys.stdout.write("\x1b[2J")
                sys.stdout.flush()

            # 5. Update view only if something changed (polling, clock tick, key press, size change, or first run)
            if not last_time_str or poll_updated or time_changed or key_pressed or size_changed:
                poll_updated = False
                last_epoch_sec = current_epoch_sec
                last_time_str = now_str
                update_tui(filtered_regs)
                with console_output.capture() as capture:
                    console_output.print(layout, end="")
                tui_string = capture.get()
                
                # Strip trailing line ending to prevent scrolling when the table fits perfectly
                if tui_string.endswith("\r\n"):
                    tui_string = tui_string[:-2]
                elif tui_string.endswith("\n"):
                    tui_string = tui_string[:-1]
                
                # Normalize line endings to \r\n to prevent staircase shifting in raw terminal mode
                tui_string = tui_string.replace("\r\n", "\n").replace("\n", "\r\n")
                
                # Overwrite terminal screen starting from top-left
                sys.stdout.write("\x1b[H" + tui_string)
                sys.stdout.flush()

            # Yield control to the event loop to let background tasks run
            await asyncio.sleep(0.05)

    # Put terminal into raw mode and restore it cleanly on exit
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        # Enter alternate screen buffer manually to support fullscreen without flicker
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.flush()
        # Clear terminal once at startup
        console_output.clear()
        with RawTerminal():
            # Hide cursor
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
            
            asyncio.run(main_loop())
    finally:
        # Cancel background polling task
        if poll_task:
            poll_task.cancel()
        # Restore alternate screen buffer
        sys.stdout.write("\x1b[?1049l")
        # Restore terminal settings
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        # Show cursor
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        # Restore original log levels
        logging.getLogger().setLevel(original_log_level)
        logging.getLogger("modbus_ctrl_core").setLevel(original_core_level)
