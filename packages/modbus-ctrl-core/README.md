# Modbus Control Core

The `modbus-ctrl-core` package encapsulates the primary engine for communicating with Modbus devices. It handles network communication, block read aggregation, and schema parsing.

## Purpose

- Establishes persistent connections using `pymodbus`.
- Translates raw binary data into strongly-typed Python values based on the schema.
- Provides `ModbusControlEngine` as a clean, asynchronous API for other modules.
