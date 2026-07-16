# Electricity tariff configuration

The dashboard calculates estimates only from `ELECTRICITY_TARIFF_CONFIG_JSON`. It does not ship with or claim any official current utility tariff.

## Interactive helper

Run on TinkerBoard:

```bash
/opt/smart-condo-dashboard-run/venv/bin/python \
  /opt/smart-condo-dashboard/scripts/generate_electricity_tariff_config.py
```

The helper validates the effective date, progressive tier ordering, nonnegative numeric values, final unlimited tier, and VAT range. It prints one compact environment line. It does not edit `/etc/default` automatically.

Example structure:

```json
{
  "tariff_name": "Configured residential tariff",
  "effective_date": "2026-07-01",
  "tiers": [
    {"up_to_kwh": 15, "rate": 2.3488},
    {"up_to_kwh": 25, "rate": 2.9882},
    {"up_to_kwh": null, "rate": 4.4217}
  ],
  "ft_rate": 0.0,
  "service_charge": 0.0,
  "vat_percent": 7.0,
  "minimum_charge": 0.0
}
```

Set the compact JSON value in the existing service environment file without deleting existing values, then restart the service.

Validation rules:

- `effective_date` must use `YYYY-MM-DD`.
- Tier thresholds must increase strictly.
- The final unlimited tier uses `null`.
- Rates, Ft, service charge, and minimum charge must be nonnegative.
- VAT must be between 0 and 100.
- Invalid or missing configuration disables billing without stopping the dashboard.

Read-only status:

```text
GET /api/electricity/tariff/status
```

## History configuration

```text
ELECTRICITY_HISTORY_RETENTION_DAYS=400
ELECTRICITY_HISTORY_PATH=/root/.smart-condo-dashboard/electricity_history.jsonl
ELECTRICITY_HISTORY_MAX_GAP_SEC=900
```

The history API reports the actual first and last stored sample, requested duration, available duration, coverage percentage, and whether the requested range is complete. It never fabricates samples before collection began.

The history file stores only timestamp, voltage, current, power, total energy, source, and health. It does not store credentials, Tuya Local Key, raw DPS, MQTT credentials, or authentication data.

## Optional legitimate backfill investigation

The one-time helper checks an existing `sensor_history.jsonl` and, when Home Assistant plus explicit electricity entity mappings are already configured, the Home Assistant recorder history endpoint.

Dry-run only:

```bash
/opt/smart-condo-dashboard-run/venv/bin/python \
  /opt/smart-condo-dashboard/scripts/import_electricity_history.py
```

Apply only reviewed, non-duplicate rows:

```bash
/opt/smart-condo-dashboard-run/venv/bin/python \
  /opt/smart-condo-dashboard/scripts/import_electricity_history.py --apply
```

Home Assistant imports are marked `home_assistant_import`; existing timestamps are skipped. No cloud API is enabled automatically. When no legitimate source is available, the helper reports `no_backfill_source_available`.
