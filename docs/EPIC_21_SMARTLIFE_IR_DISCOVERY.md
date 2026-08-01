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
