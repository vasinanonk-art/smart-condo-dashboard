# Changelog

## [Unreleased]

### Camera PTZ candidate

- Add capability-gated directional PTZ nudges for the verified Bedroom Tapo
  C200 with bounded speed, a 0.5-second movement ceiling, and automatic stop.
- Keep zoom, presets, home position, Xiaomi controls, and all unverified camera
  capabilities disabled.
- Audit authenticated, rejected, and failed camera command attempts without
  exposing camera credentials or provider identifiers.

## [1.0.17] - 2026-08-15

### Home Details mobile layout

- Keep the Environment temperature and humidity trend chart responsive without
  fixed mobile heights that can overlap following cards.
- Replace the Home Air Quality chart with a compact Living Room and Bedroom
  PM2.5 summary and status.
- Collapse Advanced Details by default on mobile and remove Home chart export
  controls while preserving Min, Average, and Max statistics.

## [1.0.16] - 2026-08-15

### Presence identity settings

- Add validated Beer and Seem IP/MAC identities under Settings → Presence.
- Keep presence testing read-only and reset household state conservatively
  after identity changes without leaving shadow mode.

## [1.0.15] - 2026-08-13

### Household presence shadow mode

- Replace independent arrival lighting with one persisted household presence
  state machine that never turns lights on.
- Default presence automation to shadow mode, recording daytime departure
  actions without issuing physical Sonoff commands.
- Add fail-safe presence classification, restart handling, diagnostics, and
  deterministic Bangkok-time boundary coverage.

## [1.0.14] - 2026-08-12

### Mobile navigation

- Keep primary navigation to five destinations and retain History under More.
- Restore dependency-free navigation and top-action icons.
- Prevent mobile label collisions and reserve safe-area space below page content.

## [1.0.13] - 2026-08-12

### Navigation and status semantics

- Simplify primary navigation to Home, Devices, Electricity, Cameras, and More
  while preserving existing routes and controls.
- Add Devices and grouped More landing views using existing frontend data.
- Normalize user-facing availability states and clarify Home, Cameras,
  Electricity, and System status presentation without changing backend logic.

## [1.0.12] - 2026-08-11

### Deployment identity

- Add an immutable managed-runtime release marker derived from the approved
  source commit and VERSION.
- Verify deployed release identity without trusting runtime `.git` metadata.
- Preserve the release marker through transactional runtime rollback while
  retaining explicit compatibility for markerless legacy baselines.

### UX cleanup

- Include the v1.0.11 Home, Electricity, Cameras, and System presentation
  cleanup unchanged.

## [1.0.11] - 2026-08-11

### UX cleanup

- Clarify Home Energy period and resolution labels.
- Add a cycle-average reference line to the daily Electricity chart.
- Compact unavailable camera cards and simplify healthy System status.

## [1.0.10] - 2026-08-11

### UX Milestone 1 clarity

- Add an action-required Home summary and a dedicated Cameras view.
- Make current-cycle daily electricity usage the primary chart while keeping
  technical details collapsed.
- Reorder System health around action-required information and distinguish
  dangerous actions visually without changing backend behavior.

### Installer safety

- Make all dry-run flag combinations strictly non-mutating.

## [1.0.9] - 2026-08-11

### Electricity power integration

- Use trapezoidal integration of power samples as the authoritative energy
  source, excluding intervals longer than 15 minutes.
- Preserve raw DP17 telemetry and legacy reconciliation values while exposing
  integration coverage metadata.
- Split power intervals at tariff-effective boundaries.

## [1.0.8] - 2026-08-11

### Daily comparison semantics

- Treat zero or sub-resolution yesterday baselines as not comparable instead
  of displaying misleading extreme percentages.
- Preserve normal increases and decreases while leaving billing, tariff,
  projection, reconciliation, and history calculations unchanged.

## [1.0.7] - 2026-08-11

### Electricity reconciliation and UX

- Add optional actual MEA usage entry with server-calculated usage difference
  and variance while preserving legacy reconciliation records.
- Clarify the current-cycle projection as a current-pace estimate and keep the
  authoritative current-cycle cost visually primary.
- Present compact, responsive energy-usage and bill-amount reconciliation with
  partial-data warnings and independently editable actual values.

## [1.0.6] - 2026-08-10

### Deployment verification readiness

- Wait for the dashboard's unauthenticated authentication-status endpoint to
  become HTTP-ready before release verification begins.
- Retry bounded startup connection failures while failing immediately on
  service exit, malformed responses, or unexpected HTTP status.
- Keep the v1.0.5 application and Electricity behavior unchanged.

## [1.0.5] - 2026-08-10

### Electricity reconciliation coverage safety

- Preserve and enrich legacy reconciliation records with authoritative
  closed-cycle coverage metadata without changing stored financial, payment,
  due-date, or reminder values.
- Mark incomplete Dashboard Calculated values as partial data and carry the
  warning into difference and variance comparisons.
- Treat unavailable coverage as unavailable rather than presenting a stored
  calculated value as a complete billing-cycle result.

