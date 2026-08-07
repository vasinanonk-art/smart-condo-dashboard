#!/bin/sh
set -eu

GO2RTC_VERSION=1.9.14
GO2RTC_ARTIFACT=go2rtc_linux_arm
GO2RTC_SHA256=4d7e1639af5a2722a28e864468fd8099b3c1682565446c798bf9e3b38fde12e4
GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/${GO2RTC_ARTIFACT}"
GO2RTC_BINARY="${GO2RTC_BINARY:-/usr/local/lib/smart-condo-dashboard/go2rtc}"
GO2RTC_CONFIG="${GO2RTC_CONFIG:-/etc/smart-condo-dashboard/go2rtc.yaml}"
GO2RTC_UNIT="${GO2RTC_UNIT:-/etc/systemd/system/smart-condo-go2rtc.service}"
GO2RTC_SERVICE="${GO2RTC_SERVICE:-smart-condo-go2rtc.service}"
GO2RTC_ENV_FILE="${GO2RTC_ENV_FILE:-/etc/default/smart-condo-dashboard}"
GO2RTC_CAMERA_CONFIG="${GO2RTC_CAMERA_CONFIG:-}"
GO2RTC_SYSTEMCTL="${GO2RTC_SYSTEMCTL:-systemctl}"
GO2RTC_PYTHON="${GO2RTC_PYTHON:-python3}"
GO2RTC_RENDERER="${GO2RTC_RENDERER:-$(dirname "$0")/render_go2rtc_config.py}"
GO2RTC_UNIT_SOURCE="${GO2RTC_UNIT_SOURCE:-$(dirname "$0")/../systemd/smart-condo-go2rtc.service}"

architecture() {
    go2rtc_arch=${GO2RTC_ARCH_OVERRIDE:-$(uname -m)}
    case "$go2rtc_arch" in
        armv7l|armv7) printf '%s\n' "$GO2RTC_ARTIFACT" ;;
        *) printf 'ERROR: go2rtc %s is pinned only for ARMv7; detected %s.\n' "$GO2RTC_VERSION" "$go2rtc_arch" >&2; return 1 ;;
    esac
}

backup_path() {
    go2rtc_source=$1; go2rtc_name=$2; go2rtc_backup=$3
    if [ -e "$go2rtc_source" ]; then
        cp -p "$go2rtc_source" "$go2rtc_backup/$go2rtc_name"
        : > "$go2rtc_backup/$go2rtc_name.present"
    else
        : > "$go2rtc_backup/$go2rtc_name.absent"
    fi
}

backup() {
    go2rtc_backup=$1
    install -d -m 0700 "$go2rtc_backup"
    backup_path "$GO2RTC_BINARY" binary "$go2rtc_backup"
    backup_path "$GO2RTC_CONFIG" config "$go2rtc_backup"
    backup_path "$GO2RTC_UNIT" unit "$go2rtc_backup"
    "$GO2RTC_SYSTEMCTL" is-active --quiet "$GO2RTC_SERVICE" 2>/dev/null && : > "$go2rtc_backup/service.active" || : > "$go2rtc_backup/service.inactive"
    "$GO2RTC_SYSTEMCTL" is-enabled --quiet "$GO2RTC_SERVICE" 2>/dev/null && : > "$go2rtc_backup/service.enabled" || : > "$go2rtc_backup/service.disabled"
}

restore_path() {
    go2rtc_target=$1; go2rtc_name=$2; go2rtc_backup=$3
    if [ -f "$go2rtc_backup/$go2rtc_name.present" ]; then
        install -d "$(dirname "$go2rtc_target")"
        cp -p "$go2rtc_backup/$go2rtc_name" "$go2rtc_target"
    else
        [ ! -e "$go2rtc_target" ] || rm -f "$go2rtc_target"
    fi
}

restore() {
    go2rtc_backup=$1
    "$GO2RTC_SYSTEMCTL" stop "$GO2RTC_SERVICE" >/dev/null 2>&1 || true
    restore_path "$GO2RTC_BINARY" binary "$go2rtc_backup"
    restore_path "$GO2RTC_CONFIG" config "$go2rtc_backup"
    restore_path "$GO2RTC_UNIT" unit "$go2rtc_backup"
    "$GO2RTC_SYSTEMCTL" daemon-reload
    if [ -f "$go2rtc_backup/service.enabled" ]; then
        "$GO2RTC_SYSTEMCTL" enable "$GO2RTC_SERVICE" >/dev/null
    else
        "$GO2RTC_SYSTEMCTL" disable "$GO2RTC_SERVICE" >/dev/null 2>&1 || true
    fi
    [ ! -f "$go2rtc_backup/service.active" ] || "$GO2RTC_SYSTEMCTL" start "$GO2RTC_SERVICE"
}

