# EPIC 20 Camera Production Readiness

Verification date: 2026-07-31  
Production service baseline: `034e718`  
EPIC 20C base commit: `5e038d2`

## Configuration status

The strict production template contains two enabled cameras:

| Camera | Provider | Configuration |
|---|---|---|
| Bedroom Camera | ONVIF | Host configured; ONVIF port 2020; RTSP port 554 recorded but not used by the ONVIF provider; credentials referenced through environment variables |
| Living Room Camera | Auto + go2rtc Xiaomi | Host and model match the verified Xiaomi cloud bridge; no local protocol ports or command capability |

Production inspection found:

- `/root/.smart-condo-dashboard/cameras.local.json`: missing
- `CAMERA_CONFIG_FILE`: missing from the service environment
- `TAPO_C200_USERNAME`: missing from the service environment
- `TAPO_C200_PASSWORD`: missing from the service environment

No secret values were read or recorded.

## Discovery status

Safe TCP reachability from the TinkerBoard:

- Bedroom Camera ONVIF port 2020: reachable
- Bedroom Camera RTSP port 554: reachable

This proves network reachability only. It does not prove ONVIF authentication
or device metadata discovery. The production service cannot complete ONVIF
verification until the persistent config, compatible ONVIF dependency, and
Tapo Camera Account variables are present.

On 2026-08-23 an isolated loopback-only go2rtc v1.9.14 trial successfully
loaded the configured `chuangmi.camera.ipc019` at `192.168.1.188`. Snapshot and
the source HEVC stream returned valid media. A follow-up browser test exposed
inconsistent HEVC parameter sets, so the isolated bridge now supplies an
on-demand 640x360 H.264 HLS compatibility stream. The isolated bridge did not
alter or restart the production dashboard or production go2rtc service.

## Expected endpoint state

After provisioning and a supported runtime deployment:

- `/api/cameras` and `/api/camera-control/devices` return the safe read-only
  camera inventory and discovery metadata.
- `/api/devices` projects Bedroom Camera from ONVIF and Living Room Camera from
  the verified named go2rtc stream.
- `/api/device-health` uses the same semantic camera state.
- Full serial numbers, credentials, profile tokens, and media URLs are not
  returned. The authenticated device-health contract may include the
  configured local IP address, consistent with EPIC 19.

Living Room snapshot and live stream are read-only. Xiaomi PTZ, recording,
motion, speaker, and microphone operations remain disabled.

## Remaining unknowns

1. Tapo Camera Account credentials have not been supplied.
2. ONVIF authentication and metadata enumeration have not been exercised
   against the production C200.
3. A compatible ONVIF client dependency must be present in the runtime.
4. Xiaomi requires cloud reachability when go2rtc obtains stream encryption
   keys, even though the media connection is local.
5. Xiaomi H.264 compatibility transcoding uses roughly one CPU core while its
   Live View is open; it is not suitable for multiple concurrent viewers.

## Recommended next actions

1. Create the Tapo Camera Account in the official Tapo application.
2. Add the three camera environment settings documented in
   `EPIC_20_CAMERA_PROVISIONING.md` without printing their values.
3. Install the validated config as root-owned mode `0600`.
4. Confirm the runtime contains the compatible ONVIF dependency.
5. Deploy through the protected runtime-only workflow.
6. Use an authenticated dashboard session to verify the four read-only
   endpoints.
7. Verify Xiaomi snapshot and H.264 HLS through the authenticated dashboard,
   while confirming PTZ and all other Xiaomi command capabilities stay absent.
