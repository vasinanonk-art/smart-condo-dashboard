# Smart Condo Dashboard Roadmap

The roadmap starts from the stable v1.0.0 production baseline. Planned work must
preserve authentication, CSRF, runtime configuration protection, safe provider
projections, and fail-closed capability reporting.

## Completed

### EPIC 17 — TP-Link read-only dashboard provider integration

- Authenticated provider health
- Provider metadata and capability matrix
- Safe diagnostics
- Read-only camera inventory
- Explicit unsupported-capability states

### EPIC 18 — Smart Condo Control Center redesign

- Shared frontend design system
- iPad-first control-center home
- Responsive household cards
- Floating touch navigation
- Notifications and designed empty/loading states
- Accessible focus, labels, safe areas, and reduced motion

## In progress

### EPIC 19 — Live Device Monitoring

Milestone 1 implemented in `9380848`:

- Device Health
- Online/Offline
- Last Seen
- Heartbeat
- Response-time measurement
- Authenticated read-only health endpoint
- Design-system Device Health card

Remaining:

- Event Timeline

Monitoring must use bounded polling or event-driven updates, deduplicate
repeated transitions, avoid exposing provider identifiers, and distinguish
offline, unavailable, stale, and unknown state.

## Planned

### EPIC 20 — Theme System

- Light Theme
- Theme Switch
- User Preference

The theme system should extend semantic design tokens, preserve contrast and
accessibility, avoid page-specific overrides, and respect operating-system
preference when no user preference is saved.

### EPIC 21 — Camera Integration

- Live Snapshot
- Streaming
- PTZ
- Recording Status

Only verified configured capabilities may be activated. Streams and snapshots
must remain authenticated and proxied or short-lived. PTZ must use narrow
per-camera locks, bounded movement, and automatic stop. Xiaomi capabilities
must not be assumed.

### EPIC 22 — Automation

- Scene Execution
- Scheduler
- Rule Engine

Automation must retain authentication and CSRF for writes, strict command
allow-listing, safe persistence, bounded retries, auditable execution results,
and no redesign of existing provider ownership without migration tests.
