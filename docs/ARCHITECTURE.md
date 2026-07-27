# Production Architecture

## Process and storage

`smart-condo-dashboard.service` runs Uvicorn on port 8090 from
`/opt/smart-condo-dashboard-run` using its existing `venv`. The source checkout
at `/opt/smart-condo-dashboard` is deployment input only. Persistent state lives
under `/root/.smart-condo-dashboard`; deployment must never remove it.

The FastAPI application is assembled by `backend.app_entry`. Its ordered runtime
modules include compatibility layers that still own or replace routes. Their
order is contract-tested and must not be collapsed without a separate migration.

## Data flows

- Browser requests authenticated APIs and supplies CSRF on writes.
- MQTT carries presence, sensors, LG gateway messages, and configured device
  integrations.
- LG control uses a reusable WebOS connection; WOL is isolated to power-on.
- Electricity samples are durable JSONL. An indexed SQLite sidecar aggregates
  requested time ranges without changing the durable format.
- Camera and eWeLink configuration paths prefer environment variables, with
  legacy local-file fallbacks.

## Configuration reference

Required security: `DASHBOARD_AUTH_USERNAME`, `DASHBOARD_AUTH_PASSWORD_HASH`,
`DASHBOARD_SESSION_SECRET`, and (for HTTPS) `DASHBOARD_COOKIE_SECURE`.

Runtime paths: `SMART_CONDO_DATA_DIR`, `CAMERA_CONFIG_FILE`,
`EWELINK_CONFIG_FILE`, `CLIMATE_STATE_FILE`, `ELECTRICITY_HISTORY_PATH`,
`ELECTRICITY_HISTORY_DB_PATH`, and `SENSOR_HISTORY_PATH`.

Connectivity: `MQTT_HOST`, `MQTT_PORT`, `MQTT_CMD_TOPIC`, `MQTT_STATE_TOPIC`,
`HA_BASE_URL`, `HA_TOKEN`, `LG_TV_IP`, `LG_TV_MAC`, and provider-specific
settings. Secrets such as `LG_TV_CLIENT_KEY`, `TUYA_METER_LOCAL_KEY`,
`TAPO_IR_PASSWORD`, and tokens belong only in root-readable runtime
configuration.

Timeout, cache, retention, and rate-limit environment variables are optional.
Use code defaults unless an operational requirement has been validated.

## Background work

The process has bounded pollers for base device state, Home Assistant, presence,
electricity, automation triggers, maintenance, and LG status. Browser dashboard
refresh is 15 seconds; settings polling runs only while Settings is open.
Provider caches are TTL- or size-bounded.

## Security boundary

Only login/status/static routes are public. API writes require session
authentication and CSRF. Diagnostic routes remain authenticated and must return
safe projections, never raw provider payloads or filesystem contents.
