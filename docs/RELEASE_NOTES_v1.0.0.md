# Smart Condo Dashboard v1.0.0

## Overview

v1.0.0 is the first stable production baseline for the Smart Condo Dashboard.
It combines authenticated smart-home control, household monitoring,
electricity analytics, provider-safe diagnostics, an iPad-first control-center
interface, and recoverable runtime deployment.

Release tag: `v1.0.0`

## Highlights

- Premium dark control-center experience optimized for wall-mounted iPad use.
- Secure LG webOS and Wake-on-LAN control.
- Low-latency, concurrency-safe Sonoff/eWeLink control.
- Electricity history, analytics, comparisons, custom ranges, adaptive
  resolution, and CSV export.
- Official MEA tariff monitoring with stale-dataset awareness.
- Room-centric device, climate, camera, presence, and automation presentation.
- Functional notification center.
- Live device-health monitoring and semantic status presentation.
- Verified Tapo C200 snapshot and explicitly opened local live view.
- Audited Bedroom AC power and target-temperature control.
- Layered topology, lightweight healthy-link heartbeat, and verified quick
  actions.
- Secure installable PWA shell for trusted HTTPS origins.
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

The verified Bedroom Tapo C200 provides authenticated, backend-proxied JPEG
snapshots and an explicitly opened on-demand H.264 live view. The live gateway
uses pinned go2rtc v1.9.14 with loopback-only API/RTSP listeners and root-only
credentials. PTZ, recording, motion, microphone, speaker, scenes, and
undocumented APIs remain disabled; the Xiaomi camera remains Unknown.

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
- Restored electricity Settings hydration and removed global billing-cycle
  polling.
- Kept IR audit records and persisted assumed state consistent by correlation
  ID.

## Performance

- Frontend polling owners are deduplicated.
- Successful device commands consume POST responses without duplicate GETs.
- LG commands reuse the authenticated webOS connection.
- Sonoff authentication and state are cached with narrow per-device locks.
- Electricity queries use requested-range aggregation through an indexed SQLite
  sidecar while JSONL remains the durable source.
- Provider caches, queues, retries, and timeouts are bounded.
- Electricity summary, topology, and billing-cycle processing reuse history
  reads and single-flight caches instead of repeatedly parsing JSONL.
- LG inventory failure retries use bounded backoff and websocket threads receive
  bounded close and force-close cleanup.

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

On ARMv7, runtime-only deployment also installs the checksum-pinned official
go2rtc v1.9.14 binary, generates a root-only stream configuration, and manages
`smart-condo-go2rtc.service`. A failure restores the prior binary, config,
systemd unit/state, and dashboard runtime. API and RTSP listeners remain bound
to loopback; WebRTC listening is disabled.

## Upgrade procedure

```sh
ssh tinkerboard
cd /opt/smart-condo-dashboard
git status --short
git fetch --tags origin
git worktree add --detach /opt/smart-condo-dashboard-v1.0.0 v1.0.0
cd /opt/smart-condo-dashboard-v1.0.0
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
`0886e10fc0911505933ac577f9c942a8fa060591`.

Do not destructively reset a dirty source checkout:

```sh
cd /opt/smart-condo-dashboard
git worktree add /opt/smart-condo-dashboard-rollback \
  0886e10fc0911505933ac577f9c942a8fa060591
cd /opt/smart-condo-dashboard-rollback
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
```

Verify service and HTTP health after rollback. Persistent data restoration is
not normally required because v1.0.0 performs no destructive migration.

## Known limitations

- Dark theme only.
- Tapo snapshot/live requires persistent configuration, Camera Account
  credentials, local camera reachability, FFmpeg, ONVIF, and go2rtc.
- Xiaomi remains Unknown; PTZ, recording, motion, microphone, and speaker are
  disabled.
- Tapo H110 transmit and learning are disabled pending a verified contract.
- Camera protocols and capabilities are never inferred from known model names.
- IR state is assumed when no feedback source exists.
- Installed PWA operation requires a trusted HTTPS origin; plain ZeroTier HTTP
  is not a secure context.
- Full browser-matrix and VoiceOver certification remain future work.
- Hardware- and tool-dependent tests may be skipped when the required device,
  Node, or Playwright is unavailable.

## Future roadmap

- Device event timeline and richer monitoring history.
- Light theme and saved theme preference.
- Verified Xiaomi camera integration and bounded PTZ only if supported.
- Expanded scenes and automation only through verified command contracts.

See [`ROADMAP.md`](ROADMAP.md) for planned scope.

## Release Summary

- Current version: `1.0.0`
- Release commit: the commit referenced by annotated tag `v1.0.0`
- Known risks: dark-only UI, trusted HTTPS required for PWA installation,
  Xiaomi capabilities unknown, unverified Tapo H110 IR transmission, and
  incomplete browser/VoiceOver certification
- Release validation: 674 tests passed on the TinkerBoard candidate; 52 tests
  were skipped only because Node/Playwright were unavailable there, with
  JavaScript syntax validated separately.
