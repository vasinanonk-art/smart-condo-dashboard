# Smart Condo Dashboard v2.2.0 Stable

Smart Condo Dashboard runtime for port 8090.

## Production runtime note

Port 8090 is the only supported Smart Condo Dashboard runtime.

Do not reference, copy, merge, or modify anything from the separate 8080 project. Keep the 8090 dashboard isolated from any 8080 runtime, service, reverse proxy, or codebase.

## Target topology

Browser/Mobile -> FastAPI Dashboard -> MQTT -> Tinker Board LG TV Gateway -> LG TV

## Install on Tinker Board / Debian / Armbian

```bash
cd /root
unzip smart_condo_dashboard_v1.zip
cd smart_condo_dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
MQTT_HOST=127.0.0.1 uvicorn backend.app:app --host 0.0.0.0 --port 8090
```

Open:

```text
http://TINKER_IP:8090
```

For your current Tinker Board:

```text
http://192.168.1.60:8090
http://172.23.250.43:8090
```

## Install as systemd

```bash
cp systemd/smart-condo-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable smart-condo-dashboard
systemctl start smart-condo-dashboard
systemctl status smart-condo-dashboard --no-pager
```

## MQTT topics

Command:

```text
home/lgtv/cmd
```

State:

```text
home/lgtv/state
```

## Supported commands

```text
power_on
power_off
youtube
netflix
disney
prime
appletv
browser
livetv
home
viu
hbo
hdmi1
hdmi2
hdmi3
hdmi4
volume_up
volume_down
mute
unmute
up
down
left
right
ok
back
home_key
```
