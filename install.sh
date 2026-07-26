#!/bin/sh
set -eu

APP_SRC="${APP_SRC:-/opt/smart-condo-dashboard}"
APP_RUN="${APP_RUN:-/opt/smart-condo-dashboard-run}"
PERSISTENT_CONFIG_ROOT="${PERSISTENT_CONFIG_ROOT:-/root/.smart-condo-dashboard}"
INSTALL_LOCK_FILE="${INSTALL_LOCK_FILE:-/run/lock/smart-condo-dashboard-install.lock}"
VENV="$APP_RUN/venv"
PY="$VENV/bin/python"
DRY_RUN=0
RUNTIME_ONLY=0

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
elif [ "${1:-}" = "--runtime-only" ]; then
    RUNTIME_ONLY=1
elif [ "$#" -gt 0 ]; then
    echo "Usage: $0 [--dry-run|--runtime-only]" >&2
    exit 2
fi

install -d "$(dirname "$INSTALL_LOCK_FILE")"
exec 9>"$INSTALL_LOCK_FILE"
if ! flock -n 9; then
    echo "ERROR: another smart-condo-dashboard deployment is active; no runtime files were touched." >&2
    exit 1
fi
echo "Deployment lock acquired."

. "$APP_SRC/scripts/runtime_config_guard.sh"

LOCAL_CONFIG_TMP=$(mktemp -d "${TMPDIR:-/tmp}/smart-condo-dashboard-local-config.XXXXXX")
LOCAL_CONFIG_MANIFEST="$LOCAL_CONFIG_TMP/manifest"
LOCAL_CONFIG_BACKUP="$LOCAL_CONFIG_TMP/files"
KEEP_LOCAL_CONFIG_BACKUP=0
DEPLOY_STARTED=0
CONFIG_RESTORE_COMPLETE=0
deployment_cleanup() {
    cleanup_status=$1
    trap - EXIT HUP INT TERM
    if [ "$DEPLOY_STARTED" -eq 1 ] && [ "$CONFIG_RESTORE_COMPLETE" -eq 0 ]; then
        echo "Deployment exited early; restoring preserved local configuration." >&2
        if ! restore_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST"; then
            KEEP_LOCAL_CONFIG_BACKUP=1
        fi
    fi
    if [ "$KEEP_LOCAL_CONFIG_BACKUP" -eq 0 ]; then
        rm -rf "$LOCAL_CONFIG_TMP"
    else
        echo "Preserved configuration backup retained at: $LOCAL_CONFIG_TMP" >&2
    fi
    flock -u 9
    exit "$cleanup_status"
}
trap 'deployment_cleanup $?' EXIT
trap 'exit 1' HUP INT TERM

install -d "$APP_RUN"
preserve_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST"
verify_config_backups "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST"

CAMERA_WAS_PRESENT=0
SONOFF_WAS_PRESENT=0
runtime_config_present \
    "${CAMERA_CONFIG_FILE:-$PERSISTENT_CONFIG_ROOT/cameras.local.json}" \
    "$APP_RUN/config/cameras.local.json" && CAMERA_WAS_PRESENT=1
runtime_config_present \
    "${EWELINK_CONFIG_FILE:-$PERSISTENT_CONFIG_ROOT/ewelink.local.json}" \
    "$APP_RUN/config/ewelink.local.json" && SONOFF_WAS_PRESENT=1

if [ "$DRY_RUN" -eq 1 ]; then
    verify_config_backups "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST"
    verify_preserved_configs "$APP_RUN" "$LOCAL_CONFIG_MANIFEST"
    echo "Dry run: managed runtime directories would be replaced without rsync --delete."
    echo "Dry run: persistent root would remain untouched: $PERSISTENT_CONFIG_ROOT"
    echo "Dry run: Camera config previously present: $CAMERA_WAS_PRESENT"
    echo "Dry run: Sonoff config previously present: $SONOFF_WAS_PRESENT"
    echo "Dry run: local configuration preservation verified."
    exit 0
fi

