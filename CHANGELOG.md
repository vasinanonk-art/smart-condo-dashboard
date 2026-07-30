# Changelog

## [Unreleased]

### EPIC 19 — Live Device Monitoring, Milestone 1

- Added an authenticated read-only device-health endpoint.
- Added in-memory heartbeat tracking, online/offline normalization, last-seen
  timestamps, and provider response-time measurement.
- Added a design-system Device Health card with green/yellow/red health
  indicators and one bounded frontend poller.
- Added unit, authentication, frontend-runtime, and full-suite regression
  coverage in commit `9380848`.

EPIC 19 Milestone 1 is implemented on `main` but is not part of the v1.0.0
production baseline at `6e319ae`.

## [1.0.0] - 2026-07-30

Smart Condo Dashboard v1.0.0 establishes the first stable production baseline.
The verified production source commit is
`6e319ae37a1797430c26cb7eb6c82104205a2abd`. The annotated `v1.0.0` tag is
prepared but has not yet been created.

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

### Security

- Dashboard sessions protect all sensitive APIs.
- State-changing routes require CSRF validation.
- Provider diagnostics expose safe projections only.
- Runtime credentials, camera URLs, client keys, tokens, account identifiers,
  IR data, and vendor device identifiers remain outside managed source.
- Runtime configuration paths prefer root-readable persistent files.

### Bug fixes through `6e319ae`

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

### Deployment notes

- Production source: `/opt/smart-condo-dashboard`
- Managed runtime: `/opt/smart-condo-dashboard-run`
- Persistent state: `/root/.smart-condo-dashboard`
- Service: `smart-condo-dashboard.service`, port `8090`
- Deploy committed source only with `sudo ./install.sh --runtime-only`.
- Runtime-only deployment preserves the virtual environment and persistent
  configuration, takes an exclusive lock, verifies local-config checksums, and
  restores the previous runtime if replacement fails.
- Back up persistent state and `/etc/default/smart-condo-dashboard` before the
  final release tag.

### Known limitations

- v1.0.0 supports the dark theme only.
- Camera cards remain configuration-unavailable until persistent camera
  configuration is restored.
- TP-Link camera integration is read-only; snapshot, streaming, PTZ,
  recordings, audio, and motion features remain disabled.
- Tapo H110 IR transmit and learning remain disabled without a verified,
  documented command contract.
- IR state is assumed unless a provider supplies real feedback.
- Current Safari, Chrome, Edge, and Firefox are intended targets, but a complete
  cross-browser and VoiceOver certification pass remains outstanding.
- Some hardware-dependent and Node/Playwright tests are skipped when their
  required tools or devices are unavailable.

### Rollback

The immediate pre-release production commit is
`eccffee98b21a87f3674b4f14e631299badf2948`.

Do not reset a dirty production checkout. Create a clean recovery worktree at
the rollback commit, then run the guarded installer:

```sh
cd /opt/smart-condo-dashboard
git status --short
git worktree add /opt/smart-condo-dashboard-rollback \
  eccffee98b21a87f3674b4f14e631299badf2948
cd /opt/smart-condo-dashboard-rollback
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager
```

Restore persistent state only when its integrity has been verified and a state
rollback is actually required. v1.0.0 introduces no destructive state migration.
