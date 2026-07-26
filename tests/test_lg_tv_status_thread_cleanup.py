import sys
import threading
import types

import pytest

from backend import lg_tv_status


def _install_fake_webos(
    monkeypatch,
    *,
    register_error=None,
    close_error=None,
    threaded=False,
    stubborn_thread=False,
):
    clients = []

    class StubbornThread:
        def __init__(self):
            self.join_timeouts = []

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)

    class FakeClient:
        REGISTERED = 2
        PROMPTED = 1

        def __init__(self, host, secure=False):
            del host, secure
            self.closed = False
            self._stop = threading.Event()
            self._th = None
            self.sock = types.SimpleNamespace(settimeout=lambda timeout: None)
            clients.append(self)

        def connect(self):
            if stubborn_thread:
                self._th = StubbornThread()
            elif threaded:
                self._th = threading.Thread(
                    target=self._stop.wait,
                    name="WebSocketClient-test",
                    daemon=True,
                )
                self._th.start()

        def register(self, store, timeout=None):
            del store, timeout
            if register_error:
                raise register_error
            yield self.REGISTERED

        def close(self):
            self.closed = True
            self._stop.set()
            if close_error:
                raise close_error

    class ApplicationControl:
        def __init__(self, client):
            del client

        def get_current(self):
            return {"appId": "com.webos.app.hdmi1"}

    class MediaControl:
        def __init__(self, client):
            del client

        def get_volume(self):
            return {"volume": 10, "muted": False}

    class SourceControl:
        def __init__(self, client):
            del client

        def get_current(self):
            return {"appId": "com.webos.app.hdmi1"}

    class SystemControl:
        def __init__(self, client):
            del client

        def info(self):
            return {"deviceName": "TV"}

    connection = types.ModuleType("pywebostv.connection")
    connection.WebOSClient = FakeClient
    controls = types.ModuleType("pywebostv.controls")
    controls.ApplicationControl = ApplicationControl
    controls.MediaControl = MediaControl
    controls.SourceControl = SourceControl
    controls.SystemControl = SystemControl
    monkeypatch.setitem(sys.modules, "pywebostv.connection", connection)
    monkeypatch.setitem(sys.modules, "pywebostv.controls", controls)
    return clients


def test_successful_poll_closes_client(monkeypatch):
    clients = _install_fake_webos(monkeypatch)

    result = lg_tv_status._collect_live("key")

    assert result["connection_state"] == "connected"
    assert clients[0].closed is True


def test_polling_exception_still_closes_client(monkeypatch):
    clients = _install_fake_webos(monkeypatch, register_error=RuntimeError("poll failed"))

    with pytest.raises(RuntimeError, match="poll failed"):
        lg_tv_status._collect_live("key")

    assert clients[0].closed is True


def test_close_exception_does_not_hide_original_error(monkeypatch, caplog):
    clients = _install_fake_webos(
        monkeypatch,
        register_error=RuntimeError("original polling error"),
        close_error=RuntimeError("close failed"),
        stubborn_thread=True,
    )

    with pytest.raises(RuntimeError, match="original polling error"):
        lg_tv_status._collect_live("key")

    assert clients[0].closed is True
    assert clients[0]._th.join_timeouts == [0.5]
    assert "Failed to close LG WebOS client" in caplog.text
    assert "LG WebOS client thread did not stop after close" in caplog.text


def test_repeated_successful_polls_do_not_leave_websocket_threads(monkeypatch):
    _install_fake_webos(monkeypatch, threaded=True)
    baseline = sum(t.name == "WebSocketClient-test" and t.is_alive() for t in threading.enumerate())

    for _ in range(10):
        lg_tv_status._collect_live("key")

    remaining = sum(t.name == "WebSocketClient-test" and t.is_alive() for t in threading.enumerate())
    assert remaining == baseline


def test_live_thread_after_bounded_join_does_not_replace_success(monkeypatch, caplog):
    clients = _install_fake_webos(monkeypatch, stubborn_thread=True)

    result = lg_tv_status._collect_live("key")

    assert result["connection_state"] == "connected"
    assert clients[0]._th.join_timeouts == [0.5]
    assert "LG WebOS client thread did not stop after close" in caplog.text