## [1.0.4] - 2026-08-10

### Electricity billing reconciliation

- Add persistent closed-cycle reconciliation with actual MEA bill, variance,
  payment status, due-date, audit, and deterministic reminder support.
- Redesign the Electricity summary around authoritative current billing-cycle
  cost, usage, projection, and remaining days while retaining daily detail.
- Bootstrap the latest authoritative closed cycle on first reconciliation read
  so mid-cycle deployments are immediately usable without creating an open-cycle
  record or duplicate notification.
- Distinguish complete start coverage from genuinely limited projection data
  without changing billing-cycle, tariff, history, or projection calculations.

## [1.0.3] - 2026-08-10

### Deployment source integrity

- Default runtime deployments to the directory containing the invoked
  installer, so a release worktree packages its own exact commit and version.
- Preserve explicit `APP_SRC` overrides for isolated and custom-source
  deployments.
- Add end-to-end regressions for worktree source selection, runtime version
  propagation, backend version reporting, and transactional version rollback.

### LG webOS reliability

- Include the v1.0.2 LG powered-off polling and bounded WebSocket cleanup
  hotfix. The published v1.0.2 tag remains immutable after its deployment was
  rejected by the runtime-version gate.

## [1.0.2] - 2026-08-10

### LG webOS reliability

- Treat a powered-off or unreachable TV as an expected offline state during
  status polling without changing the public API contract, command retries, or
  poll interval.
- Reduce expected TV-off timeout and WebSocket cleanup messages to debug-level
  diagnostics.
- Close WebSocket clients with bounded joins and a final forced connection
  close when the background thread does not stop normally.
- Add regressions for TV-off state, clean client teardown, and normal reconnect
  after the TV becomes reachable.

## [1.0.1] - 2026-08-07

Smart Condo Dashboard v1.0.1 supersedes the published v1.0.0 release tag with
the same application feature set and corrected production release validation.

### Deployment verification

- Aligned the release verifier with the documented camera inventory envelope,
  which exposes camera records under `cameras`.
- Added explicit checks for loaded and configured camera inventory, the stable
  Tapo public identifier, verified state, snapshot, and live-stream capability.
- Replaced human-readable journal parsing with quiet JSON journal output so an
  empty journal is not misreported as an error.
- Added regression coverage for empty, single-error, and multiple-error journal
  results.

## [1.0.0] - 2026-08-07

Smart Condo Dashboard v1.0.0 establishes the first stable production baseline.
The annotated `v1.0.0` tag identifies the verified release commit.

### Smart-home control

- Added reusable, authenticated LG webOS control with bounded client cleanup,
  connection reuse, input and installed-application discovery, compact remote
  controls, and Wake-on-LAN power-on for configured TVs.
- Added fast Sonoff/eWeLink commands with cached authentication, narrow
  per-device locking, safe concurrent state merging, stale-response rejection,
  and direct POST-response UI updates.
- Added room-centric device cards for the living room, bedroom, climate, fan,
  soundbar, TV, and known camera inventory.
- Added capability-aware camera and climate contracts that fail safely when
  configuration or a verified command provider is unavailable.
- Added the Universal IR framework and production Tapo H110 diagnostics without
  enabling unverified IR transmission or learning.
- Added MQTT presence, lighting, Home Assistant, scenes, favorites, automation,
  topology, and the safe household device registry.
- Added verified Bedroom AC power and 18–30°C temperature control through the
  official Tuya IR Cloud endpoint, with CSRF, rate limits, per-device locking,
  assumed-state persistence, and structured redacted audit records.

### Monitoring, cameras, topology, and PWA

- Added authenticated device health with normalized online/offline state,
  last-seen, response time, provider-dependent firmware/model/network metrics,
  and semantic health indicators.
- Added verified Tapo C200 ONVIF inventory and authenticated RTSP-derived JPEG
  snapshots without exposing credentials or stream URLs.
- Added explicitly opened, on-demand H.264 live view through pinned go2rtc
  v1.9.14 with loopback-only listeners and transactional provisioning.
- Added layered topology summaries, non-overlapping nodes, link states, and a
  lightweight CSS heartbeat for healthy links.
- Added verified dashboard quick actions without creating new command paths.
- Added an installable PWA shell that caches only versioned static assets and
  never caches APIs, authentication, camera media, or operational data.

### Electricity and tariffs

- Added indexed electricity-history aggregation backed by durable JSONL and a
  non-destructive SQLite sidecar.
- Added 24-hour, 7-day, 30-day, and custom Asia/Bangkok date ranges.
- Added adaptive 15-minute, 30-minute, hourly, 3-hour, and daily buckets.
- Added interval-correct consumption, cumulative-meter reset handling, missing
  data gaps, comparison metrics, analytics cards, moving averages, tooltips,
  zoom, pan, and CSV export with Excel-compatible UTF-8 BOM.
- Added official MEA tariff discovery, validation, status reporting, safe
  diagnostics, negative historical FT credit support, and clear handling when
  the official dataset is outdated.

### EPIC 17 — TP-Link dashboard integration