echo "Replacing managed runtime directories. Preserved config/*.local.json files will be restored."
# This deployment intentionally does not use rsync --delete. Only the managed code
# directories below are replaced; the persistent configuration root is never touched.
DEPLOY_STARTED=1
[ ! -d "$APP_RUN/backend" ] || rm -r "$APP_RUN/backend"
[ ! -d "$APP_RUN/frontend" ] || rm -r "$APP_RUN/frontend"
[ ! -d "$APP_RUN/config" ] || rm -r "$APP_RUN/config"
[ ! -d "$APP_RUN/scripts" ] || rm -r "$APP_RUN/scripts"
[ ! -f "$APP_RUN/sonoff_client.py" ] || rm "$APP_RUN/sonoff_client.py"

cp -R "$APP_SRC/backend" "$APP_RUN/backend"
cp -R "$APP_SRC/frontend" "$APP_RUN/frontend"
cp -R "$APP_SRC/config" "$APP_RUN/config"
cp -R "$APP_SRC/scripts" "$APP_RUN/scripts"
cp "$APP_SRC/sonoff_client.py" "$APP_RUN/sonoff_client.py"

# Explicitly install the production dashboard shell and authoritative frontend assets.
install -d "$APP_RUN/frontend/assets"
install -m 0644 "$APP_SRC/frontend/index.html" "$APP_RUN/frontend/index.html"
for asset in dashboard_v3.css dashboard_v3_layout.css dashboard_upgrade.css dashboard_polish.css dashboard_upgrade.js dashboard_v3.js dashboard_command_fixes.js; do
    install -m 0644 "$APP_SRC/frontend/assets/$asset" "$APP_RUN/frontend/assets/$asset"
done

if ! restore_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST"; then
    KEEP_LOCAL_CONFIG_BACKUP=1
    echo "ERROR: deployment aborted before service restart because local configuration could not be restored." >&2
    exit 1
fi
CONFIG_RESTORE_COMPLETE=1
if ! verify_preserved_configs "$APP_RUN" "$LOCAL_CONFIG_MANIFEST"; then
    restore_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST" || true
    KEEP_LOCAL_CONFIG_BACKUP=1
    echo "ERROR: deployment aborted before service restart because local configuration was lost." >&2
    exit 1
fi

if [ "$CAMERA_WAS_PRESENT" -eq 1 ] && ! runtime_config_present \
    "${CAMERA_CONFIG_FILE:-$PERSISTENT_CONFIG_ROOT/cameras.local.json}" \
    "$APP_RUN/config/cameras.local.json"; then
    restore_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST" || true
    KEEP_LOCAL_CONFIG_BACKUP=1
    echo "ERROR: deployment aborted before service restart because Camera configuration was lost." >&2
    exit 1
fi
if [ "$SONOFF_WAS_PRESENT" -eq 1 ] && ! runtime_config_present \
    "${EWELINK_CONFIG_FILE:-$PERSISTENT_CONFIG_ROOT/ewelink.local.json}" \
    "$APP_RUN/config/ewelink.local.json"; then
    restore_local_configs "$APP_RUN" "$LOCAL_CONFIG_BACKUP" "$LOCAL_CONFIG_MANIFEST" || true
    KEEP_LOCAL_CONFIG_BACKUP=1
    echo "ERROR: deployment aborted before service restart because Sonoff configuration was lost." >&2
    exit 1
fi

if [ "$RUNTIME_ONLY" -eq 1 ]; then
    echo "Runtime-only deployment: virtual environment and dependencies were not modified."
    systemctl restart smart-condo-dashboard
    systemctl status smart-condo-dashboard --no-pager -l || true
    exit 0
fi

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
"$PY" -c "from pywebostv.connection import WebOSClient; assert WebOSClient.PROMPTED == 1 and WebOSClient.REGISTERED == 2"

install -m 0644 "$APP_SRC/systemd/smart-condo-dashboard.service" /etc/systemd/system/smart-condo-dashboard.service

systemctl daemon-reload
systemctl enable smart-condo-dashboard
systemctl restart smart-condo-dashboard
systemctl status smart-condo-dashboard --no-pager -l || true
