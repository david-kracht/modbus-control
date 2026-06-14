import yaml
from pathlib import Path
from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator, model_serializer, ValidationError

class DeviceConfig(BaseModel):
    name: Optional[str] = Field(None, description="Unique name of the Modbus device")
    host: str = Field(..., description="IP address or host name")
    port: int = Field(502, description="Modbus TCP port")
    unit_id: int = Field(1, description="Modbus Slave Unit ID")
    schema_name: str = Field(default="modbus_config/latest", description="Schema version name")
    polling_interval: float = Field(1.0, description="Polling interval in seconds")
    active: bool = Field(True, description="Whether polling is active for this device")
    registers: Optional[list[str]] = Field(None, description="Optional list of registers to query and display, in specific order")

    @model_validator(mode="after")
    def set_default_name(self) -> "DeviceConfig":
        if not self.name:
            self.name = f"{self.host}_{self.port}"
        return self

    @model_validator(mode="after")
    def validate_schema(self) -> "DeviceConfig":
        """
        Validate that the specified schema can be resolved to a valid
        ModbusInterfaceSpecification. We don't store the spec in the model
        to keep the config lightweight, but we fail early if it's invalid.
        """
        if self.schema_name:
            try:
                from modbus_schema_common.resolver import resolve_schema
                spec = resolve_schema(self.schema_name)
            except Exception as e:
                raise ValueError(f"Invalid schema '{self.schema_name}': {e}")
        return self

    @model_validator(mode="after")
    def validate_registers(self) -> "DeviceConfig":
        if self.registers:
            self.registers = [r.strip() for r in self.registers if r.strip()]
            
            # 1. Uniqueness check
            seen = set()
            duplicates = []
            for r in self.registers:
                if r in seen:
                    if r not in duplicates:
                        duplicates.append(r)
                else:
                    seen.add(r)
            if duplicates:
                raise ValueError(
                    f"Duplicate register names are not allowed in the config: {', '.join(duplicates)}"
                )

            # 2. Schema check
            try:
                from modbus_schema_common.resolver import resolve_schema
                spec = resolve_schema(self.schema_name)
                valid_names = {reg.name for reg in spec.registers}
                invalid = [r for r in self.registers if r not in valid_names]
                if invalid:
                    raise ValueError(
                        f"The following registers specified in 'registers' config are not defined in schema '{self.schema_name}': {', '.join(invalid)}"
                    )
            except ImportError:
                pass
            except Exception as e:
                if isinstance(e, ValueError):
                    raise
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
        if self.schema_name == "modbus_config/latest":
            data.pop("schema_name", None)
        if not self.registers:
            data.pop("registers", None)
        return data

class AppConfig(BaseModel):
    devices: list[DeviceConfig] = Field(default_factory=list)

    @classmethod
    def load_from_yaml(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls(devices=[])
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            messages = []
            for error in e.errors():
                loc_str = " -> ".join(str(x) for x in error["loc"])
                msg = error["msg"]
                messages.append(f"  - Config error at '{loc_str}': {msg}")
            err_msg = (
                f"Invalid configuration in '{path.name}'. Please correct the file manually:\n"
                + "\n".join(messages)
            )
            raise ValueError(err_msg) from e

    def save_to_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        devices_list = [d.to_compact_dict() for d in self.devices]
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"devices": devices_list}, f, sort_keys=False)
