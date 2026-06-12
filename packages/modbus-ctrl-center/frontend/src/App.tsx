import React, { useState, useEffect, useRef } from "react";
import { 
  Cpu, Layers, Settings, Play, Trash, Plus, 
  RefreshCw, Wifi, WifiOff, Database, Save, AlertTriangle, Info, Edit 
} from "lucide-react";

interface Device {
  name: string;
  host: string;
  port: number;
  unit_id: number;
  schema_name: string;
  polling_interval: number;
  active: boolean;
}

interface Register {
  name: string;
  address_dec: number;
  address_hex: string;
  description: string;
  data_type: string;
  register_count: number;
  access: string;
  register_type: string;
  unit: string | null;
  enum_values: Record<string, string> | null;
}

interface Schema {
  device_name: string;
  version: string;
  firmware: string | null;
  source_url: string | null;
  registers: Register[];
}

export default function App() {
  // Device list and active device selection
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDeviceName, setSelectedDeviceName] = useState<string>("");
  const [schema, setSchema] = useState<Schema | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [stagedChanges, setStagedChanges] = useState<Record<string, any>>({});
  
  // Connection states
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [loadingSchema, setLoadingSchema] = useState<boolean>(false);
  const [writingStatus, setWritingStatus] = useState<string | null>(null);

  // Form and Edit state
  const [formMode, setFormMode] = useState<'add' | 'edit' | null>(null);
  const [editingDeviceOriginalName, setEditingDeviceOriginalName] = useState<string>("");
  const [formName, setFormName] = useState("");
  const [formHost, setFormHost] = useState("");
  const [formPort, setFormPort] = useState<number | "">("");
  const [formUnitId, setFormUnitId] = useState<number | "">("");
  const [formSchema, setFormSchema] = useState("");
  const [formInterval, setFormInterval] = useState<number | "">("");
  const [formActive, setFormActive] = useState<boolean>(true);

  const handleStartAdd = () => {
    setFormMode("add");
    setEditingDeviceOriginalName("");
    setFormName("");
    setFormHost("");
    setFormPort("");
    setFormUnitId("");
    setFormSchema("");
    setFormInterval("");
    setFormActive(true);
  };

  const handleStartEdit = (dev: Device) => {
    setFormMode("edit");
    setEditingDeviceOriginalName(dev.name);
    setFormName(dev.name);
    setFormHost(dev.host);
    setFormPort(dev.port);
    setFormUnitId(dev.unit_id);
    setFormSchema(dev.schema_name);
    setFormInterval(dev.polling_interval);
    setFormActive(dev.active);
  };

  const wsRef = useRef<WebSocket | null>(null);

  // 1. Fetch devices on mount
  useEffect(() => {
    fetchDevices();
  }, []);

  const fetchDevices = async () => {
    try {
      const res = await fetch("/api/devices");
      const data = await res.json();
      setDevices(data);
      if (data.length > 0 && !selectedDeviceName) {
        setSelectedDeviceName(data[0].name);
      }
    } catch (e) {
      console.error("Error fetching devices:", e);
    }
  };

  // 2. Fetch schema and cached values when device selection changes
  useEffect(() => {
    if (!selectedDeviceName) {
      setSchema(null);
      setValues({});
      setStagedChanges({});
      return;
    }

    setSchema(null);
    setValues({});
    setStagedChanges({});
    fetchSchemaAndValues(selectedDeviceName);
  }, [selectedDeviceName]);

  const fetchSchemaAndValues = async (name: string) => {
    setLoadingSchema(true);
    try {
      // Fetch schema
      const schemaRes = await fetch(`/api/devices/${name}/schema`);
      if (schemaRes.ok) {
        const schemaData = await schemaRes.json();
        setSchema(schemaData);
      } else {
        console.error("Failed to load schema");
      }

      // Fetch current values
      const valRes = await fetch(`/api/devices/${name}/values`);
      if (valRes.ok) {
        const valData = await valRes.json();
        setValues(valData);
      }
    } catch (e) {
      console.error("Error loading device details:", e);
    } finally {
      setLoadingSchema(false);
    }
  };

  // 3. Manage WebSocket connection for live deltas
  useEffect(() => {
    const loc = window.location;
    const wsProto = loc.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProto}//${loc.host}/ws/telemetry`;

    logger("Connecting to WebSocket: " + wsUrl);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      logger("WebSocket connection established");
      setWsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.device_name === selectedDeviceName) {
          logger("Delta telemetry received: " + JSON.stringify(data.deltas));
          setValues((prev) => ({
            ...prev,
            ...data.deltas,
          }));
        }
      } catch (e) {
        console.error("Error parsing WS message:", e);
      }
    };

    ws.onclose = () => {
      logger("WebSocket disconnected");
      setWsConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [selectedDeviceName]);

  const logger = (msg: string) => {
    console.log(`[Modbus Control] ${msg}`);
  };

  // 4. Handle staging changes for RW holding registers
  const handleStageChange = (regName: string, value: any, originalVal: any) => {
    // If it's an enum, always send the numeric ordinal
    let parsedVal: any = value;
    if (typeof value === "string") {
      // Normalise decimal separator: replace comma with dot (German locale safety)
      const normalised = value.replace(",", ".");
      if (normalised !== "" && !isNaN(Number(normalised))) {
        parsedVal = Number(normalised);
      }
    } else if (typeof value === "number") {
      parsedVal = value;
    }

    if (parsedVal === originalVal) {
      const nextStaged = { ...stagedChanges };
      delete nextStaged[regName];
      setStagedChanges(nextStaged);
    } else {
      setStagedChanges((prev) => ({
        ...prev,
        ...{ [regName]: parsedVal }
      }));
    }
  };

  // 5. Submit single Write Only (WO) Coil trigger action
  const triggerCoilAction = async (regName: string) => {
    setWritingStatus(`Triggering action: ${regName}...`);
    try {
      const res = await fetch(`/api/devices/${selectedDeviceName}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ writes: { [regName]: true } })
      });
      if (res.ok) {
        const results = await res.json();
        if (results[regName] === "Success") {
          setWritingStatus(`Success: ${regName} triggered!`);
        } else {
          setWritingStatus(`Failed: ${results[regName]}`);
        }
      } else {
        setWritingStatus("Failed to send action POST");
      }
    } catch (e: any) {
      setWritingStatus(`Error: ${e.message}`);
    }
    setTimeout(() => setWritingStatus(null), 3000);
  };

  // 6. Apply all staged changes (RW Holding Registers)
  const applyStagedChanges = async () => {
    if (Object.keys(stagedChanges).length === 0) return;
    setWritingStatus("Applying staged changes...");
    try {
      const res = await fetch(`/api/devices/${selectedDeviceName}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ writes: stagedChanges })
      });
      if (res.ok) {
        const results = await res.json();
        const failures = Object.entries(results).filter(([_, status]) => status !== "Success");
        if (failures.length === 0) {
          setWritingStatus("Success: All changes applied successfully!");
          setStagedChanges({});
        } else {
          const failMsg = failures.map(([name, status]) => `${name}: ${status}`).join(", ");
          setWritingStatus(`Errors occurred: ${failMsg}`);
        }
      } else {
        setWritingStatus("Failed to apply staged changes");
      }
    } catch (e: any) {
      setWritingStatus(`Error: ${e.message}`);
    }
    setTimeout(() => setWritingStatus(null), 4000);
  };

  // 7. Device form submit (Add or Edit)
  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload: any = {
      host: formHost,
      active: formActive,
    };
    if (formName.trim() !== "") {
      payload.name = formName;
    }
    if (formPort !== "") {
      payload.port = Number(formPort);
    }
    if (formUnitId !== "") {
      payload.unit_id = Number(formUnitId);
    }
    if (formSchema.trim() !== "") {
      payload.schema_name = formSchema;
    }
    if (formInterval !== "") {
      payload.polling_interval = Number(formInterval);
    }

    try {
      const url = formMode === "add" ? "/api/devices" : `/api/devices/${editingDeviceOriginalName}`;
      const method = formMode === "add" ? "POST" : "PUT";
      
      const res = await fetch(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        const data = await res.json();
        const resolvedName = payload.name || (data.device ? data.device.name : `${formHost}_${payload.port || 502}`);
        fetchDevices();
        setFormMode(null);
        setSelectedDeviceName(resolvedName);
      } else {
        const err = await res.json();
        alert(`Error: ${err.detail || "Failed to save device"}`);
      }
    } catch (err: any) {
      alert(`Error: ${err.message}`);
    }
  };

  // 8. Delete device
  const handleDeleteDevice = async (name: string) => {
    if (!confirm(`Are you sure you want to delete device '${name}'?`)) return;
    try {
      const res = await fetch(`/api/devices/${name}`, { method: "DELETE" });
      if (res.ok) {
        fetchDevices();
        if (selectedDeviceName === name) {
          setSelectedDeviceName("");
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Separate registers by access
  const readOnlyRegisters = schema?.registers.filter(
    (r) => r.register_type === "discrete_input" || r.register_type === "input_register"
  ) || [];

  const writeOnlyRegisters = schema?.registers.filter(
    (r) => r.register_type === "coil" && r.access === "WO"
  ) || [];

  const readWriteRegisters = schema?.registers.filter(
    (r) => r.register_type === "holding_register"
  ) || [];

  // Format a value for display: float32 always shows 2 decimal places with a dot
  const formatDisplayVal = (val: any, dataType: string): string => {
    if (val === undefined || val === null) return "";
    if (dataType === "float32" && typeof val === "number") {
      return val.toFixed(2);
    }
    return String(val);
  };

  // Format value for the read-only telemetry tile
  const formatTileVal = (val: any, dataType: string, enumValues: Record<string, string> | null): string => {
    if (typeof val === "boolean") return val ? "ON" : "OFF";
    if (enumValues && val !== undefined && val !== null) {
      const label = enumValues[String(val)];
      return label ? `${label} (${val})` : String(val);
    }
    if (dataType === "float32" && typeof val === "number") {
      return Number.isInteger(val) ? val.toFixed(1) : val.toFixed(2);
    }
    return String(val);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col antialiased">
      {/* Header bar */}
      <header className="border-b border-slate-900 bg-slate-900/40 backdrop-blur-md sticky top-0 z-40 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="bg-gradient-to-tr from-amber-500 to-orange-600 p-2 rounded-lg text-white shadow-lg shadow-orange-500/20">
            <Cpu className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white">Modbus Control Center</h1>
            <p className="text-xs text-slate-400">Schema-Driven Dynamic Automation</p>
          </div>
        </div>

        {/* Live connection state */}
        <div className="flex items-center space-x-4">
          <div className={`flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold ${wsConnected ? "bg-emerald-950/50 text-emerald-400 border border-emerald-800/60" : "bg-red-950/50 text-red-400 border border-red-800/60"}`}>
            {wsConnected ? (
              <>
                <Wifi className="h-3.5 w-3.5 animate-pulse" />
                <span>WS Connected</span>
              </>
            ) : (
              <>
                <WifiOff className="h-3.5 w-3.5" />
                <span>WS Offline</span>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main container */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0">
        
        {/* Sidebar (Devices lists) */}
        <aside className="w-full md:w-80 border-r border-slate-900 bg-slate-950/50 p-6 flex flex-col gap-6 shrink-0">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-2">
              <Database className="h-4 w-4" />
              Devices
            </h2>
            <button 
              onClick={() => formMode === "add" ? setFormMode(null) : handleStartAdd()}
              className="p-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 transition-colors"
              title="Add new device"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>

          {/* Device Add/Edit Form */}
          {formMode !== null && (
            <form onSubmit={handleFormSubmit} className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 flex flex-col gap-3">
              <h3 className="text-xs font-bold text-slate-300">
                {formMode === "add" ? "New Modbus Device" : "Edit Modbus Device"}
              </h3>
              <div>
                <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Name</label>
                <input 
                  type="text" placeholder="Auto-generated if empty"
                  value={formName} onChange={(e) => setFormName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                />
              </div>
              <div>
                <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Host IP / Address *</label>
                <input 
                  type="text" placeholder="e.g. 192.168.1.15" required
                  value={formHost} onChange={(e) => setFormHost(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Port</label>
                  <input 
                    type="number" placeholder="502"
                    value={formPort} onChange={(e) => setFormPort(e.target.value === "" ? "" : Number(e.target.value))}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                  />
                </div>
                <div>
                  <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Unit ID</label>
                  <input 
                    type="number" placeholder="1"
                    value={formUnitId} onChange={(e) => setFormUnitId(e.target.value === "" ? "" : Number(e.target.value))}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                  />
                </div>
              </div>
              <div>
                <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Schema Name</label>
                <input 
                  type="text" placeholder="Defaults to latest (v30)"
                  value={formSchema} onChange={(e) => setFormSchema(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                />
              </div>
              <div>
                <label className="text-[10px] uppercase font-bold tracking-wider text-slate-500 block mb-1">Poll Interval (s)</label>
                <input 
                  type="number" step="0.1" placeholder="1.0"
                  value={formInterval} onChange={(e) => setFormInterval(e.target.value === "" ? "" : Number(e.target.value))}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
                />
              </div>
              <div className="flex items-center gap-2 py-1">
                <input 
                  type="checkbox" id="formActive"
                  checked={formActive} onChange={(e) => setFormActive(e.target.checked)}
                  className="rounded bg-slate-950 border-slate-800 text-amber-500 focus:ring-amber-500"
                />
                <label htmlFor="formActive" className="text-xs text-slate-350 select-none">Active / Poll enabled</label>
              </div>
              <div className="flex gap-2 mt-1">
                <button 
                  type="button" onClick={() => setFormMode(null)}
                  className="flex-1 py-2 bg-slate-900 hover:bg-slate-850 border border-slate-800 text-slate-300 font-medium rounded-lg text-sm transition-all"
                >
                  Cancel
                </button>
                <button 
                  type="submit"
                  className="flex-1 py-2 bg-gradient-to-r from-amber-500 to-orange-600 hover:from-amber-600 hover:to-orange-700 text-white font-medium rounded-lg text-sm transition-all shadow-md shadow-orange-500/10"
                >
                  {formMode === "add" ? "Add Device" : "Save"}
                </button>
              </div>
            </form>
          )}

          {/* List of configured devices */}
          <div className="flex flex-col gap-2">
            {devices.map((dev) => (
              <div 
                key={dev.name}
                onClick={() => setSelectedDeviceName(dev.name)}
                className={`group border rounded-xl p-4 flex items-center justify-between cursor-pointer transition-all ${selectedDeviceName === dev.name ? "bg-amber-500/10 border-amber-500 text-white" : "bg-slate-900/20 border-slate-800 text-slate-300 hover:bg-slate-900/50 hover:border-slate-700"}`}
              >
                <div className="flex items-center space-x-3">
                  <div className={`p-2 rounded-lg ${selectedDeviceName === dev.name ? "bg-amber-500 text-white" : "bg-slate-900 text-slate-400 group-hover:text-slate-200"}`}>
                    <Cpu className="h-4 w-4" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-sm leading-tight">{dev.name}</h3>
                    <p className="text-xs text-slate-400">{dev.host}:{dev.port}</p>
                  </div>
                </div>
                <div className="flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button 
                    onClick={(e) => {
                      e.stopPropagation();
                      handleStartEdit(dev);
                    }}
                    className="p-1 rounded hover:bg-slate-800 hover:text-white text-slate-400 transition-all"
                    title="Edit device"
                  >
                    <Edit className="h-4 w-4" />
                  </button>
                  <button 
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteDevice(dev.name);
                    }}
                    className="p-1 rounded hover:bg-red-950/50 hover:text-red-400 text-slate-500 transition-all"
                    title="Remove device"
                  >
                    <Trash className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}

            {devices.length === 0 && (
              <div className="text-center py-6 text-slate-500 border border-dashed border-slate-800 rounded-xl">
                No devices configured. Click the "+" button to add one.
              </div>
            )}
          </div>
        </aside>

        {/* Dashboard Center */}
        <main className="flex-1 p-8 overflow-y-auto flex flex-col gap-8">
          
          {/* Active device top header info */}
          {schema ? (
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-slate-900 pb-6">
              <div>
                <h2 className="text-2xl font-bold tracking-tight text-white">{schema.device_name}</h2>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mt-1.5 text-xs text-slate-400">
                  <span className="bg-slate-900 px-2.5 py-1 rounded-md border border-slate-800/80">Schema: {selectedDeviceName} ({schema.version})</span>
                  {schema.firmware && (
                    <span className="bg-slate-900 px-2.5 py-1 rounded-md border border-slate-800/80">Firmware: {schema.firmware}</span>
                  )}
                  <span className="flex items-center gap-1.5"><Database className="h-3.5 w-3.5 text-slate-500" /> Host: {devices.find(d=>d.name === selectedDeviceName)?.host}</span>
                </div>
              </div>
              <button 
                onClick={() => fetchSchemaAndValues(selectedDeviceName)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-semibold text-slate-300 transition-colors"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loadingSchema ? "animate-spin" : ""}`} />
                Reload Schema
              </button>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-12">
              <Layers className="h-16 w-16 text-slate-800 mb-4" />
              <h2 className="text-lg font-bold text-slate-400">No Device Selected</h2>
              <p className="text-sm text-slate-600 max-w-sm mt-1">Select a device from the left sidebar to load its schema-driven control center dashboard.</p>
            </div>
          )}

          {/* Staged Changes alert banners */}
          {Object.keys(stagedChanges).length > 0 && (
            <div className="bg-amber-950/40 border border-amber-800/60 rounded-xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 shadow-lg shadow-amber-500/5">
              <div className="flex items-center space-x-3">
                <div className="bg-amber-500/20 p-2 rounded-lg text-amber-400">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <div>
                  <h4 className="font-semibold text-sm text-white">Pending Configuration Changes ({Object.keys(stagedChanges).length})</h4>
                  <p className="text-xs text-amber-300/80 mt-0.5">Holding register values have been staged but not written to the Modbus device yet.</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 self-end md:self-auto">
                <button 
                  onClick={() => setStagedChanges({})}
                  className="px-3 py-1.5 rounded-lg text-slate-400 hover:text-slate-200 text-xs font-semibold transition-colors"
                >
                  Discard
                </button>
                <button 
                  onClick={applyStagedChanges}
                  className="flex items-center gap-1.5 px-4 py-1.5 bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold rounded-lg text-xs tracking-wide shadow-md shadow-amber-500/10 transition-colors"
                >
                  <Save className="h-3.5 w-3.5" />
                  Apply Changes
                </button>
              </div>
            </div>
          )}

          {/* Action progress log banner */}
          {writingStatus && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex items-center space-x-3">
              <RefreshCw className="h-4 w-4 text-amber-500 animate-spin" />
              <span className="text-sm text-slate-300">{writingStatus}</span>
            </div>
          )}

          {schema && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              
              {/* Left Column: Read-Only Registers (Discrete Inputs, Input Registers) */}
              <div className="lg:col-span-2 flex flex-col gap-6">
                <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                  <Layers className="h-4.5 w-4.5" />
                  Telemetry (Read-Only Status)
                </h3>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {readOnlyRegisters.map((reg) => {
                    const val = values[reg.name];
                    const hasVal = val !== undefined && val !== null;
                    return (
                      <div 
                        key={reg.name}
                        className="bg-slate-900/30 border border-slate-900 rounded-xl p-5 hover:border-slate-800 transition-all flex flex-col justify-between"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <span className="text-[10px] uppercase font-bold tracking-wider text-slate-500">{reg.register_type.replace("_", " ")} ({reg.address_dec})</span>
                            <h4 className="font-bold text-sm text-white truncate" title={reg.name}>{reg.name}</h4>
                          </div>
                          {reg.unit && (
                            <span className="text-xs bg-slate-950 border border-slate-850 px-2 py-0.5 rounded font-mono text-slate-400">{reg.unit}</span>
                          )}
                        </div>
                          <div className="mt-4 flex items-baseline gap-1.5">
                          {hasVal ? (
                            <span className="text-2xl font-bold text-white tracking-tight">
                              {formatTileVal(val, reg.data_type, reg.enum_values)}
                            </span>
                          ) : (
                            <span className="text-sm text-slate-600 italic">Offline</span>
                          )}
                        </div>
                        {reg.description && (
                          <p className="text-[11px] text-slate-500 leading-normal mt-2.5 line-clamp-2" title={reg.description}>
                            {reg.description}
                          </p>
                        )}
                      </div>
                    );
                  })}

                  {readOnlyRegisters.length === 0 && (
                    <div className="col-span-2 text-center py-8 text-slate-500 border border-slate-900 rounded-xl">
                      No read-only telemetry registers defined in this schema.
                    </div>
                  )}
                </div>
              </div>

              {/* Right Column: Controls (WO Actions & RW Holding Configuration) */}
              <div className="flex flex-col gap-8">
                
                {/* Write-Only Actions (WO Coils) */}
                {writeOnlyRegisters.length > 0 && (
                  <div className="flex flex-col gap-4">
                    <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                      <Play className="h-4.5 w-4.5" />
                      Trigger Actions (WO Coils)
                    </h3>
                    <div className="flex flex-col gap-3 bg-slate-900/20 border border-slate-900 rounded-xl p-5">
                      {writeOnlyRegisters.map((reg) => (
                        <div key={reg.name} className="flex items-center justify-between gap-4 p-2 bg-slate-950/40 rounded-lg border border-slate-900/60">
                          <div className="min-w-0">
                            <h4 className="font-bold text-xs text-white truncate" title={reg.name}>{reg.name}</h4>
                            <p className="text-[10px] text-slate-500 truncate" title={reg.description || `Address ${reg.address_dec}`}>{reg.description || `Address ${reg.address_dec}`}</p>
                          </div>
                          <button 
                            onClick={() => triggerCoilAction(reg.name)}
                            className="flex items-center gap-1 px-3 py-1.5 bg-gradient-to-r from-amber-500 to-orange-600 hover:from-amber-600 hover:to-orange-700 text-white font-bold rounded-lg text-[10px] uppercase tracking-wider shadow-sm transition-all"
                          >
                            <Play className="h-3 w-3 fill-current" />
                            Trigger
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Read-Write Configuration (Holding Registers) */}
                <div className="flex flex-col gap-4">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <Settings className="h-4.5 w-4.5" />
                    Settings Configuration
                  </h3>
                  
                  <div className="flex flex-col gap-5 bg-slate-900/20 border border-slate-900 rounded-xl p-6">
                    {readWriteRegisters.map((reg) => {
                      const currentVal = values[reg.name];
                      const stagedVal = stagedChanges[reg.name];
                      const isStaged = stagedVal !== undefined;
                      const hasCurrentVal = currentVal !== undefined && currentVal !== null;
                      
                      // Resolve display value: staged value takes precedence
                      const displayVal = isStaged
                        ? formatDisplayVal(stagedVal, reg.data_type)
                        : (hasCurrentVal ? formatDisplayVal(currentVal, reg.data_type) : "");
                      // For enum select: find the current ordinal key
                      const enumSelectVal = isStaged
                        ? String(stagedVal)
                        : (hasCurrentVal ? String(currentVal) : "");

                      return (
                        <div 
                          key={reg.name} 
                          className={`flex flex-col gap-2 p-4 rounded-xl border transition-all ${isStaged ? "bg-amber-500/5 border-amber-500/80" : "bg-slate-950/40 border-slate-900/80"}`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <span className="text-[9px] uppercase font-bold tracking-wider text-slate-500">Address {reg.address_dec}</span>
                              <h4 className="font-bold text-xs text-white" title={reg.name}>{reg.name}</h4>
                            </div>
                            {isStaged && (
                              <span className="text-[9px] bg-amber-500 text-slate-950 font-extrabold px-1.5 py-0.5 rounded tracking-wide uppercase">Staged</span>
                            )}
                          </div>

                          {/* Control Input */}
                          <div className="mt-1 flex items-center gap-2">
                            {reg.enum_values ? (
                              // Render Dropdown: value and onChange use numeric ordinal codes
                              <select
                                value={enumSelectVal}
                                onChange={(e) => handleStageChange(reg.name, Number(e.target.value), currentVal)}
                                disabled={!hasCurrentVal}
                                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                {!hasCurrentVal && <option value="">Loading...</option>}
                                {hasCurrentVal && Object.entries(reg.enum_values).map(([code, label]) => (
                                  <option key={code} value={code}>{label}</option>
                                ))}
                              </select>
                            ) : reg.data_type === "float32" ? (
                              // Float32: type="text" to avoid browser locale comma issue
                              <div className="relative w-full flex items-center">
                                <input
                                  type="text"
                                  inputMode="decimal"
                                  value={displayVal}
                                  onChange={(e) => handleStageChange(reg.name, e.target.value, currentVal)}
                                  placeholder={hasCurrentVal ? formatDisplayVal(currentVal, reg.data_type) : "Offline"}
                                  disabled={!hasCurrentVal}
                                  className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-3 pr-10 py-2 text-xs text-white font-mono focus:outline-none focus:border-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
                                />
                                {reg.unit && (
                                  <span className="absolute right-3 text-[10px] text-slate-500 font-semibold">{reg.unit}</span>
                                )}
                              </div>
                            ) : (
                              // Integer types: type="number" with step=1
                              <div className="relative w-full flex items-center">
                                <input
                                  type="number"
                                  step="1"
                                  value={displayVal}
                                  onChange={(e) => handleStageChange(reg.name, e.target.value, currentVal)}
                                  placeholder={hasCurrentVal ? formatDisplayVal(currentVal, reg.data_type) : "Offline"}
                                  disabled={!hasCurrentVal}
                                  className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-3 pr-10 py-2 text-xs text-white focus:outline-none focus:border-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
                                />
                                {reg.unit && (
                                  <span className="absolute right-3 text-[10px] text-slate-500 font-semibold">{reg.unit}</span>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Info and current value helper */}
                          {hasCurrentVal && isStaged && (
                            <span className="text-[10px] text-slate-400 italic mt-0.5 flex items-center gap-1">
                              <Info className="h-3 w-3" />
                              Original: {formatDisplayVal(currentVal, reg.data_type)} {reg.unit || ""}
                            </span>
                          )}

                          {reg.description && (
                            <p className="text-[10px] text-slate-500 leading-normal mt-1.5">
                              {reg.description}
                            </p>
                          )}
                        </div>
                      );
                    })}

                    {readWriteRegisters.length === 0 && (
                      <div className="text-center py-6 text-slate-500">
                        No read-write holding registers defined in this schema.
                      </div>
                    )}
                  </div>
                </div>

              </div>

            </div>
          )}

        </main>

      </div>
    </div>
  );
}
