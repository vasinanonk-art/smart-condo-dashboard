from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=(ROOT/'backend'/'lg_tv_status.py').read_text(encoding='utf-8')
RUNTIME=(ROOT/'backend'/'lg_tv_status_runtime.py').read_text(encoding='utf-8')
PAIR=(ROOT/'backend'/'lg_tv_pairing.py').read_text(encoding='utf-8')
REQ=(ROOT/'backend'/'requirements.txt').read_text(encoding='utf-8')
INSTALL=(ROOT/'install.sh').read_text(encoding='utf-8')
ENTRY=(ROOT/'backend'/'app_entry.py').read_text(encoding='utf-8')


def test_status_routes_and_safe_contract():
    for route in ('/api/lg-tv/status','/api/lg-tv/status/diagnostics','/api/lg-tv/status/refresh','/api/lg-tv/pairing/test','/api/lg-tv/pairing/forget'):
        assert route in SRC
    for field in ('current_app','current_input','audio','device','last_success_ts','last_attempt_ts','data_age_sec','stale','reconnect_count','consecutive_failures','key_source'):
        assert field in SRC
    public=SRC.split('def _public_status',1)[1]
    assert 'client_key' not in public


def test_one_worker_one_poll_lock_and_adaptive_intervals():
    assert 'POLL_LOCK = threading.Lock()' in SRC
    assert 'name="lg-tv-status"' in SRC
    assert 'if _WORKER and _WORKER.is_alive()' in SRC
    assert 'return 5' in SRC and 'return 30' in SRC
    assert 'BACKOFF = (30, 60, 120, 300)' in SRC
    assert 'POLL_LOCK.acquire(blocking=False)' in SRC


def test_live_collection_and_friendly_mapping():
    for name in ('Netflix','YouTube','Disney+','Prime Video','Apple TV','HBO Max','Viu','Browser','Live TV','Home'):
        assert name in SRC
    assert 'ApplicationControl' in SRC and 'MediaControl' in SRC and 'SourceControl' in SRC and 'SystemControl' in SRC
    assert '"volume": volume' in SRC and '"muted": muted' in SRC
    assert '"model": info.get' in SRC and '"webos_version"' in SRC


def test_pairing_runtime_uses_live_cache_without_sync_validation():
    assert 'status._public_status()' in RUNTIME
    assert 'pairing._validate_key' not in RUNTIME
    assert 'last_connection_success' in RUNTIME


def test_security_forget_backup_and_permissions():
    block=SRC.split('def lg_pairing_forget',1)[1].split('def record_command',1)[0]
    assert 'forgotten-' in block and 'shutil.copy2' in block and '0o600' in block
    assert 'key_path.unlink()' in block
    assert 'systemctl' not in block
    assert 'print(' not in SRC


def test_polling_does_not_restart_services():
    poll=SRC.split('def _poll_once',1)[1].split('def _interval',1)[0]
    assert 'systemctl' not in poll
    assert 'restart' not in poll.lower()


def test_power_transitions_and_safe_errors():
    assert 'WAKE_GRACE_SEC = 60' in SRC
    assert 'intentional_power_off_until' in SRC
    for code in ('tv_unreachable','websocket_connect_failed','pairing_required','key_missing','status_timeout','status_read_failed','dependency_missing','refresh_already_running'):
        assert code in SRC


def test_dependency_and_registration_constants_preserved():
    assert 'pywebostv==0.8.9' in REQ
    assert 'WebOSClient.PROMPTED == 1' in INSTALL
    assert 'WebOSClient.REGISTERED == 2' in INSTALL
    assert 'WebOSClient.PROMPTED' in PAIR and 'WebOSClient.REGISTERED' in PAIR


def test_auth_loads_after_all_lg_mutating_routes():
    assert ENTRY.index('lg_tv_status_runtime') < ENTRY.index('dashboard_auth')
