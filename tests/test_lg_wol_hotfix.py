import json
from pathlib import Path

import bcrypt
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import lg_tv_control as control


def _auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "wol-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "wol-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    login = client.post("/api/auth/login", json={"username": "wol-test", "password": "password"})
    assert login.status_code == 200
    return client, login.json()["csrf_token"]


def test_mac_validation_accepts_verified_shape_and_rejects_unsafe_values():
    assert control._valid_mac("58:96:0A:9D:1C:0F") is True
    assert control._valid_mac("58-96-0a-9d-1c-0f") is True
    for value in (
        "", "58:96:0A:9D:1C", "58:96:0A:9D:1C:GG",
        "00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF", "59:96:0A:9D:1C:0F",
    ):
        assert control._valid_mac(value) is False


def test_magic_packet_has_standard_header_and_sixteen_mac_repetitions():
    raw = bytes.fromhex("58960A9D1C0F")
    packet = control._magic_packet("58:96:0A:9D:1C:0F")
    assert packet == b"\xff" * 6 + raw * 16
    assert len(packet) == 102


def test_wake_binds_and_sends_to_lan_broadcast_on_both_interfaces(monkeypatch):
    sent = []

    class FakeSocket:
        def __init__(self, number):
            self.number = number

        def settimeout(self, value):
            sent.append(("timeout", self.number, value))

        def setsockopt(self, *values):
            sent.append(("option", self.number, values))

        def sendto(self, packet, address):
            sent.append(("send", self.number, packet, address))

        def close(self):
            sent.append(("close", self.number))

    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    sockets = []

    def create_socket(*args):
        sock = FakeSocket(len(sockets))
        sockets.append(sock)
        return sock

    monkeypatch.setattr(control.socket, "socket", create_socket)
    control._wake()
    sends = [entry for entry in sent if entry[0] == "send"]
    assert len(sends) == 2
    assert all(entry[3] == ("192.168.1.255", 9) for entry in sends)
    assert all(len(entry[2]) == 102 for entry in sends)
    bind_to_device = getattr(control.socket, "SO_BINDTODEVICE", 25)
    bindings = [
        entry[2][2]
        for entry in sent
        if entry[0] == "option" and entry[2][1] == bind_to_device
    ]
    assert bindings == [b"wlx6c4cbcdb7033\0", b"wlan0\0"]
    assert [entry for entry in sent if entry[0] == "timeout"] == [
        ("timeout", 0, control.COMMAND_TIMEOUT_SEC),
        ("timeout", 1, control.COMMAND_TIMEOUT_SEC),
    ]
    assert [entry for entry in sent if entry[0] == "close"] == [("close", 0), ("close", 1)]


def test_wake_still_attempts_second_interface_if_first_fails(monkeypatch):
    attempts = []

    class FakeSocket:
        def __init__(self, number):
            self.number = number

        def settimeout(self, value):
            pass

        def setsockopt(self, level, option, value):
            if option == getattr(control.socket, "SO_BINDTODEVICE", 25):
                attempts.append(value)
                if self.number == 0:
                    raise OSError("interface unavailable")

        def sendto(self, packet, address):
            attempts.append(address)

        def close(self):
            pass

    sockets = []
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(
        control.socket,
        "socket",
        lambda *args: sockets.append(FakeSocket(len(sockets))) or sockets[-1],
    )
    control._wake()
    assert attempts == [
        b"wlx6c4cbcdb7033\0",
        b"wlan0\0",
        ("192.168.1.255", 9),
    ]


def test_power_on_is_unavailable_without_valid_external_setting(monkeypatch):
    monkeypatch.delenv("LG_TV_MAC", raising=False)
    monkeypatch.delenv("TV_MAC", raising=False)
    monkeypatch.setattr(control, "TV_MAC", "")
    response = control.lg_tv_command(control.TvCommand(command="power_on"))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert json.loads(response.body)["reason"] == "mac_not_configured"
    assert control.capabilities(enumerate_live=False)["power_on"]["supported"] is False


