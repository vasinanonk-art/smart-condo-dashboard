# Production Checklist

## Before release

1. Confirm a clean source checkout and the intended commit.
2. Run the complete Python suite on the TinkerBoard runtime Python.
3. Run Python compilation, JavaScript syntax, shell syntax, and
   `git diff --check`.
4. Run `sudo ./install.sh --dry-run`.
5. Confirm persistent camera/eWeLink/settings files and a recent backup.
6. Record the previous production commit for rollback.
7. Create the release tag only after production verification. For v1.0.0:
   `git tag -a v1.0.0 -m "Smart Condo Dashboard v1.0.0"`.

## Backup

As root, stop writes or take a consistent filesystem snapshot, then archive:

- `/root/.smart-condo-dashboard`
- `/etc/default/smart-condo-dashboard`
- the current Git commit and `/opt/smart-condo-dashboard-run` version

Store the archive outside both source and runtime directories with mode 0600.
Do not print or check its contents into Git.

## Restore

1. Restore persistent files as `root:root`, mode 0600 for secret configuration.
2. Restore `/etc/default/smart-condo-dashboard`.
3. Fast-forward or check out the recorded source commit in a separate recovery
   checkout; do not destructively reset a dirty production checkout.
4. Run `sudo ./install.sh --dry-run`, then `sudo ./install.sh --runtime-only`.
5. Verify authenticated health and read-only integration status.

## Interrupted deployment

The deployment trap restores the prior managed runtime and local configuration.
If it reports a retained backup directory, do not rerun deployment until that
backup and runtime integrity are inspected. The stable flock prevents overlap.

## Release verification command

```sh
cd /opt/smart-condo-dashboard
test "$(cat VERSION)" = "1.0.0" &&
git diff --check &&
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q &&
find backend -name '*.py' -print0 | xargs -0 /opt/smart-condo-dashboard-run/venv/bin/python -m py_compile &&
find frontend -name '*.js' -print0 | xargs -0 -n1 node --check &&
sh -n install.sh scripts/*.sh
```

## Post-deployment read-only checks

- service is active; restart count unchanged
- authenticated `/api/health` contains no secrets or paths
- LG capabilities/inventory load without sending a command
- electricity 24h, 7d, 30d, custom history, comparisons, and CSV respond
- MQTT presence updates and background thread count remains stable

## Rollback

Use the commit immediately before the release commits (recorded in the release
report), then run the same dry-run and runtime-only installer. Restore persistent
state only if its format or content changed; v1.0.0 preparation performs no
destructive migration.