renderer() {
    go2rtc_mode=$1; shift
    set -- --environment-file "$GO2RTC_ENV_FILE" "$@"
    [ -z "$GO2RTC_CAMERA_CONFIG" ] || set -- "$@" --camera-config "$GO2RTC_CAMERA_CONFIG"
    if [ "$go2rtc_mode" = validate ]; then
        set -- "$@" --validate-only
    fi
    "$GO2RTC_PYTHON" "$GO2RTC_RENDERER" "$@"
}

provision() {
    architecture >/dev/null
    renderer validate
    [ -f "$GO2RTC_UNIT_SOURCE" ] || { echo "ERROR: go2rtc unit template missing." >&2; return 1; }
    go2rtc_tmp=$(mktemp -d "${TMPDIR:-/tmp}/smart-condo-go2rtc.XXXXXX")
    trap 'rm -rf "$go2rtc_tmp"' EXIT HUP INT TERM
    go2rtc_download="$go2rtc_tmp/$GO2RTC_ARTIFACT"
    go2rtc_installed_hash=
    if [ -f "$GO2RTC_BINARY" ]; then
        go2rtc_installed_hash=$(sha256sum "$GO2RTC_BINARY"); go2rtc_installed_hash=${go2rtc_installed_hash%% *}
    fi
    if [ "$go2rtc_installed_hash" = "$GO2RTC_SHA256" ]; then
        cp "$GO2RTC_BINARY" "$go2rtc_download"
    elif [ -n "${GO2RTC_ARTIFACT_FILE:-}" ]; then
        cp "$GO2RTC_ARTIFACT_FILE" "$go2rtc_download"
    else
        curl --fail --location --silent --show-error --retry 0 \
            --connect-timeout 10 --max-time 120 --output "$go2rtc_download" "$GO2RTC_URL"
    fi
    go2rtc_actual=$(sha256sum "$go2rtc_download"); go2rtc_actual=${go2rtc_actual%% *}
    if [ "$go2rtc_actual" != "$GO2RTC_SHA256" ]; then
        echo "ERROR: go2rtc artifact checksum mismatch; no files were replaced." >&2
        return 1
    fi

    go2rtc_generated="$go2rtc_tmp/go2rtc.yaml"
    renderer render --output "$go2rtc_generated"
    chmod 0600 "$go2rtc_generated"
    install -d -m 0755 "$(dirname "$GO2RTC_BINARY")"
    install -d -m 0700 "$(dirname "$GO2RTC_CONFIG")"
    install -d -m 0755 "$(dirname "$GO2RTC_UNIT")"
    go2rtc_changed=0
    if [ ! -f "$GO2RTC_BINARY" ] || ! cmp -s "$go2rtc_download" "$GO2RTC_BINARY"; then
        install -m 0755 "$go2rtc_download" "$GO2RTC_BINARY"; go2rtc_changed=1
    fi
    if [ ! -f "$GO2RTC_CONFIG" ] || ! cmp -s "$go2rtc_generated" "$GO2RTC_CONFIG"; then
        install -m 0600 "$go2rtc_generated" "$GO2RTC_CONFIG"; go2rtc_changed=1
    else
        chmod 0600 "$GO2RTC_CONFIG"
    fi
    if [ ! -f "$GO2RTC_UNIT" ] || ! cmp -s "$GO2RTC_UNIT_SOURCE" "$GO2RTC_UNIT"; then
        install -m 0644 "$GO2RTC_UNIT_SOURCE" "$GO2RTC_UNIT"; go2rtc_changed=1
    fi
    "$GO2RTC_SYSTEMCTL" daemon-reload
    "$GO2RTC_SYSTEMCTL" enable "$GO2RTC_SERVICE" >/dev/null
    if [ "$go2rtc_changed" -eq 1 ]; then
        "$GO2RTC_SYSTEMCTL" restart "$GO2RTC_SERVICE"
    else
        "$GO2RTC_SYSTEMCTL" start "$GO2RTC_SERVICE"
    fi
    "$GO2RTC_SYSTEMCTL" is-active --quiet "$GO2RTC_SERVICE"
    rm -rf "$go2rtc_tmp"; trap - EXIT HUP INT TERM
    echo "go2rtc ${GO2RTC_VERSION} provisioned with loopback-only listeners."
}

dry_run() {
    architecture >/dev/null
    renderer validate
    echo "go2rtc dry run: pinned ${GO2RTC_VERSION}/${GO2RTC_ARTIFACT}; configuration inputs valid."
}

case "${1:-}" in
    backup) [ "$#" -eq 2 ] || exit 2; backup "$2" ;;
    restore) [ "$#" -eq 2 ] || exit 2; restore "$2" ;;
    provision) [ "$#" -eq 1 ] || exit 2; provision ;;
    dry-run) [ "$#" -eq 1 ] || exit 2; dry_run ;;
    *) echo "Usage: $0 {backup DIR|restore DIR|provision|dry-run}" >&2; exit 2 ;;
esac