def test_reconnect_uses_bounded_attempts_and_backoff(monkeypatch):
    attempts = []
    sleeps = []
    monkeypatch.setattr(control, "WOL_RECONNECT_ATTEMPTS", 3)
    monkeypatch.setattr(control.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(
        control.status,
        "_public_status",
        lambda: {"online": False},
    )
    monkeypatch.setattr(
        control,
        "_open_client",
        lambda key, timeout: attempts.append((key, timeout)) or (_ for _ in ()).throw(TimeoutError()),
    )
    state, refreshed, count = control._reconnect_after_wol("key")
    assert state == {"online": False}
    assert refreshed is False
    assert count == 3
    assert attempts == [("key", control.WOL_RECONNECT_TIMEOUT_SEC)] * 3
    assert sleeps == [0.5, 1.0, 2.0]


def test_successful_wol_reconnect_closes_temporary_webos_client(monkeypatch):
    client = type("Client", (), {"closed": False, "close": lambda self: setattr(self, "closed", True)})()
    monkeypatch.setattr(control, "WOL_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(control.time, "sleep", lambda delay: None)
    monkeypatch.setattr(control, "_open_client", lambda key, timeout: client)
    monkeypatch.setattr(control.status, "_persist", lambda: None)
    monkeypatch.setattr(control.status, "_public_status", lambda: {"online": True})
    state, refreshed, count = control._reconnect_after_wol("key")
    assert state == {"online": True}
    assert refreshed is True and count == 1
    assert client.closed is True


def test_power_on_sends_one_packet_then_returns_reconnected_state(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    wake_calls = []
    monkeypatch.setattr(control, "_wake", lambda: wake_calls.append("wake"))
    monkeypatch.setattr(
        control,
        "_reconnect_after_wol",
        lambda key: ({"online": True, "connection_state": "connected"}, True, 2),
    )
    result = control.lg_tv_command(control.TvCommand(command="power_on"))
    assert result["ok"] is True
    assert result["wol_sent"] is True
    assert result["state_refreshed"] is True
    assert result["reconnect_attempts"] == 2
    assert wake_calls == ["wake"]
    assert control.wol_diagnostics()["last_wol_result"] == "reconnected"


def test_power_on_does_not_send_duplicate_wol_when_tv_is_already_online(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: True)
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control, "_wake", lambda: (_ for _ in ()).throw(AssertionError("duplicate WOL")))
    monkeypatch.setattr(control, "_refresh", lambda key: ({"online": True}, True))
    result = control.lg_tv_command(control.TvCommand(command="power_on"))
    assert result["wol_sent"] is False
    assert result["state_refreshed"] is True
    assert control.wol_diagnostics()["last_wol_result"] == "already_online"


def test_concurrent_power_on_is_rejected_before_duplicate_packet(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(control, "_wake", lambda: (_ for _ in ()).throw(AssertionError("duplicate WOL")))
    assert control.COMMAND_LOCK.acquire(blocking=False)
    try:
        response = control.lg_tv_command(control.TvCommand(command="power_on"))
    finally:
        control.COMMAND_LOCK.release()
    assert response.status_code == 409


def test_wol_diagnostics_are_authenticated_and_never_expose_mac(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: False)
    monkeypatch.setattr(control.status, "_service_active", lambda: True)
    client, _ = _auth_client(monkeypatch)
    client.cookies.clear()
    assert client.get("/api/lg-tv/status/diagnostics").status_code == 401
    client, _ = _auth_client(monkeypatch)
    response = client.get("/api/lg-tv/status/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert {
        "wol_configured", "last_wol_sent_at", "reconnect_attempts", "last_wol_result",
    } <= set(payload)
    assert "58:96" not in repr(payload)
    assert "mac" not in json.dumps(payload).lower()


def test_power_on_route_preserves_authentication_and_csrf(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    client, csrf = _auth_client(monkeypatch)
    client.cookies.clear()
    assert client.post("/api/lg-tv/command", json={"command": "power_on"}).status_code == 401
    client, csrf = _auth_client(monkeypatch)
    assert client.post("/api/lg-tv/command", json={"command": "power_on"}).status_code == 403


def test_frontend_keeps_power_on_visible_with_exact_disabled_reason():
    source = (
        Path(control.app_module.FRONTEND_DIR) / "assets" / "dashboard_lg_remote.js"
    ).read_text(encoding="utf-8")
    reason = "Wake-on-LAN is not configured. Add the TV MAC address to enable Power On."
    assert "button('power_on', 'Power On'" in source
    assert reason in source


def test_household_renderer_cannot_overwrite_live_wol_capabilities():
    assets = Path(control.app_module.FRONTEND_DIR) / "assets"
    household = (assets / "dashboard_household_devices.js").read_text(encoding="utf-8")
    status = (assets / "dashboard_lg_status.js").read_text(encoding="utf-8")
    assert "renderLgCompactRemote" not in household
    assert "renderLgTvCompact" not in household
    assert status.count("window.renderLgCompactRemote?.(state.capabilities || {})") == 1
    assert "request('/api/lg-tv/capabilities')" in status
