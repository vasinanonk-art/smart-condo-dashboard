# EPIC 20 Camera Provisioning

EPIC 20 provisions production camera inventory for bounded, read-only
discovery. The verified Tapo C200 supports an authenticated snapshot and an
on-demand live view. PTZ, recordings, motion, microphone, and speaker controls
remain disabled.

## Persistent configuration

Copy the validated template without placing it in the managed runtime tree:

```sh
sudo install -o root -g root -m 0600 \
  recovery/templates/root/.smart-condo-dashboard/cameras.local.json.example \
  /root/.smart-condo-dashboard/cameras.local.json
```

Configure the service environment:

```text
CAMERA_CONFIG_FILE=/root/.smart-condo-dashboard/cameras.local.json
TAPO_C200_USERNAME=<Tapo Camera Account username>
TAPO_C200_PASSWORD=<Tapo Camera Account password>
GO2RTC_API_URL=http://127.0.0.1:1984
GO2RTC_TAPO_C200_STREAM=tapo_c200_main
```

The Tapo credentials must be created under **Advanced Settings → Camera
Account** in the Tapo application. They are camera-local ONVIF credentials,
not the TP-Link cloud account. Never put their values in the JSON file.

The go2rtc API and RTSP listeners must bind to loopback only. Its root-owned
configuration should define `tapo_c200_main` from the verified 1920×1080 ONVIF
RTSP URI and may reserve `tapo_c200_sub` for future use. The dashboard connects
only to the main stream and proxies fragmented MP4 through its authenticated
camera route; neither go2rtc management endpoints nor RTSP credentials are
browser-accessible. The camera producer starts when Live View is opened and is
released when the dialog closes or the browser disconnects.

The Xiaomi camera is intentionally configured as an unverified `auto`
candidate with no protocol ports or credentials. Until a supported protocol is
verified, it remains Unknown and no network command is attempted.

## Validation

Validate the production file without printing its contents:

```sh
sudo /opt/smart-condo-dashboard-run/venv/bin/python \
  /opt/smart-condo-dashboard/scripts/validate_camera_config.py \
  /root/.smart-condo-dashboard/cameras.local.json
```

After a deployment, use the existing authenticated dashboard client to verify:

```text
GET /api/cameras
GET /api/camera-control/devices
GET /api/devices
GET /api/device-health
```

Expected behavior:

- a successful bounded ONVIF discovery reports the Tapo camera Online;
- rejected credentials or a reachable configuration failure reports Offline;
- absent credential environment variables report Unknown;
- the unverified Xiaomi candidate reports Unknown;
- an invalid camera entry is excluded without suppressing valid entries.

These endpoints expose safe metadata only. They must not expose credential
values, hosts, stream URLs, raw serial numbers, or vendor device identifiers.
