# EPIC 21 Smart Life IR Discovery

EPIC 21A replaces the historical `t3_ir` placeholder with an explicit,
provider-neutral, read-only inventory layer. It does not transmit or learn IR,
execute scenes, map commands, or enable appliance controls.

## Existing integration audit

- EPIC 11 already owns the generic IR device/profile/driver contract. EPIC 21A
  leaves its command and learning behavior unchanged.
- The Tapo H110 discovery and driver remain dedicated to the configured
  living-room remotes.
- TinyTuya is already used for electricity and lighting, but exposes no
  verified Smart Life IR inventory in this codebase.
- No LocalTuya integration exists. EPIC 21B adds a minimal signed Smart Life
  cloud reader for one explicitly configured device.
- Home Assistant already has a bounded authenticated state reader, which the
  Home Assistant adapter reuses.
- MQTT already has one shared connection, but no documented Smart Life IR
  inventory topic or payload exists. EPIC 21A creates no new subscription.

## Provider selection

No provider is inferred. Set `SMARTLIFE_IR_PROVIDER` explicitly to one of:

- `smartlife_cloud`
- `tuya_local`
- `homeassistant`
- `mqtt`
- `unsupported`

An unset or invalid value fails closed as `unsupported`. Local Tuya and MQTT
remain unavailable until a documented IR inventory source is configured.
Existing TinyTuya electricity/lighting and MQTT integrations are not reused as
proof of an IR device.

The `smartlife_cloud` provider uses a minimal signed, GET-only Tuya OpenAPI
client for the Singapore data center. It requires:

```text
SMARTLIFE_IR_PROVIDER=smartlife_cloud
TUYA_CLOUD_ACCESS_ID=...
TUYA_CLOUD_ACCESS_SECRET=...
TUYA_CLOUD_DEVICE_ID=...
TUYA_CLOUD_REGION=sg
```

The access token is cached until shortly before expiry and refresh is guarded
so only one caller authenticates at a time. Requests are restricted to device
information, specification, status, and the optional device function list for
the single configured device. No command method or arbitrary path is exposed.

The Home Assistant adapter reuses the existing bounded Home Assistant state
reader. It only considers entity IDs explicitly allow-listed in:

```text
SMARTLIFE_IR_HA_ENTITY_IDS=climate.example,remote.example
```

## Read-only contract

`GET /api/ir/inventory` returns:

- selected provider and whether it was positively detected;
- provider online/health/state-quality values;
- safe available capability categories;
- a safe discovery reason;
- product name, model, redacted device ID, firmware, online state, and
  supported command categories for each verified inventory item.

Raw Home Assistant entity IDs, Tuya device IDs, credentials, local keys,
tokens, command mappings, and IR codes are never returned.

The household registry keeps the Bed Room Air Conditioner visible and Unknown
until exactly one explicitly configured inventory device is positively
verified. Capability categories are diagnostic metadata only; no controls are
enabled.

## Future provider work

- Smart Life cloud inventory supports the official device information,
  specification, and status APIs for one explicitly configured `wnykq` device.
- Local Tuya requires a verified IR device schema and safe read-only
  capability query.
- Home Assistant requires the owner to provide the exact entity ID and confirm
  that it represents the bedroom IR device.
- MQTT requires a documented retained inventory/state topic and payload
  schema.

IR transmission, learning, scenes, command mappings, AC controls, and fan
controls remain out of scope.

## T3 hub and virtual Air inventory (EPIC 21D)

The verified `T3-Smart-301` is projected as two read-only inventory records:

- `T3 Hub` combines the verified Smart Life identity with temperature,
  humidity, and last-seen data already received by the shared
  `condo/t3/state` MQTT subscriber. It does not create another TinyTuya poller
  or MQTT subscription.
- `Air Remote` is a virtual `infrared_ac` child of the hub. The relationship is
  represented using dashboard-local identifiers only. It has no command
  capabilities and remains non-controllable.

Fresh MQTT telemetry confirms hub health. Stale telemetry is reported as
offline rather than silently presenting old sensor values as current. During a
cloud inventory outage, fresh MQTT telemetry may continue to identify the
physical hub, but the virtual child is not invented without its verified cloud
identity.

## Air child mapping audit (EPIC 21E)

The existing TinyTuya inventory cache contains nine descriptors for the
verified `infrared_ac` child. The table uses deterministic, dashboard-local
fingerprints derived from private mapping keys; neither the keys nor descriptor
contents are recorded here.

| Index | Fingerprint | Declared type | Encoded length | Structural shape | Changed with UI state |
| ---: | --- | --- | ---: | --- | --- |
| 1 | `b2955482fd3d` | enum | 89 | 3-field object; value schema has four integers and one string | Not observed |
| 2 | `9d3ec7ae4bf1` | enum | 89 | 3-field object; value schema has four integers and one string | Not observed |
| 3 | `14ec672582d9` | string | 55 | 3-field object; scalar string value schema | Not observed |
| 4 | `78606287df15` | string | 53 | 3-field object; scalar string value schema | Not observed |
| 5 | `833db6e771b4` | enum | 91 | 3-field object; value schema has four integers and one string | Not observed |
| 6 | `ccafe739b51c` | enum | 92 | 3-field object; value schema has four integers and one string | Not observed |
| 7 | `bf4ec672018b` | boolean | 45 | 3-field object; empty object value schema | Not observed |
| 8 | `a344d4ec875a` | enum | 94 | 3-field object; value schema has four integers and one string | Not observed |
| 9 | `e1d5b5cb356c` | enum | 92 | 3-field object; value schema has four integers and one string | Not observed |

These are capability descriptors, not observed state values or a transmit
contract. The saved cloud record contains no Air status entries, and the saved
LAN snapshot contains no Air DPS entries. Consequently there are no repeated
observations against which a Smart Life display change can be measured.

No descriptor is assigned to power, target temperature, mode, fan, or swing.
The boolean descriptor is not treated as proof of power, the string descriptors
are not treated as proof of a combined AC state, and the enum descriptors are
not assigned semantic labels. Doing so would be inference rather than an
observation.

No safe IR transmit contract has been found. The cache does not disclose a
documented child RPC method, request schema, response semantics, or confirmed
mapping from a displayed AC state to a callable operation. Controls therefore
remain disabled. A future mapping experiment requires separately approved,
operator-controlled state changes with before/after read-only snapshots; a
future send implementation additionally requires a verified method and request
schema.
