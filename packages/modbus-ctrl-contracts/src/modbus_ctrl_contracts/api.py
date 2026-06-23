from __future__ import annotations
from pydantic import BaseModel, Field

class WriteBatchRequest(BaseModel):
    writes: dict[str, float | int | bool] = Field(..., description="Mapping of register names or address strings to values to write")

class TelemetryDeltaResponse(BaseModel):
    device_name: str = Field(..., description="Name of the reporting device")
    timestamp: float = Field(..., description="Epoch timestamp of the report")
    deltas: dict[str, float | int | bool | str | None] = Field(..., description="Key-value mapping of updated register values")
