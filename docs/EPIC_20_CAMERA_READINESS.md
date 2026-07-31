# EPIC 20 Camera Production Readiness

Verification date: 2026-07-31  
Production service baseline: `034e718`  
EPIC 20C base commit: `5e038d2`

## Configuration status

The strict production template contains two enabled cameras:

| Camera | Provider | Configuration |
|---|---|---|
| Bedroom Camera | ONVIF | Host configured; ONVIF port 2020; RTSP port 554 recorded but not used by the ONVIF provider; credentials referenced through environment variables |
| Living Room Camera | Auto | Host and model recorded; no protocol, port, or credentials inferred |

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

The Living Room Camera remains Unknown. The current framework has no verified
ONVIF, RTSP, or Xiaomi-native configuration for this model and performs no
network probe for the unverified `auto` entry.

## Expected endpoint state

After provisioning and a supported runtime deployment:

- `/api/cameras` and `/api/camera-control/devices` return the safe read-only
  camera inventory and discovery metadata.
- `/api/devices` projects Bedroom Camera as Online, Offline, or Unknown from
  the ONVIF result and leaves Living Room Camera Unknown unless positively
  identified.
- `/api/device-health` uses the same semantic camera state.
- Full serial numbers, credentials, profile tokens, and media URLs are not
  returned. The authenticated device-health contract may include the
  configured local IP address, consistent with EPIC 19.

No snapshot, stream, PTZ, recording, motion, speaker, or microphone operation
is enabled.

## Remaining unknowns

1. Tapo Camera Account credentials have not been supplied.
2. ONVIF authentication and metadata enumeration have not been exercised
   against the production C200.
3. A compatible ONVIF client dependency must be present in the runtime.
4. No supported local protocol has been verified for the Xiaomi camera.
5. The running service remains on `034e718`; candidate endpoint behavior
   cannot be observed live without deployment.

## Recommended next actions

1. Create the Tapo Camera Account in the official Tapo application.
2. Add the three camera environment settings documented in
   `EPIC_20_CAMERA_PROVISIONING.md` without printing their values.
3. Install the validated config as root-owned mode `0600`.
4. Confirm the runtime contains the compatible ONVIF dependency.
5. Deploy through the protected runtime-only workflow.
6. Use an authenticated dashboard session to verify the four read-only
   endpoints.
7. Leave the Xiaomi camera Unknown until a supported protocol is positively
   identified.
