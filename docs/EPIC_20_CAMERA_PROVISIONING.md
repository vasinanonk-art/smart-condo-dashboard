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
```

The Tapo credentials must be created under **Advanced Settings → Camera
Account** in the Tapo application. They are camera-local ONVIF credentials,
not the TP-Link cloud account. Never put their values in the JSON file.

Runtime-only deployment provisions the pinned official go2rtc v1.9.14 ARM
artifact on ARMv7 after validating its SHA-256 checksum. The installer reads
the existing environment file as data (it never shell-sources it), performs a
bounded read-only ONVIF profile lookup, and writes the resulting main-stream
source to `/etc/smart-condo-dashboard/go2rtc.yaml` as `root:root` mode `0600`.
The binary is installed outside Git at
`/usr/local/lib/smart-condo-dashboard/go2rtc` and managed by the dedicated
`smart-condo-go2rtc.service` unit.
The service receives only the config-file path as an argument and suppresses
go2rtc process output so an upstream error cannot echo the credential-bearing
source URI into the journal.

The go2rtc API and RTSP listeners bind only to `127.0.0.1:1984` and
`127.0.0.1:8554`; WebRTC listening is disabled. The dashboard connects
only to the main stream and proxies fragmented MP4 through its authenticated
camera route; neither go2rtc management endpoints nor RTSP credentials are
browser-accessible. The camera producer starts when Live View is opened and is
released when the dialog closes or the browser disconnects.

The public camera ID is derived from the persistent schema and is currently
`tapo-c220` (a compatibility identifier retained for the physical C200). Its
routes are `/api/camera-control/tapo-c220/snapshot` and
`/api/camera-control/tapo-c220/live`.

Provisioning is transactional with the managed runtime. Failure restores the
previous binary, root-only config, systemd unit and active/enabled state before
the existing installer restores the previous dashboard runtime. Reinstalling
the same binary, config and unit does not restart go2rtc.

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
