# Universal IR framework

LG webOS is outside this framework. Existing climate endpoints remain as a
compatibility API; capability-driven IR controls use `/api/ir`.

## Architecture

```text
Household UI
    │ safe capability metadata + command/value
    ▼
IR device registry
    │ device allow-list
    ▼
Versioned JSON profile
    │ private IR code lookup
    ▼
Per-device bounded FIFO
    │ one active command per device
    ▼
IR driver lifecycle
    │ bounded send
    ▼
Verified IR bridge adapter
```

`IR_DEVICE_REGISTRY_FILE` may point to a persistent registry. When unset,
`config/ir/devices.json` is used. `IR_PROFILE_DIR` may point to a persistent
profile directory; the default is `config/ir/profiles`.

The public API exposes only safe device identity, capability metadata, runtime
status, controllability, and a safe unavailable reason. It never returns IR
codes, driver/profile identifiers, bridge addresses, credentials, or driver
internals.

## Capability model

Every device contains a capability allow-list. Its profile supplies declarative
metadata:

```json
{
  "id": "power",
  "type": "button",
  "label": "Power",
  "icon": "power",
  "group": "main",
  "confirm": false
}
```

Supported widget types are `button`, `toggle`, `select`, `range`,
`navigation`, `media`, and `custom`. A range also declares numeric `min`,
`max`, and positive `step`, plus an optional `unit`. A select declares a
non-empty, unique `values` list.

The frontend chooses controls only from `type` and the commands attached to that
capability. A select exposes only values with commands. A range is enabled only
when its complete declared sequence has command mappings. Capability IDs and
device brands do not select widgets.

## Driver lifecycle

Every driver implements:

- `initialize()`
- `shutdown()`
- `health()`
- `supports(profile)`
- `send(command)`
- `learn()` (interface only)

Health includes `online`, `ready`, `last_error`, and `driver_version`. Drivers
must honor the timeout carried by each dispatch command. Registration initializes
the driver, replacement shuts down the previous driver, and process exit shuts
down all registered drivers.

The production Tapo driver reads the verified H110 bridge status through the
existing local `python-kasa` discovery path. This confirms bridge reachability,
authentication, model, firmware, and discovery latency. The deployed
`python-kasa 0.10.2` API does not expose an IR transmit callable or a verified
command format, so the driver remains not ready for sends and reports
`tapo_ir_send_unsupported`.

An audited adapter may call `register_verified_sender()` only after its
transport and command format are verified. No sender is registered by the
default installation, no checked-in profile contains IR codes, and learning
remains disabled.

The authenticated read-only endpoint `/api/tapo-ir/existing-remotes` projects
the H110's already-configured `SMART.TAPOREMOTE` children. It exposes only a
dashboard-local identifier, friendly name, category/type, safe reported state,
and whether stored command metadata exists. Vendor child IDs, remote IDs,
opaque IR fields, stored command references, credentials, and raw child records
remain private. Discovered remotes remain non-controllable until an exact
transmit method and schema pass the verification gate.

All verified send attempts are serialized by one bridge lock. Device queues
remain independent, but two commands can never transmit through the physical
bridge simultaneously.

## Queue and runtime registry

Each device has an independent FIFO with a maximum of 20 waiting commands. One
caller drains a device queue synchronously, so no unmanaged worker thread is
created. Commands for different devices can drain independently. When full, the
oldest waiting command receives `ir_queue_overflow`.

Transient failures and timeouts retry once. Validation and permanent failures do
not retry.

Runtime state tracks enabled, online and healthy state, internal driver/profile
ownership, firmware version, last seen/command/success/failure, pending depth,
retry count, authentication state, bridge model, latency, last response, and a
safe last-error reason. Driver/profile ownership remains private; the other safe
fields are returned under `runtime_status`.

Each dispatched command produces one structured log record with timestamp,
safe device and command IDs, duration, result, and safe error reason. IR codes,
credentials, bridge addresses, tokens, and raw bridge responses are never
logged.

## Profile schema

Schema version 1 requires:

- `schema_version`
- `id`
- `brand`
- `model`
- `device_type`
- `capabilities`
- `commands`
- `metadata`

Commands map a stable command ID to a declared capability, safe label/icon,
optional typed value, and private IR code. Duplicate JSON keys, duplicate
capabilities, unknown types/schema versions, invalid ranges and malformed
commands are rejected with descriptive errors.

Schema dispatch is explicit so later readers can retain the version 1 contract
when a future version is added.

The checked-in household inventory uses the empty `unconfigured` profile. No
unverified capability or IR code is enabled.

## Future learning extension

`learn()`, `save()`, `delete()`, and `rename()` are reserved interfaces only.
They raise `ir_learning_not_implemented`. There are no learning or profile
mutation routes in EPIC 11.
