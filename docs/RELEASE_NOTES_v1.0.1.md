# Smart Condo Dashboard v1.0.1

Release date: **2026-08-07**

## Overview

v1.0.1 is the final production release of the Smart Condo Dashboard v1.0
feature set. It supersedes the published v1.0.0 tag without rewriting that tag.
Application behavior is unchanged from v1.0.0; this patch corrects and tests
the production release-verification pipeline.

## Deployment verification fixes

- The authenticated camera inventory verifier now reads the documented
  `cameras` response field and validates configuration state, the stable Tapo
  public ID, verification status, snapshot capability, and live capability.
- Journal error checks now use `journalctl --quiet --output=json` and count only
  real JSON journal records for `smart-condo-dashboard.service` and
  `smart-condo-go2rtc.service`.
- Empty journals are correctly treated as zero errors. Regression tests cover
  empty, single-error, and multiple-error results.

## Upgrade

After backing up the runtime, environment, and persistent state, deploy from a
clean checkout of the annotated `v1.0.1` tag:

```sh
sudo ./install.sh --runtime-only
```

The installer preserves the managed virtual environment, local configuration,
camera credentials, and persistent state. It provisions the pinned go2rtc
gateway transactionally with loopback-only listeners.

## Rollback

The verified pre-release rollback commit remains:

```text
0886e10fc0911505933ac577f9c942a8fa060591
```

Use a clean detached worktree at that commit and run the supported runtime-only
installer. Do not reset or overwrite a dirty production checkout.

## Known limitations

The application limitations documented in
[`RELEASE_NOTES_v1.0.0.md`](RELEASE_NOTES_v1.0.0.md) are unchanged. In
particular, the theme remains dark-only, Xiaomi camera capabilities remain
unverified, camera PTZ remains disabled, and Tapo H110 IR transmission remains
disabled without a verified command contract.
