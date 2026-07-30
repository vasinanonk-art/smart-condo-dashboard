# Smart Condo Dashboard v1.0.0

## Overview

v1.0.0 is the first stable production baseline for the Smart Condo Dashboard.
It combines authenticated smart-home control, household monitoring,
electricity analytics, provider-safe diagnostics, an iPad-first control-center
interface, and recoverable runtime deployment.

Production commit:
`6e319ae37a1797430c26cb7eb6c82104205a2abd`

Recommended tag: `v1.0.0`

## Highlights

- Premium dark control-center experience optimized for wall-mounted iPad use.
- Secure LG webOS and Wake-on-LAN control.
- Low-latency, concurrency-safe Sonoff/eWeLink control.
- Electricity history, analytics, comparisons, custom ranges, adaptive
  resolution, and CSV export.
- Official MEA tariff monitoring with stale-dataset awareness.
- Room-centric device, climate, camera, presence, and automation presentation.
- Functional notification center.
- Runtime-only deployment with exclusive locking, checksum-protected
  configuration preservation, and rollback.

## EPIC 17 — TP-Link read-only dashboard provider integration

EPIC 17 integrates the EPIC 15/16 TP-Link provider foundation into the
authenticated dashboard:

- provider health
- provider metadata and implementation status
- explicit capability matrix
- bounded safe diagnostics
- redacted camera inventory
- clear `Not Supported` presentation

No cloud login, operational camera command, snapshot, streaming, PTZ, recording,
scene, or undocumented API was activated.

## EPIC 18 — Smart Condo Control Center redesign

EPIC 18 replaces the administration-first home presentation with a reusable,
iPad-first smart-home design system:

- compact utility bar and hero
- four primary household metrics
- energy feature widget
- device, environment, and air-quality cards
- semantic glass surfaces and status chips
- responsive 12-column layout
- touch-friendly floating navigation
- safe-area support
- focus-visible and reduced-motion behavior
- designed loading, empty, offline, warning, and error states

v1.0.0 is dark-theme only.

## Hotfixes

- Corrected TP-Link diagnostic wrapping, timestamp formatting, and object
  presentation.
- Corrected chart endpoint scrubbing for PM2.5, temperature, humidity, and
  electricity on scaled SVGs and iPad Safari.
- Added deterministic, query-gated preview chart data for physical endpoint
  verification.
- Fixed authenticated electricity CSV download and adaptive chart buckets.
- Fixed LG Wake-on-LAN delivery on the production dual-interface host.
- Restored LG inputs, installed applications, navigation, playback, and compact
  layout while preserving connection reuse.
- Eliminated LG websocket thread accumulation.
- Reduced Sonoff command latency and protected concurrent state updates.
- Protected persistent camera/eWeLink configuration from managed deployments.
- Improved stale official MEA dataset status without changing tariff
  calculation or automatic apply behavior.

## Performance

- Frontend polling owners are deduplicated.
- Successful device commands consume POST responses without duplicate GETs.
- LG commands reuse the authenticated webOS connection.
- Sonoff authentication and state are cached with narrow per-device locks.
- Electricity queries use requested-range aggregation through an indexed SQLite
  sidecar while JSONL remains the durable source.
- Provider caches, queues, retries, and timeouts are bounded.
- Production startup after the final chart deployment used approximately
  66 MB with 16 tasks; a final ten-minute release soak remains recommended.

## Security

- Sensitive APIs require an authenticated dashboard session.
- POST, PUT, PATCH, and DELETE routes require CSRF validation.
- Diagnostic endpoints remain authenticated.
- Safe provider projections exclude credentials, tokens, account identifiers,
  local keys, client keys, RTSP URLs, MAC addresses, IR data, and vendor device
  identifiers.
- Runtime secrets and persistent configuration remain outside the managed Git
  runtime.
- Preview chart mode is read-only, query-gated, and does not write storage or
  change backend APIs.

## Deployment

The source checkout and managed runtime are separate:

```text
/opt/smart-condo-dashboard
/opt/smart-condo-dashboard-run
/root/.smart-condo-dashboard
```

Deploy only through `install.sh`. The installer snapshots committed HEAD,
acquires an exclusive lock, preserves local configuration generically, verifies
checksums, replaces managed files, retains the venv in runtime-only mode, and
restores the prior runtime after an interrupted replacement.

## Upgrade procedure

```sh
ssh tinkerboard
cd /opt/smart-condo-dashboard
git status --short
git pull --ff-only origin main
test "$(git rev-parse HEAD)" = \
  "6e319ae37a1797430c26cb7eb6c82104205a2abd"
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager -l
curl -sS -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:8090/
curl -sS -o /dev/null -w "auth=%{http_code}\n" \
  http://127.0.0.1:8090/api/auth/status
sudo journalctl -u smart-condo-dashboard.service -n 100 --no-pager
```

Expected HTTP results are `home=303` and `auth=200`.

## Rollback procedure

The immediate rollback commit is
`eccffee98b21a87f3674b4f14e631299badf2948`.

Do not destructively reset a dirty source checkout:

```sh
cd /opt/smart-condo-dashboard
git worktree add /opt/smart-condo-dashboard-rollback \
  eccffee98b21a87f3674b4f14e631299badf2948
cd /opt/smart-condo-dashboard-rollback
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
```

Verify service and HTTP health after rollback. Persistent data restoration is
not normally required because v1.0.0 performs no destructive migration.

## Known limitations

- Dark theme only.
- Persistent camera configuration is currently absent in production.
- TP-Link camera capabilities beyond inventory and health are disabled.
- Tapo H110 transmit and learning are disabled pending a verified contract.
- Camera protocols and capabilities are never inferred from known model names.
- IR state is assumed when no feedback source exists.
- Full browser-matrix, VoiceOver, and ten-minute idle-soak evidence should be
  completed before publishing the release tag.
- Hardware- and tool-dependent tests may be skipped when the required device,
  Node, or Playwright is unavailable.

## Future roadmap

- EPIC 19: Live Device Monitoring. Milestone 1 is implemented after the
  v1.0.0 production baseline in commit `9380848`; event timeline work remains.
- EPIC 20: Theme System
- EPIC 21: Camera Integration
- EPIC 22: Automation

See [`ROADMAP.md`](ROADMAP.md) for planned scope.

## Release Summary

- Current version: `1.0.0`
- Production commit: `6e319ae37a1797430c26cb7eb6c82104205a2abd`
- Recommended tag: `v1.0.0`
- Known risks: dark-only UI, absent camera configuration, read-only TP-Link
  provider, unverified Tapo IR transmission, incomplete browser/VoiceOver
  certification
- Outstanding verification: final backup, dry-run evidence, Python and shell
  verification reports, browser console audit, browser matrix, accessibility
  audit, and ten-minute production soak
- Post-baseline status: EPIC 19 Milestone 1 is implemented in `9380848` and is
  intentionally excluded from the recommended v1.0.0 tag at `6e319ae`
