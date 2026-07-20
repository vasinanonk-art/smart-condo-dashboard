"""EPIC 08 regression and security contract tests.

Live TV/network/systemd calls are intentionally mocked by the production test suite;
this file also guards the source-level security properties without a live TV.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "lg_tv_pairing.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "backend" / "app_entry.py").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
UI = (ROOT / "frontend" / "assets" / "dashboard_lg_status.js").read_text(encoding="utf-8")


def test_status_and_diagnostics_routes_exist():
    for route in (
        '/api/lg-tv/pairing/status', '/api/lg-tv/pairing/job',
        '/api/lg-tv/pairing/diagnostics',
    ):
        assert route in SOURCE


def test_mutating_pairing_routes_are_post_and_auth_middleware_loads_last():
    for route in (
        '/api/lg-tv/pairing/request', '/api/lg-tv/pairing/save',
        '/api/lg-tv/pairing/cancel',
    ):
        assert f'@app.post("{route}")' in SOURCE
    assert ENTRY.index('lg_tv_pairing') < ENTRY.index('dashboard_auth')


def test_status_never_returns_client_key():
    status_block = SOURCE.split('def pairing_status', 1)[1].split('@app.post', 1)[0]
    diagnostics_block = SOURCE.split('def pairing_diagnostics', 1)[1]
    assert '"client_key"' not in status_block
    assert '"client_key"' not in diagnostics_block
    assert 'pending_key' not in SOURCE.split('def _job_public', 1)[1].split('@app.on_event', 1)[0]


def test_key_source_priority_and_secure_path():
    current = SOURCE.split('def _current_key', 1)[1].split('def _atomic_write_key', 1)[0]
    assert current.index('LG_TV_CLIENT_KEY') < current.index('_read_key_file') < current.index('_legacy_key')
    assert '/root/.smart-condo-dashboard/secrets' in SOURCE
    assert 'lg_tv_client_key' in SOURCE


def test_atomic_save_permissions_backup_and_rollback():
    block = SOURCE.split('def _atomic_write_key', 1)[1].split('def _restore_key', 1)[0]
    assert '0o700' in block
    assert '0o600' in block
    assert 'os.fsync' in block
    assert 'os.replace' in block
    assert 'backup-' in block
    assert '_restore_key' in SOURCE


def test_pairing_is_single_bounded_background_job():
    assert 'PAIR_TIMEOUT_SEC' in SOURCE and '120' in SOURCE
    assert 'threading.Thread' in SOURCE
    assert 'name="lg-tv-pairing"' in SOURCE
    request = SOURCE.split('def pairing_request', 1)[1].split('@app.get("/api/lg-tv/pairing/job")', 1)[0]
    assert 'thread.is_alive()' in request
    assert 'pairing_job_active' in request
    assert 'pairing_rate_limited' in request


def test_register_flow_is_secure_and_uses_pywebostv_089_constants():
    register = SOURCE.split('def _webos_register', 1)[1].split('def _validate_key', 1)[0]
    assert 'secure=True' in register
    assert 'WebOSClient.PROMPTED' in register
    assert 'WebOSClient.REGISTERED' in register
    assert 'from pywebostv.model import Registration' not in register
    assert 'PAIR_TIMEOUT_SEC' in register
    for result in ('prompted', 'registered', 'timeout', 'rejected', 'connection_failed'):
        assert result in SOURCE


def test_save_validates_restarts_only_lgtv_service_and_rolls_back():
    save = SOURCE.split('def pairing_save', 1)[1].split('@app.get("/api/lg-tv/pairing/diagnostics")', 1)[0]
    assert save.count('_validate_key(candidate)') >= 2
    assert '["systemctl", "restart", SERVICE_NAME]' in save
    assert '_restore_key(backup, previous)' in save
    assert 'rolled_back' in save
    assert 'mosquitto' not in save.lower()


def test_legacy_migration_preserves_backup_and_does_not_print_key():
    assert 'CLIENT_KEY' in SOURCE
    assert 'pre-key-loader-' in SOURCE
    assert 'MIGRATION_MARKER' in SOURCE
    lowered = SOURCE.lower()
    assert 'print(' not in lowered
    assert 'log.info' not in lowered and 'log.warning' not in lowered


def test_pairing_flow_is_preserved_in_consolidated_ui():
    assert 'dashboard_lg_remote.js' in INDEX
    assert 'dashboard_lg_status.js' in INDEX
    assert 'dashboard_lg_pairing.js' not in INDEX
    for label in ('Repair Pairing', 'Test Connection', 'Save & Reconnect', 'Cancel Pairing'):
        assert label in UI
    assert 'Approve the connection request on the LG TV' in UI
    assert "['connecting', 'prompted']" in UI


def test_regression_forbidden_integrations_untouched():
    lowered = SOURCE.lower()
    for forbidden in ('sonoff', 'presence', 'pj1103', 'tariff', 'automation_trigger', 'camera', 'tapo'):
        assert forbidden not in lowered
