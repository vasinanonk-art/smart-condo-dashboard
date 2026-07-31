# EPIC 19 — Live Device Monitoring

## Milestone 1

Milestone 1 adds a read-only live health projection over the existing safe
household device registry. It does not change device control routes, provider
ownership, vendor configuration, or the `/api/devices` response.

### Health contract

Authenticated clients may read:

```text
GET /api/device-health
```

The response contains:

- stable safe device ID, display name, room, and category
- normalized health and green/yellow/red indicator
- online, offline, or unknown state
- most recent successful heartbeat
- heartbeat age
- last provider observation
- measured provider projection response time
- observation timestamp
- aggregate online, offline, unknown, healthy, and degraded counts

No credentials, vendor device IDs, tokens, stream URLs, provider payloads, or
control capabilities are exposed. Milestone 2 adds authenticated, validated
IP and MAC health fields when a provider or protected configuration supplies
them; they remain null otherwise.

### Runtime behavior

- The tracker is in-memory and starts empty after a service restart.
- A successful online observation advances the heartbeat and last-seen time.
- Explicit provider offline state takes precedence immediately.
- When a provider temporarily returns unknown, the most recent heartbeat remains
  online only for the configured staleness window.
- `DEVICE_HEALTH_STALE_AFTER_SECONDS` controls the window and defaults to 90
  seconds, with a minimum of 15 seconds.
- The endpoint samples on demand and creates no background worker.
- The dashboard uses one 30-second poller, pauses while the document is hidden,
  and keeps the last successful card state when a refresh fails.

### Compatibility

Existing device, topology, control, authentication, and provider APIs remain
unchanged. The endpoint is protected by the existing dashboard session
middleware and is read-only, so no CSRF token is required.

## Milestone 2 — Provider health metrics

Milestone 2 adds optional fields to each existing device object. The Milestone
1 fields, endpoint path, summary, polling interval, and online/offline behavior
are unchanged.

| Field | Type | Meaning |
|---|---|---|
| `firmware_version` | string or null | Provider-reported software/firmware version |
| `uptime` | integer or null | Provider-reported uptime in seconds |
| `ip_address` | string or null | Valid configured or provider-reported IP address |
| `mac_address` | string or null | Valid configured or provider-reported MAC address |
| `signal_strength` | number or null | Provider-reported RSSI in dBm |
| `connection_type` | string or null | Normalized `Wi-Fi`, `Ethernet`, or explicit `Unknown` |
| `model` | string or null | Provider-reported or verified configured model |
| `manufacturer` | string or null | Provider-reported or verified configured manufacturer |

All eight fields are provider-dependent. Missing, malformed, unsupported, or
ambiguous values are returned as `null`; the backend does not infer a connection
type from an address or a manufacturer from a display name.

### Current provider coverage

- LG webOS TV: cached model and firmware plus validated configured IP and MAC.
  Uptime, RSSI, connection type, and manufacturer remain null unless webOS
  explicitly reports them.
- Tapo H110-backed living-room IR devices: cached bridge model, firmware, IP,
  MAC, uptime, RSSI, connection type, and manufacturer when present. These
  metrics describe the shared bridge connection, not proof that an IR command
  reached the appliance.
- Configured cameras: validated configured IP, model, and manufacturer, plus
  firmware when returned by the active read-only provider.
- Configuration-unavailable camera placeholders and the unverified
  provider-neutral bedroom IR path return null metrics.

The dashboard renders only fields with real values. It does not show empty
metric rows and retains the existing 30-second visibility-aware poller.
