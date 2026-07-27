# Smart Condo Dashboard

Production dashboard for the condo automation system. The canonical version is
stored in [`VERSION`](VERSION); the proposed stable release is **v1.0.0**.

## Production layout

The Git checkout and running application are deliberately separate:

- source checkout: `/opt/smart-condo-dashboard`
- managed runtime: `/opt/smart-condo-dashboard-run`
- Python environment: `/opt/smart-condo-dashboard-run/venv`
- persistent configuration and state: `/root/.smart-condo-dashboard`
- service: `smart-condo-dashboard.service`, port `8090`

Never run the service from the Git checkout and never copy the persistent root
into Git. `install.sh` snapshots committed `HEAD`, takes the deployment lock,
preserves `config/*.local.json`, replaces only managed runtime files, verifies
checksums, and leaves the virtual environment unchanged in runtime-only mode.

## Production deployment

```sh
ssh tinkerboard
cd /opt/smart-condo-dashboard
git pull --ff-only
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager -l
```

Run tests before the final command. A dirty checkout is not deployed:
`install.sh` always archives committed `HEAD`. Concurrent deployments fail
before runtime files are touched. An interrupted replacement restores the
previous managed runtime and preserved local configuration.

## Required authentication settings

Configure these in `/etc/default/smart-condo-dashboard`:

- `DASHBOARD_AUTH_USERNAME`
- `DASHBOARD_AUTH_PASSWORD_HASH` (bcrypt)
- `DASHBOARD_SESSION_SECRET`
- `DASHBOARD_COOKIE_SECURE=1` when served exclusively over HTTPS

Persistent provider paths should be explicit:

```text
CAMERA_CONFIG_FILE=/root/.smart-condo-dashboard/cameras.local.json
EWELINK_CONFIG_FILE=/root/.smart-condo-dashboard/ewelink.local.json
```

Other integration settings are documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Never commit passwords, tokens,
local keys, vendor account identifiers, camera URLs, or device IDs.

## Verification

```sh
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q
find backend -name '*.py' -print0 | xargs -0 /opt/smart-condo-dashboard-run/venv/bin/python -m py_compile
find frontend -name '*.js' -print0 | xargs -0 -n1 node --check
sh -n install.sh scripts/*.sh
git diff --check
```

See [`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md) for release,
backup, restore, troubleshooting, and rollback steps.

## Troubleshooting

```sh
sudo systemctl status smart-condo-dashboard.service --no-pager -l
sudo journalctl -u smart-condo-dashboard.service -n 200 --no-pager
sudo systemctl show smart-condo-dashboard.service \
  -p MainPID -p MemoryCurrent -p TasksCurrent -p NRestarts
sudo ./install.sh --dry-run
```

Do not delete the runtime directory to repair a failed deployment. Resolve the
reported guard failure, retain its backup path, and follow the restore checklist.
