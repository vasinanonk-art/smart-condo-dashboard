import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend/index.html").read_text()
M2 = (ROOT / "frontend/assets/dashboard_milestone2.js").read_text()
M2_CSS = (ROOT / "frontend/assets/dashboard_milestone2.css").read_text()
PAGE_CHROME = (ROOT / "frontend/assets/dashboard_page_chrome.js").read_text()
HOME_CSS = (ROOT / "frontend/assets/dashboard_home.css").read_text()
HOME = (ROOT / "frontend/assets/dashboard_home.js").read_text()
CAMERAS = (ROOT / "frontend/assets/dashboard_cameras.js").read_text()
CAMERA_CSS = (ROOT / "frontend/assets/dashboard_cameras.css").read_text()
ELECTRICITY = (ROOT / "frontend/assets/dashboard_electricity.js").read_text()
V3 = (ROOT / "frontend/assets/dashboard_v3.js").read_text()


def nav_routes(fragment):
    return re.findall(r'data-nav="([^"]+)"', fragment)


def test_primary_navigation_has_exactly_five_shared_destinations():
    desktop = re.search(r'<nav class="nav">(.*?)</nav>', INDEX, re.S).group(1)
    mobile = re.search(r'<nav class="mobile-nav[^>]*>(.*?)</nav>', INDEX, re.S).group(1)
    expected = ["overview", "devices", "electricity", "camera", "more"]
    assert nav_routes(desktop) == expected
    assert nav_routes(mobile) == expected
    assert "installPrimaryNavigation()" in M2
    assert "PRIMARY_ROUTES.map" in M2
    assert 'data-nav="history"' not in (ROOT / "frontend/assets/dashboard_polish10.js").read_text()


def test_more_and_devices_landings_link_to_preserved_routes():
    for route in ("system", "topology", "history", "settings", "automation"):
        assert f'data-more-route="{route}"' in INDEX
    for title, route in (("Lights", "lighting"), ("Climate", "climate"), ("TV / Entertainment", "entertainment"), ("Presence", "presence")):
        assert f"title:'{title}'" in M2
        assert f"route:'{route}'" in M2
    for route in ("lighting", "climate", "entertainment", "presence", "system"):
        assert f'data-page="{route}"' in INDEX
        assert f"{route}: '" in M2
    assert "Household tools" in INDEX
    assert "System and configuration" in INDEX
    assert '<h2>Devices</h2>' not in M2


def test_secondary_destinations_have_explicit_parent_back_navigation():
    for route in ("lighting", "climate", "entertainment", "presence"):
        assert f"{route}: {{page:'devices', label:'Devices'}}" in PAGE_CHROME
    for route in ("system", "topology", "history", "automation", "settings"):
        assert f"{route}: {{page:'more', label:'More'}}" in PAGE_CHROME
    assert "button.id = 'pageBackButton'" in PAGE_CHROME
    assert "button.onclick = () => window.nav(parent.page)" in PAGE_CHROME
    assert "Back to ${parent.label}" in PAGE_CHROME
    assert "window.scrollTo({top:0, left:0, behavior:'auto'});" in PAGE_CHROME


def test_desktop_pages_use_shared_container_and_navigation_does_not_overlay_content():
    assert '.main > .page:not([data-page="overview"])' in HOME_CSS
    assert "width: min(100%, var(--sc-content-max))" in HOME_CSS
    desktop_navigation = HOME_CSS.split(
        ".sc-dashboard-shell > .sc-bottom-navigation-container", 1
    )[1].split("}", 1)[0]
    assert "position: relative" in desktop_navigation
    mobile = HOME_CSS.split("@media (max-width: 760px)", 1)[1]
    assert "position: fixed" in mobile
    assert "padding-bottom: calc(152px + env(safe-area-inset-bottom))" in mobile


def test_status_contract_and_lg_truthfulness():
    expected = {
        "healthy": ("Healthy", "success"), "online": ("Online", "success"),
        "off": ("Off", "neutral"), "attention": ("Attention", "warning"),
        "offline": ("Offline", "critical"), "unavailable": ("Unavailable", "warning"),
        "unknown": ("Unknown", "neutral"),
    }
    for key, (label, tone) in expected.items():
        assert f"{key}: {{label: '{label}', tone: '{tone}'}}" in M2
    assert "online === true && powered === false" in M2
    assert "states.every(value => value === false)" in M2
    assert "reachable(item) !== false" not in M2
    tv_status = V3[V3.index("function tvStatusLabel"):V3.index("function renderEntertainment")]
    assert "['off'" not in tv_status and "label:'Off'" not in tv_status
    assert "reachable" in tv_status and "connection_state" in tv_status
    assert "if (!tv) return {label:'Unknown'" in tv_status


