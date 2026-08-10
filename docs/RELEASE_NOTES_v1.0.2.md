# Smart Condo Dashboard v1.0.2

Release date: **2026-08-10**

## Overview

v1.0.2 is a narrow LG webOS reliability hotfix. It contains no feature, API,
poll-interval, command-retry, camera, go2rtc, topology, or Quick Actions change.

## LG webOS hotfix

- Powered-off, unreachable, and status-timeout conditions are represented as
  the existing offline/unavailable state without an application error.
- Expected TV-off polling and client-close failures are debug-level diagnostics
  instead of repeated informational or warning log entries.
- WebSocket teardown performs a bounded normal close and join, followed by a
  bounded forced connection close only when the background thread remains alive.
- The next scheduled status poll reconnects normally when the TV is reachable.

## Verification

- Focused LG suite: `38 passed`.
- Full TinkerBoard suite: `685 passed, 52 skipped, 0 failed`.
- Production release verifier passed for authentication, camera inventory,
  snapshot, live proxy, topology, electricity, Quick Actions, journals, and
  loopback-only go2rtc listeners.
- Runtime-only installer dry-run preserved local camera and Sonoff configuration
  and validated pinned go2rtc inputs.

## Upgrade

After creating and verifying a current production backup, deploy from a clean
checkout of the annotated `v1.0.2` tag:

```sh
sudo ./install.sh --runtime-only
```

## Rollback

The immediate rollback commit is:

```text
13fdea03a3836bbb00ee374004eada09e9d93124
```

Persistent-data rollback is not required because this release has no schema or
state migration.
