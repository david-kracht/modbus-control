import yaml
from pathlib import Path
from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator, model_serializer

def get_latest_schema() -> str:
    try:
        from modbus_schema_common.registry import get_registry
        registry = get_registry("modbus_config")
        vers = registry.versions()
        if vers:
            return vers[-1]
    except Exception:
        pass
    return "v10"

class DeviceConfig(BaseModel):
    name: Optional[str] = Field(None, description="Unique name of the Modbus device")
    host: str = Field(..., description="IP address or host name")
    port: int = Field(502, description="Modbus TCP port")
    unit_id: int = Field(1, description="Modbus Slave Unit ID")
    schema_name: str = Field(default_factory=get_latest_schema, description="Schema version name")
    polling_interval: float = Field(1.0, description="Polling interval in seconds")
    active: bool = Field(True, description="Whether polling is active for this device")

    @model_validator(mode="after")
    def set_default_name(self) -> "DeviceConfig":
        if not self.name:
            self.name = f"{self.host}_{self.port}"
        return self

    def to_compact_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        # Exclude defaults to keep the YAML file compact
        if self.name == f"{self.host}_{self.port}":
            data.pop("name", None)
        if self.port == 502:
            data.pop("port", None)
        if self.unit_id == 1:
            data.pop("unit_id", None)
        if self.polling_interval == 1.0:
            data.pop("polling_interval", None)
        if self.active is True:
            data.pop("active", None)
        if self.schema_name == get_latest_schema():
            data.pop("schema_name", None)
        return data

class AppConfig(BaseModel):
    devices: list[DeviceConfig] = Field(default_factory=list)

    @classmethod
    def load_from_yaml(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls(devices=[])
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    def save_to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        devices_list = [d.to_compact_dict() for d in self.devices]
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"devices": devices_list}, f, sort_keys=False)
