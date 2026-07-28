import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
ASSETS = ROOT / "frontend/assets"
INDEX = (ROOT / "frontend/index.html").read_text()
SYSTEM_JS = (ASSETS / "dashboard_household_design_system.js").read_text()
SYSTEM_CSS = (ASSETS / "dashboard_household_design_system.css").read_text()
HOUSEHOLD_JS = (ASSETS / "dashboard_household_devices.js").read_text()
HOUSEHOLD_CSS = (ASSETS / "dashboard_household_devices.css").read_text()
LG_STATUS = (ASSETS / "dashboard_lg_status.js").read_text()
LG_REMOTE = (ASSETS / "dashboard_lg_remote.js").read_text()
LG_CSS = (ASSETS / "dashboard_lg_remote.css").read_text()
NOTIFICATIONS = (ASSETS / "dashboard_notifications.js").read_text()
NOTIFICATION_CSS = (ASSETS / "dashboard_notifications.css").read_text()


def test_shared_components_are_defined_and_loaded_first():
    for component in (
        "deviceCard", "deviceHeader", "statusBadge", "stateQualityBadge",
        "actionGrid", "warningBox", "deviceDetails", "toast",
    ):
        assert f"function {component}" in SYSTEM_JS
    assert INDEX.index("dashboard_household_design_system.js") < INDEX.index("dashboard_lg_remote.js")
    assert INDEX.index("dashboard_household_design_system.js") < INDEX.index("dashboard_household_devices.js")


def test_household_and_lg_consume_shared_components():
    for call in ("UI.deviceCard", "UI.deviceDetails", "UI.actionButton"):
        assert call in HOUSEHOLD_JS
    for call in ("UI.deviceHeader", "UI.deviceDetails", "UI.actionButton", "UI.toast"):
        assert call in LG_STATUS + LG_REMOTE


def test_normal_cards_use_device_language_and_collapse_provider_reason():
    assert "warning:userReason(device)" in HOUSEHOLD_JS
    assert "Controls are not configured yet." in HOUSEHOLD_JS
    assert "device.unavailable_reason" in HOUSEHOLD_JS
    assert "<span>Technical status</span>" in HOUSEHOLD_JS


def test_lg_details_are_collapsed_and_lazy_loaded():
    assert "summary:'TV Details'" in LG_STATUS
    assert "data-lg-details" in LG_STATUS
    assert "if (event.currentTarget.open) loadDetails()" in LG_STATUS
    assert "/api/lg-tv/pairing/status" in LG_STATUS
    assert "/api/lg-tv/status/diagnostics" in LG_STATUS
    for field in ("Key source", "Service", "Software", "Firmware", "Status worker"):
        assert field in LG_STATUS
    assert "tv_ip" not in LG_STATUS.lower()
    assert "client_key" not in LG_STATUS.lower()


def test_lg_remote_keeps_honest_power_and_live_options():
    reason = "Wake-on-LAN is not configured. Add the TV MAC address to enable Power On."
    assert reason in LG_REMOTE
    assert "capabilities.inputs" in LG_REMOTE
    assert "capabilities.applications" in LG_REMOTE
    assert "enumeration_available === true" in LG_REMOTE
    for assumption in ("hdmi1", "hdmi2", "com.webos.app", "youtube.leanback"):
        assert assumption not in LG_REMOTE.lower()


def test_notification_owner_has_complete_accessible_lifecycle():
    assert "aria-haspopup" in NOTIFICATIONS
    assert "aria-expanded" in NOTIFICATIONS
    assert "event.key === 'Escape'" in NOTIFICATIONS
    assert "panel.contains(event.target)" in NOTIFICATIONS
    assert "button?.focus()" in NOTIFICATIONS
    assert "if (state.loadPromise) return state.loadPromise" in NOTIFICATIONS
    assert "state.loadPromise = (async () =>" in NOTIFICATIONS
    for endpoint in (
        "/api/notifications/mark-all-read",
        "/api/notifications/clear-all",
        "/api/notifications/${encodeURIComponent(button.dataset.notificationRead)}/read",
        "/api/notifications/${encodeURIComponent(button.dataset.notificationClear)}",
    ):
        assert endpoint in NOTIFICATIONS
    assert "setInterval" not in NOTIFICATIONS
    assert "setTimeout" not in NOTIFICATIONS


def test_notifications_have_one_frontend_data_owner():
    owners = []
    for path in sorted(ASSETS.glob("*.js")):
        source = path.read_text()
        if "'/api/notifications'" in source or '"/api/notifications"' in source:
            owners.append(path.name)
    assert owners == ["dashboard_notifications.js"]
    assert NOTIFICATIONS.count("load({renderPanel:false})") == 1


def test_disabled_reasons_and_keyboard_focus_are_visible():
    assert 'aria-label="${safe(`${label}. ${reason}`)}"' in SYSTEM_JS
    assert 'title="${safe(reason)}"' in SYSTEM_JS
    assert ":focus-visible" in SYSTEM_CSS
    assert ":focus-visible" in LG_CSS
    assert ":focus-visible" in NOTIFICATION_CSS
    assert "household-warning-box" in SYSTEM_CSS
    assert "'disabled'} aria-label=\"LG TV volume\"" in LG_REMOTE


def test_unobserved_lg_status_is_not_reported_offline():
    assert "value.online === false ? 'offline' : 'unknown'" in LG_STATUS


def test_mobile_and_requested_width_contracts_have_no_fixed_height_truncation():
    combined = SYSTEM_CSS + HOUSEHOLD_CSS + LG_CSS + NOTIFICATION_CSS
    assert "@media (max-width: 520px)" in combined
    assert "@media (max-width: 390px)" in combined
    assert "grid-template-columns: minmax(0, 1fr)" in combined
    assert "max-height: calc(100vh - 92px)" in combined
    assert not re.search(r"\\.household-device-card\\s*\\{[^}]*height:\\s*\\d", combined, re.S)


def test_new_ui_has_no_inline_styles_or_important_rules():
    scripts = SYSTEM_JS + HOUSEHOLD_JS + LG_STATUS + LG_REMOTE + NOTIFICATIONS
    styles = SYSTEM_CSS + HOUSEHOLD_CSS + LG_CSS + NOTIFICATION_CSS
    assert "style=" not in scripts
    assert "!important" not in styles
