# Master Specification (PRD): Modbus Control Software Suite

## 1. Projektübersicht & Architektur-Philosophie

Die `Modbus Control Software Suite` ist ein generisches Set aus Werkzeugen, um Modbus-fähige Geräte via Modbus TCP auszulesen, zu überwachen und zu steuern.

**Architektur-Kernprinzipien:**

* **Contract-First:** Sämtliche Datenstrukturen (Geräte-Konfigurationen, REST-API Payloads, Modbus-Register-Modelle) sind in einem zentralen, unabhängigen Vertrags-Paket (`modbus-ctrl-contracts`) als strikte Pydantic-Modelle definiert.

* **External Schema Dependency:** Die konkreten JSON-Definitionen der Register und deren Basis-Parser leben nicht im Kern dieser Suite, sondern werden als Abhängigkeit über ein externes Schema-Paket bezogen (das bewährte Config-Modell wird hier für beliebige Geräte wiederverwendet).

* **Shared State:** CLI und Web-GUI (Control Center) teilen sich dieselbe lokale Konfigurationsdatei (YAML) für das Geräte-Management, validiert durch das Contracts-Paket.

* **Monorepo Workspace:** Die Suite wird als Python Workspace (z. B. via `uv`) entwickelt, was die getrennten logischen Pakete (Core, Contracts, CLI, GUI, Simulation) sauber separiert, aber gemeinsam versionierbar macht.

## 2. Hardware Constraints & Protokoll-Gesetze

Jede Komponente MUSS flexibel genug sein, um gerätespezifische Hardware-Vorgaben abzubilden, implementiert aber strikte Fallbacks für den Modbus TCP Standard:

* **TCP & Port:** Modbus TCP, Default Port 502, `Unit ID` (Slave ID) konfigurierbar (Default 1).

* **Verbindungs-Management:** TCP Keep-Alive zwingend implementiert, um Verbindungsabbrüche zu vermeiden und Geräte-Limits bei parallelen Verbindungen zu respektieren.

* **Endianness (Kritisch & Konfigurierbar):**
  * Byte- und Word-Order (z.B. Big-Endian für 16-bit, Little-Endian für 32/64-bit) müssen vom Schema vorgegeben oder als Parameter für das Packing/Unpacking übergeben werden können.

* **Polling Rate Limit:** Konfigurierbares Polling-Intervall pro Gerät, um Überlastungen der Hardware zu vermeiden (z.B. Drosselung auf max. 1 Hz für langsame IoT-Geräte).

* **Gerätespezifisches Register-Verhalten (Write Behavior):**
  * Das System unterstützt den Umgang mit flüchtigen Registern:
    * *Trigger Coils (WO):* Werden oft als Taster genutzt. Nach dem Schreiben (`ON`) können sie geräteseitig sofort wieder auf `OFF` fallen.
    * *Holding Registers (RW):* Konfigurationswerte, die nach erfolgreicher Übernahme geräteseitig möglicherweise auf Default- oder Nullwerte (z.B. `-1` oder `NaN`) zurückgesetzt werden. Rücklesen ist nicht zwingend zur Status-Verifikation geeignet.

## 3. Die Paket-Architektur (The Packages)

### 3.1 `modbus-ctrl-contracts` (The Single Source of Truth)

Definiert alle systemweiten Pydantic-Modelle. Hat keine Abhängigkeiten außer `pydantic` und der Schema-Basis-Bibliothek.

* **`DeviceConfig`:** Modell für ein einzelnes Gerät (Name, Host/IP, Port, Unit ID, Schema-Referenz, Active).
* **`AppConfig`:** Modell für die lokale `devices.yaml`.
* **API Models:** Pydantic-Requests/Responses für FastAPI (`WriteBatchRequest`, `TelemetryDeltaResponse`).

### 3.2 `modbus-ctrl-core` (Backend Library)

Die `asyncio` basierte Polling- und Übersetzungs-Bibliothek (Wrapper um `pymodbus`).

* **Schema Resolution & Auto-Discovery:** Ermittelt (entweder durch statische Konfiguration in der `DeviceConfig` oder durch Auslesen definierter Identifikations-Register) das korrekte JSON-Schema für das verbundene Gerät.
* **Aggregation Layer:** Chunking (Split von Requests, z.B. bei 125 Registern) und Gap-Filling (Auffüllen kleiner Adress-Lücken zur Reduktion des HTTP-Overheads).
* **Translation Layer:** Endianness-konformes Packing/Unpacking (`struct`) und Anwenden von Skalierungsfaktoren (`scale_factor`).

### 3.3 `modbus-ctrl-simulation` (Mock Server)

Lokaler Dev-Server für Entwicklungszwecke.

* Startet via CLI: `modbus-sim --schema device_a.json --port 5020`.
* Simuliert Register-Zustände, wendet Schema-Regeln an und ahmt ggf. flüchtiges Verhalten (Auto-Reset von Coils) nach.

## 4. Component: `modbus-ctrl-cli` (Typer Command Line Interface)

Nutzt `modbus-ctrl-core` und `modbus-ctrl-contracts`. Bietet Zugriff auf Metadaten, Modbus-Reads/Writes und lokales Geräte-Management. (Executable Alias z.B. `modbus-ctrl`).

