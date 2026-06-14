import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Path to the shared devices YAML file
MODBUS_DEVICES_YAML: Path = Path(os.getenv("MODBUS_DEVICES_YAML", "devices.yaml")).resolve()

# Control Center Backend Settings
CTRL_CENTER_HOST: str = os.getenv("CTRL_CENTER_HOST", "0.0.0.0")
CTRL_CENTER_PORT: int = int(os.getenv("CTRL_CENTER_PORT", "8080"))

# Default Ad-Hoc / Anonymous Modbus Connection Defaults
DEFAULT_MODBUS_HOST: str | None = os.getenv("DEFAULT_MODBUS_HOST", None)
DEFAULT_MODBUS_PORT: int = int(os.getenv("DEFAULT_MODBUS_PORT", "502"))
DEFAULT_MODBUS_UNIT_ID: int = int(os.getenv("DEFAULT_MODBUS_UNIT_ID", "1"))
DEFAULT_MODBUS_SCHEMA: str = os.getenv("DEFAULT_MODBUS_SCHEMA", "modbus_config/latest")

# Polling configuration
DEFAULT_POLLING_INTERVAL: float = float(os.getenv("DEFAULT_POLLING_INTERVAL", "1.0"))

# Customization Settings
SUITE_TITLE: str = os.getenv("SUITE_TITLE", "Modbus Control Suite")
SUITE_LOGO_PATH: str = os.getenv("SUITE_LOGO_PATH", "")
