from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/'frontend'/'assets'/'dashboard_lg_status.js').read_text(encoding='utf-8')
CSS=(ROOT/'frontend'/'assets'/'dashboard_lg_status.css').read_text(encoding='utf-8')
INDEX=(ROOT/'frontend'/'index.html').read_text(encoding='utf-8')
REMOTE=(ROOT/'frontend'/'assets'/'dashboard_lg_remote.js').read_text(encoding='utf-8')


def test_live_status_values_and_stable_mount():
    for value in ('lgStatusValue','lgAppValue','lgInputValue','lgVolumeValue','lgMuteValue','lgUpdateValue'):
        assert value in UI
    assert 'if(S.mounted)return' in UI
    assert 'outerHTML' not in UI
    assert '.remove()' not in UI.replace("document.getElementById('lgPairingCard')?.remove();",'')


def test_background_polling_keeps_values_and_is_single():
    assert 'S.timer=setInterval' in UI
    assert 'if(S.timer){clearInterval(S.timer);S.timer=null;}' in UI
    assert "document.addEventListener('visibilitychange'" in UI
    assert "window.addEventListener('beforeunload'" in UI
    assert 'S.status=status' in UI
    assert 'innerHTML=\'\'' not in UI


def test_stale_response_and_diagnostics():
    assert 'if(seq<S.applied){S.ignored++;return;}' in UI
    assert 'window.dashboardLgTvDiagnostics' in UI
    for field in ('active_pollers','last_status_fetch_started','last_status_fetch_completed','ignored_stale_responses','current_poll_interval_ms','status_age_sec','pairing_job_poll_active','command_in_progress','last_ui_error'):
        assert field in UI
    assert 'client_key' not in UI.lower()


def test_pairing_polish_and_actions():
    for text in ('Paired & Connected','LG TV is paired and ready','Repair Pairing','Test Connection','Forget Pairing','Save & Reconnect','Cancel Pairing'):
        assert text in UI
    assert 'Available only after a new key is registered.' in UI
    assert '/api/lg-tv/pairing/test' in UI and '/api/lg-tv/pairing/forget' in UI
    assert "confirm('Forget the saved LG TV pairing key?" in UI


def test_manual_refresh_and_individual_command_disable():
    assert '/api/lg-tv/status/refresh' in UI
    assert 'button.disabled=true' in UI
    assert 'if(button)button.disabled=true' in UI
    assert 'setTimeout(()=>req' in UI


def test_relative_time_and_unavailable_mute():
    assert 'Just now' in UI and 'sec ago' in UI and 'min ago' in UI
    assert "'Unavailable'" in UI
    assert "s.audio?.muted===true?'Muted':s.audio?.muted===false?'Unmuted':'Unavailable'" in UI


def test_responsive_and_accessibility():
    assert '@media(max-width:980px)' in CSS and '@media(max-width:620px)' in CSS
    assert 'grid-template-columns:repeat(3' in CSS
    assert 'aria-live="polite"' in UI
    assert 'min-height' in CSS


def test_existing_remote_layout_and_commands_preserved():
    assert 'dashboard_lg_remote.js' in INDEX
    assert 'dashboard_lg_status.js' in INDEX
    assert INDEX.index('dashboard_lg_remote.js') < INDEX.index('dashboard_lg_status.js')
    for command in ('power_on','power_off','volume_up','hdmi1','netflix'):
        assert command in REMOTE
