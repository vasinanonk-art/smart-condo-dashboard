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

No credentials, network addresses, vendor device IDs, tokens, stream URLs,
provider payloads, or control capabilities are exposed.

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