def test_home_health_and_energy_wording_remain_truthful():
    assert "const overall = required.length ? 'Needs attention' : 'Healthy'" in V3
    assert "const actionSection = required.length ?" in V3
    assert "Last 24 hours" in HOME
    assert "30-minute intervals" in HOME
    assert "Selected period" not in HOME


def test_camera_reason_is_human_readable_and_unavailable_card_is_compact():
    assert "Camera provider is not available." in CAMERAS
    assert "safe(reasonText(camera.unavailable_reason))" in CAMERAS
    assert "['Reason', camera.unavailable_reason]" in CAMERAS
    camera_grid = re.search(r"\.camera-grid\{([^}]*)\}", CAMERA_CSS).group(1)
    unavailable_card = re.search(r"\.camera-card\.is-unavailable\{([^}]*)\}", CAMERA_CSS).group(1)
    assert "align-items:start" in camera_grid
    assert "height:fit-content" in unavailable_card
    assert "align-self:start" in unavailable_card


def test_electricity_primary_layout_and_kpis_are_unchanged():
    assert "Daily Energy Usage — Current Billing Cycle" in ELECTRICITY
    assert "cycle-chart-average-line" in ELECTRICITY
    assert "Today is partial" in ELECTRICITY
    for label in ("Current Cycle Cost", "Current-Pace Bill Estimate", "Cycle Usage", "Cycle Ends In"):
        assert label in ELECTRICITY
    primary = re.search(r'return `<section class="electricity-cycle-chart">(.*?)</section>`;', ELECTRICITY, re.S).group(1)
    assert "power integration" not in primary.lower()
    assert "power integration from authoritative history" in ELECTRICITY
    assert "Advanced Diagnostics" in ELECTRICITY


def test_responsive_navigation_has_no_horizontal_rail():
    assert "repeat(5,minmax(0,1fr))" in M2_CSS
    assert "overflow-x:hidden" in M2_CSS
    for width in (1024, 768, 390):
        assert f"@media(max-width:{width}px)" in M2_CSS
    assert "grid-template-columns:minmax(0,1fr)" in M2_CSS
    assert "@media(max-width:480px)" in M2_CSS
    assert "calc(152px + env(safe-area-inset-bottom))" in M2_CSS
    assert "scroll-padding-bottom:calc(120px + env(safe-area-inset-bottom))" in M2_CSS
    assert "min-height:44px" in M2_CSS
    assert "touch-action:manipulation" in M2_CSS


def test_mobile_primary_icons_are_inline_and_top_actions_are_not_blank():
    assert M2.count('class="primary-nav-icon"') == 5
    assert M2.count('<svg class="primary-nav-icon"') == 5
    assert "NAV_ICONS[route]" in M2
    assert "data-lucide" not in M2[M2.index("const NAV_ICONS"):M2.index("function normalizeStatus")]
    assert not re.search(r'https?://|cdn|unpkg', M2, re.I)
    notifications = (ROOT / "frontend/assets/dashboard_notifications.js").read_text()
    assert 'aria-label\', \'Notifications' in notifications
    assert 'class="top-action-icon"' in notifications
    topbar = re.search(r'<header class="topbar.*?</header>', INDEX, re.S).group(0)
    assert 'aria-label="Refresh dashboard"' in topbar
    assert 'aria-label="More dashboard options"' in topbar
    assert topbar.count('class="top-action-icon"') == 2


def test_grouped_routes_mark_more_as_current():
    assert "history: 'more'" in M2
    assert "button.setAttribute('aria-current', 'page')" in M2


def test_milestone_asset_loads_after_dynamic_route_installers():
    assert INDEX.index("dashboard_automation.js") < INDEX.index("dashboard_milestone2.js")
    assert INDEX.index("dashboard_cameras.js") < INDEX.index("dashboard_milestone2.js")
    assert INDEX.index("dashboard_electricity.js") < INDEX.index("dashboard_milestone2.js")