### 4.1 CLI Device Management (Local YAML)

Verwaltet die zentrale `devices.yaml`, auf die auch das Control Center zugreift.

* `modbus-ctrl device add "Device_Alpha" 192.168.1.100 --port 502`
* `modbus-ctrl device list` (Zeigt Tabelle mit Index, Name, Host)
* `modbus-ctrl device remove "Device_Alpha"`

### 4.2 Targeting (Zielauswahl)

Lese- und Schreibvorgänge können statt fester IPs einfach die konfigurierten Gerätenamen oder Indizes nutzen:

* `modbus-ctrl read Temperature --target "Device_Alpha"`
* `modbus-ctrl read Temperature --target 0` (via Listen-Index)
* `modbus-ctrl read Temperature --host 10.0.0.5` (Ad-hoc Override)

### 4.3 Kommando: `list` (Metadaten-Inspektion)

* **Syntax:** `modbus-ctrl list [SEARCH_TERM] [OPTIONS]`
* **Views (`--view`):** `table`, `grouped`, `csv`, `ini`, `json`, `yaml`. (Müssen zwingend alle Schema-Metadaten wie Address, Type, Access, Unit, Scale etc. enthalten).

### 4.4 Kommando: `read` & `write`

* **PascalCase Extractor (Read):** Akzeptiert Freitext, extrahiert via Regex PascalCase-Wörter (Register-Keys) und filtert diese gegen das Modbus-Schema.
* **Output-Formatting:** `--format console` (mit Unit), `--format json/yaml/csv/ini` (ohne Unit, pure Zahl). `--enum-mode literal|ordinal`.
* **Asymmetrisches Write (Strikt Ordinal):** Akzeptiert beim Schreiben AUSSCHLIESSLICH Zahlen, niemals Enum-Strings. Ignoriert Delimiter (`:`, `=`, `,`). Bewahrt Float-Punkte und negative Vorzeichen.
* **Echo Feedback:** `Success: ConfigRegister set to 3 ("Mode A")`

## 5. Component: `modbus-ctrl-center` (Web Service & SPA)

Headless FastAPI Server + React/Vite/Tailwind SPA.

### 5.1 Backend (FastAPI)

* **Shared Database:** Liest/Schreibt exakt dieselbe `devices.yaml` (via Contracts) wie die CLI.
* **Polling Engine:** Asynchrone Endlosschleife (Intervall definiert durch `DeviceConfig`).
* **WebSockets (`/ws/telemetry`):** Pusht Delta-Updates der gepollten Registerwerte an verbundene Browser.

### 5.2 Schema-Driven UI (React SPA)

Generiert sich zur Laufzeit zu 100% aus dem gewählten JSON-Schema.

* **Read-Only (RO):** Dashboard-Kacheln für Input Registers/Discrete Inputs (Live-Werte).
* **Control UI & Staging:**
  * *WO Coils:* Sofort ausführende **Action-Buttons** (ein Klick = POST Request). Kein Staging.
  * *RW Holding Registers:* `<select>` (Enums) oder `<input type="number">` (Numerics).
  * *Staging:* Änderungen an RW-Feldern werden vom Frontend optisch (z.B. gelb) markiert (Pending). Ein zentraler **"Apply Changes"** Button bündelt alle Pending-Werte als JSON und sendet sie im Batch an die API.

## 6. Monorepo / Workspace Layout

Die Architektur wird als Workspace aufgebaut, um interne Abhängigkeiten sauber aufzulösen, externe Config-Pakete zu integrieren und eine einfache PyPI-Veröffentlichung zu ermöglichen.

```text
modbus-control/
├── pyproject.toml              # Workspace Root (Workspace Defs, Linting)
│
├── packages/                   # Interne Workspace-Pakete
│   ├── modbus-ctrl-contracts/  # Pydantic Modelle (DeviceConfig, ApiModels)
│   │   ├── pyproject.toml      # deps: pydantic, external-schema-package
│   │   └── src/modbus_ctrl_contracts/
│   │
│   ├── modbus-ctrl-core/       # Async Modbus Backend Library
│   │   ├── pyproject.toml      # deps: pymodbus, modbus-ctrl-contracts
│   │   └── src/modbus_ctrl_core/
│   │
│   ├── modbus-ctrl-cli/        # Typer CLI Entrypoint
│   │   ├── pyproject.toml      # deps: typer, rich, modbus-ctrl-core, modbus-ctrl-contracts
│   │   └── src/modbus_ctrl_cli/
│   │
│   ├── modbus-ctrl-simulation/ # Mock Server Entrypoint
│   │   ├── pyproject.toml      # deps: pymodbus, external-schema-package
│   │   └── src/modbus_ctrl_simulation/
│   │
│   └── modbus-ctrl-center/     # FastAPI App & React SPA
│       ├── pyproject.toml      # deps: fastapi, uvicorn, modbus-ctrl-core, modbus-ctrl-contracts
│       ├── frontend/           # React/Vite TypeScript Projekt
│       │   └── src/
│       └── src/modbus_ctrl_center/
│           ├── server.py       
│           └── static/         # (Build-Output des React Frontends)