- Added authenticated read-only TP-Link provider status, metadata,
  capabilities, diagnostics, and camera inventory.
- Added fail-closed capability reporting and safe redaction.
- Added responsive provider cards and readable diagnostics without exposing
  credentials, addresses, vendor identifiers, or raw payloads.

### EPIC 18 — Smart Condo Control Center

- Rebuilt the home presentation as an iPad-first smart-home control center.
- Added a shared dark glass design system, semantic tokens, reusable cards,
  status chips, responsive grids, touch-friendly controls, and floating bottom
  navigation.
- Added compact hero, primary home metrics, energy summary, device,
  environment, and air-quality widgets.
- Added safe empty, loading, offline, and error states.
- Added accessible focus states, reduced-motion behavior, ARIA labels, and
  mobile safe-area handling.
- Added a query-gated preview chart mode for deterministic physical-device
  verification.

### Notifications and reliability

- Added a functional notification center with unread counts, mark-read,
  mark-all-read, delete, clear-all, deduplication, keyboard closing, and safe
  text rendering.
- Removed duplicate frontend owners, repeated polling, and duplicate
  post-command refreshes.
- Eliminated the LG webOS polling thread leak by closing and bounded-joining
  every temporary client.
- Added bounded caches, per-device command locks, deployment locks, and
  recoverable runtime replacement.
- Reused electricity history reads across summary, topology, and billing-cycle
  calculations and removed duplicate frontend summary/billing ownership.

### Security

- Dashboard sessions protect all sensitive APIs.
- State-changing routes require CSRF validation.
- Provider diagnostics expose safe projections only.
- Runtime credentials, camera URLs, client keys, tokens, account identifiers,
  IR data, and vendor device identifiers remain outside managed source.
- Runtime configuration paths prefer root-readable persistent files.

### Bug fixes through the v1.0.0 release

- Secured provider debug and runtime diagnostics routes.
- Corrected canonical tariff route reporting and stale dataset UX.
- Allowed legitimate negative historical FT credits while rejecting non-finite
  values.
- Preserved runtime-only configuration through repeated deployments.
- Fixed Sonoff command latency, duplicate login, stale cache writes, and
  multi-gang intent preservation.
- Fixed LG WOL routing on the dual-interface TinkerBoard and restored compact
  controls, applications, inputs, and low-latency command dispatch.
- Fixed electricity date ranges, aggregation, chart resolution, comparison,
  and authenticated CSV download.
- Fixed notification preview failures and TP-Link diagnostic wrapping and
  object formatting.
- Fixed PM2.5, temperature, humidity, and electricity scrubbing at both true
  endpoints by accounting for SVG `preserveAspectRatio`, CSS/viewBox scaling,
  and iPad touch coordinates.
- Restored electricity Settings hydration without duplicate mounts or fetches.
- Added bounded LG inventory retry backoff and forced websocket cleanup.
- Made IR command audits and persisted assumed state correlation-consistent.

### Deployment notes

- Production source: `/opt/smart-condo-dashboard`
- Managed runtime: `/opt/smart-condo-dashboard-run`
- Persistent state: `/root/.smart-condo-dashboard`
- Service: `smart-condo-dashboard.service`, port `8090`
- Deploy committed source only with `sudo ./install.sh --runtime-only`.
- Runtime-only deployment preserves the virtual environment and persistent
  configuration, takes an exclusive lock, verifies local-config checksums, and
  restores the previous runtime if replacement fails.
- ARMv7 deployment pins and checksum-verifies go2rtc v1.9.14, generates a
  root-only stream configuration, and rolls back its binary/config/unit/state
  together with the dashboard runtime on failure.
- Back up persistent state and `/etc/default/smart-condo-dashboard` before the
  final release tag.

### Known limitations

- v1.0.0 supports the dark theme only.
- Tapo C200 snapshot and on-demand live view require valid persistent camera
  configuration and local Camera Account credentials.
- Xiaomi camera capabilities remain Unknown; PTZ, recordings, audio, and
  motion controls remain disabled.
- Tapo H110 IR transmit and learning remain disabled without a verified,
  documented command contract.
- IR state is assumed unless a provider supplies real feedback.
- Current Safari, Chrome, Edge, and Firefox are intended targets, but a complete
  cross-browser and VoiceOver certification pass remains outstanding.
- Some hardware-dependent and Node/Playwright tests are skipped when their
  required tools or devices are unavailable.

### Rollback

The immediate pre-release production rollback commit is
`0886e10fc0911505933ac577f9c942a8fa060591`.

Do not reset a dirty production checkout. Create a clean recovery worktree at
the rollback commit, then run the guarded installer:

```sh
cd /opt/smart-condo-dashboard
git status --short
git worktree add /opt/smart-condo-dashboard-rollback \
  0886e10fc0911505933ac577f9c942a8fa060591
cd /opt/smart-condo-dashboard-rollback
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager
```

Restore persistent state only when its integrity has been verified and a state
rollback is actually required. v1.0.0 introduces no destructive state migration.
