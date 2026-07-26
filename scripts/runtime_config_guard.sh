#!/bin/sh

preserve_local_configs() {
    guard_run_root=$1
    guard_backup_root=$2
    guard_manifest=$3
    : > "$guard_manifest"
    install -d "$guard_backup_root"
    for guard_path in "$guard_run_root"/config/*.local.json; do
        [ -f "$guard_path" ] || continue
        guard_name=${guard_path##*/}
        cp -p "$guard_path" "$guard_backup_root/$guard_name"
        guard_hash=$(sha256sum "$guard_backup_root/$guard_name")
        guard_hash=${guard_hash%% *}
        printf '%s\t%s\n' "$guard_hash" "$guard_name" >> "$guard_manifest"
        printf 'Preserved local config: config/%s\n' "$guard_name"
    done
}

restore_local_configs() {
    guard_run_root=$1
    guard_backup_root=$2
    guard_manifest=$3
    install -d "$guard_run_root/config"
    while IFS='	' read -r guard_hash guard_name; do
        [ -n "$guard_hash" ] || continue
        [ -n "$guard_name" ] || continue
        cp -p "$guard_backup_root/$guard_name" "$guard_run_root/config/$guard_name"
    done < "$guard_manifest"
}


verify_config_backups() {
    guard_backup_root=$1
    guard_manifest=$2
    while IFS='	' read -r guard_hash guard_name; do
        [ -n "$guard_hash" ] || continue
        [ -n "$guard_name" ] || continue
        if [ ! -f "$guard_backup_root/$guard_name" ]; then
            printf 'ERROR: local config backup is missing: config/%s\n' "$guard_name" >&2
            return 1
        fi
        guard_actual=$(sha256sum "$guard_backup_root/$guard_name")
        guard_actual=${guard_actual%% *}
        if [ "$guard_actual" != "$guard_hash" ]; then
            printf 'ERROR: local config backup checksum mismatch: config/%s\n' "$guard_name" >&2
            return 1
        fi
    done < "$guard_manifest"
}


verify_preserved_configs() {
    guard_run_root=$1
    guard_manifest=$2
    while IFS='	' read -r guard_hash guard_name; do
        [ -n "$guard_hash" ] || continue
        [ -n "$guard_name" ] || continue
        if [ ! -f "$guard_run_root/config/$guard_name" ]; then
            printf 'ERROR: preserved local config is missing: config/%s\n' "$guard_name" >&2
            return 1
        fi
        guard_actual=$(sha256sum "$guard_run_root/config/$guard_name")
        guard_actual=${guard_actual%% *}
        if [ "$guard_actual" != "$guard_hash" ]; then
            printf 'ERROR: preserved local config checksum mismatch: config/%s\n' "$guard_name" >&2
            return 1
        fi
    done < "$guard_manifest"
}

runtime_config_present() {
    guard_persistent_path=$1
    guard_legacy_path=$2
    [ -f "$guard_persistent_path" ] || [ -f "$guard_legacy_path" ]
}
