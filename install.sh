#!/bin/sh
set -eu

APP_SRC="/opt/smart-condo-dashboard"
APP_RUN="/opt/smart-condo-dashboard-run"
VENV="$APP_RUN/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

install -d "$APP_RUN"
rm -rf "$APP_RUN/backend" "$APP_RUN/frontend"
cp -R "$APP_SRC/backend" "$APP_RUN/backend"
cp -R "$APP_SRC/frontend" "$APP_RUN/frontend"

if [ ! -x "$PY" ]; then
    rm -rf "$VENV"
    python3 -m venv "$VENV"
fi

if [ ! -x "$PIP" ]; then
    "$PY" -m ensurepip --upgrade || true
fi

if [ ! -x "$PIP" ]; then
    echo "ERROR: pip is missing in $VENV"
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
