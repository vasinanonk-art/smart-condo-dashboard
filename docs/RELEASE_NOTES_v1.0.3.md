# Smart Condo Dashboard v1.0.3

Release date: **2026-08-10**

## Overview

v1.0.3 contains the LG webOS powered-off polling hotfix from v1.0.2 and fixes
runtime deployment source selection. The published v1.0.2 tag remains immutable;
its production deployment was rejected and rolled back because the installer
packaged the stale default checkout instead of the invoked release worktree.

## Fixes

- Treat powered-off or unreachable LG TVs as expected offline/unavailable state
  without changing the API contract, poll interval, or command retries.
- Close LG WebSocket clients with bounded joins and forced connection cleanup
  only when the background thread remains alive.
- Resolve the default deployment source from the invoked `install.sh` location.
- Preserve explicit `APP_SRC` overrides and transactional rollback of runtime
  code, version metadata, and local configuration.
- Retain the v1.0.1 camera-envelope, JSON journal, and loopback-listener release
  verifier fixes.

## Deployment

After current backup and preflight gates pass, deploy only from a clean checkout
of the annotated `v1.0.3` tag:

```sh
sudo ./install.sh --runtime-only
```

The immediate rollback commit remains:

```text
13fdea03a3836bbb00ee374004eada09e9d93124
```

This release has no persistent-state or schema migration.
