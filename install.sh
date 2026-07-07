#!/bin/sh
set -eu

APP_SRC="/opt/smart-condo-dashboard"
APP_RUN="/opt/smart-condo-dashboard-run"
VENV="$APP_RUN/venv"
PY="$VENV/bin/python"

install -d "$APP_RUN"

# Rebuild runtime folders from repository files only. Do not copy or run frontend patch scripts.
[ ! -d "$APP_RUN/backend" ] || rm -r "$APP_RUN/backend"
[ ! -d "$APP_RUN/frontend" ] || rm -r "$APP_RUN/frontend"
[ ! -d "$APP_RUN/config" ] || rm -r "$APP_RUN/config"
[ ! -d "$APP_RUN/scripts" ] || rm -r "$APP_RUN/scripts"
[ ! -f "$APP_RUN/sonoff_client.py" ] || rm "$APP_RUN/sonoff_client.py"

cp -R "$APP_SRC/backend" "$APP_RUN/backend"
cp -R "$APP_SRC/frontend" "$APP_RUN/frontend"
cp -R "$APP_SRC/config" "$APP_RUN/config"
cp "$APP_SRC/sonoff_client.py" "$APP_RUN/sonoff_client.py"

if [ ! -x "$PY" ]; then
    [ ! -d "$VENV" ] || rm -r "$VENV"
    python3 -m venv "$VENV"
fi

if ! "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m ensurepip --upgrade || true
fi

if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "ERROR: pip module is missing in $VENV"
    echo "Install python3-venv/python3-pip on the TinkerBoard, then rerun install.sh"
    exit 1
fi

"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r "$APP_RUN/backend/requirements.txt"

install -m 0644 "$APP_SRC/systemd/smart-condo-dashboard.service" /etc/systemd/system/smart-condo-dashboard.service

systemctl daemon-reload
systemctl enable smart-condo-dashboard
systemctl restart smart-condo-dashboard
systemctl status smart-condo-dashboard --no-pager -l || true
