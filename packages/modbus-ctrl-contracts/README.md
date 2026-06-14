# Modbus Control Contracts

The `modbus-ctrl-contracts` package maintains the shared data transfer objects (DTOs) and configuration contracts across the Modbus Control suite.

## Purpose

- Defines models like `AppConfig` and `DeviceConfig` for the YAML configuration.
- Ensures a unified standard for defining connection parameters (Host, Port, Unit ID).
- Prevents circular dependencies between the CLI, the Center, and the Core modules.
