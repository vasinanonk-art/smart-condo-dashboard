# Smart Condo Dashboard

Smart Condo Dashboard v1.0.1 is the stable production control center for the
condo automation system. The canonical version is stored in [`VERSION`](VERSION).
The annotated `v1.0.1` tag identifies the final verified release commit;
`v1.0.0` remains an immutable superseded release candidate.

## Project status

- Version: **1.0.1**
- Status: v1.0.1 release prepared
- Theme: dark only in v1.0.x
- Primary UI: iPad-first responsive smart-home control center
- Backend: FastAPI/Uvicorn on the TinkerBoard
- Authentication: dashboard session with CSRF protection for writes
- Monitoring: live device health with semantic status and optional metrics

Release notes are in
[`docs/RELEASE_NOTES_v1.0.1.md`](docs/RELEASE_NOTES_v1.0.1.md). Planned work is
tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Supported platforms

### Server

- TinkerBoard Linux production host
- Python environment managed at `/opt/smart-condo-dashboard-run/venv`
- systemd service `smart-condo-dashboard.service`
- local service port `8090`

### Clients

- iPadOS Safari in landscape and portrait; landscape is the primary target
- current desktop Safari, Chrome, Edge, and Firefox
- TinkerBoard-connected touch displays using a current Chromium-class browser

Older browsers without modern JavaScript, CSS Grid, custom properties, and SVG
pointer-event support are not supported. A complete cross-browser accessibility
certification pass remains a release follow-up.

## Supported devices and integrations

- LG webOS TV: status, pairing, reusable control connection, compact remote,
  installed apps, live inputs, playback, volume, navigation, power-off, and
  configured Wake-on-LAN power-on
- Sonoff/eWeLink switches: discovered/static mappings, multi-gang state,
  authenticated cloud commands, and background reconciliation
- Lamptan/Tuya and Home Assistant lighting where configured
- PJ1103/electricity meter history, analytics, billing-cycle estimates, and MEA
  tariff status
- MQTT presence and sensors
- Home Assistant PM2.5 and configured automation inventory
- household scenes, favorites, topology, and device registry
- TP-Link provider and camera inventory: authenticated and read-only
- Tapo C200: verified ONVIF metadata, authenticated snapshot, and explicitly
  opened on-demand local live view; PTZ remains disabled
- Bedroom AC: verified Tuya IR Cloud power and target-temperature controls with
  assumed-state labeling and structured audit records
- Tapo H110: bridge/inventory diagnostics only; unverified IR transmission is
  disabled
- configured camera-control providers where persistent camera configuration is
  available

Unsupported capabilities are reported explicitly and fail closed. The dashboard
does not invent device functions, camera protocols, IR mappings, or feedback.

## Theme

v1.0.1 ships with the dark glass control-center theme only. A light theme,
theme switch, and saved user preference are planned for EPIC 20.

## Preview chart mode

Deterministic chart data can be enabled explicitly for development and physical
touch verification:

```text
http://<preview-host>:8090/?previewChartData=1
```

Preview mode supplies 24 query-gated samples for temperature, humidity, PM2.5,
and electricity, including internal gaps and identifiable endpoints. It is
inactive when the parameter is absent and does not change production APIs or
stored data. Never use preview values as operational readings.

## Production layout

The Git checkout and running application are deliberately separate:

- source checkout: `/opt/smart-condo-dashboard`
- managed runtime: `/opt/smart-condo-dashboard-run`
- Python environment: `/opt/smart-condo-dashboard-run/venv`
- persistent configuration and state: `/root/.smart-condo-dashboard`
- environment file: `/etc/default/smart-condo-dashboard`
- service: `smart-condo-dashboard.service`
- camera gateway: `smart-condo-go2rtc.service`, loopback only

Never run the service from the Git checkout and never copy persistent state into
Git. Runtime-only deployment snapshots committed `HEAD`, takes an exclusive
lock, preserves and verifies `config/*.local.json`, replaces managed runtime
files, leaves the virtual environment unchanged, and restores the prior runtime
after an interrupted replacement.

## Production installation and upgrade

```sh
ssh tinkerboard
cd /opt/smart-condo-dashboard
git status --short
git pull --ff-only origin main
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q
sudo ./install.sh --dry-run
sudo ./install.sh --runtime-only
sudo systemctl status smart-condo-dashboard.service --no-pager -l
curl -sS -o /dev/null -w "home=%{http_code}\n" http://127.0.0.1:8090/
curl -sS -o /dev/null -w "auth=%{http_code}\n" \
  http://127.0.0.1:8090/api/auth/status
```

Expected unauthenticated checks are `home=303` and `auth=200`. Do not deploy a
dirty checkout, modify the runtime virtual environment, or use direct
`rsync --delete`.

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

Never commit passwords, tokens, local keys, vendor account identifiers, camera
URLs, IR data, device identifiers, MAC addresses, or client keys.

## Verification

```sh
test "$(cat VERSION)" = "1.0.1"
/opt/smart-condo-dashboard-run/venv/bin/python -m pytest -q
find backend -name '*.py' -print0 | \
  xargs -0 /opt/smart-condo-dashboard-run/venv/bin/python -m py_compile
find frontend -name '*.js' -print0 | xargs -0 -n1 node --check
sh -n install.sh scripts/*.sh
git diff --check
```

## Documentation

- [Production architecture](docs/ARCHITECTURE.md)
- [Production checklist](docs/PRODUCTION_CHECKLIST.md)
- [v1.0.1 release notes](docs/RELEASE_NOTES_v1.0.1.md)
- [v1.0.0 release notes](docs/RELEASE_NOTES_v1.0.0.md)
- [Roadmap](docs/ROADMAP.md)
- [EPIC 19 device monitoring](docs/EPIC_19_DEVICE_MONITORING.md)
- [EPIC 20 camera provisioning](docs/EPIC_20_CAMERA_PROVISIONING.md)
- [EPIC 21 Smart Life IR discovery](docs/EPIC_21_SMARTLIFE_IR_DISCOVERY.md)
- [TP-Link connector](docs/TPLINK_CONNECTOR.md)
- [Universal IR framework](docs/IR_FRAMEWORK.md)
- [Electricity tariff configuration](docs/electricity_tariff_configuration.md)

## Troubleshooting

```sh
sudo systemctl status smart-condo-dashboard.service --no-pager -l
sudo journalctl -u smart-condo-dashboard.service -n 200 --no-pager
sudo systemctl show smart-condo-dashboard.service \
  -p MainPID -p MemoryCurrent -p TasksCurrent -p NRestarts
sudo ./install.sh --dry-run
```

Do not delete the runtime directory to repair a failed deployment. Inspect the
retained backup, resolve the guard failure, and follow the documented restore or
rollback procedure.
