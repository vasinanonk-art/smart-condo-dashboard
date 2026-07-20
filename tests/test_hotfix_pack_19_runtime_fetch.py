import socket
from pathlib import Path

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix19_runtime as runtime

DETAIL_URL = "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"


class _Headers:
    def get_content_type(self):
        return "text/html"

    def get(self, _name):
        return None


class _Response:
    status = 200
    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return DETAIL_URL

    def read(self, _limit):
        return b"<html><body>production detail response</body></html>"


def test_exact_production_detail_timeout_retries_then_recovers(monkeypatch):
    calls = {"count": 0}

    class _Opener:
        def open(self, request, timeout):
            assert request.full_url == DETAIL_URL
            assert timeout == runtime.FETCH_TIMEOUT_SEC
            calls["count"] += 1
            if calls["count"] < 3:
                raise socket.timeout("timed out")
            return _Response()

    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_args: _Opener())
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    runtime.mea._LAST_REMOTE_FETCH = 0.0
    h14._SAFE_DEBUG.clear()

    result = runtime.fetch_official(DETAIL_URL, {"text/html"})

    assert result["http_status"] == 200
    assert calls["count"] == 3
    assert h14._SAFE_DEBUG["fetch_stage"] == "residential_detail"
    assert h14._SAFE_DEBUG["fetch_attempts"] == 3
    assert h14._SAFE_DEBUG["fetch_failure_kind"] is None


def test_terminal_timeout_keeps_public_error_safe_and_diagnostic_specific(monkeypatch):
    class _Opener:
        def open(self, _request, timeout):
            assert timeout == runtime.FETCH_TIMEOUT_SEC
            raise socket.timeout("timed out")

    monkeypatch.setattr(runtime.urllib.request, "build_opener", lambda *_args: _Opener())
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    runtime.mea._LAST_REMOTE_FETCH = 0.0
    h14._SAFE_DEBUG.clear()

    try:
        runtime.fetch_official(DETAIL_URL, {"text/html"})
    except RuntimeError as exc:
        assert str(exc) == "source_fetch_failed"
    else:
        raise AssertionError("expected source_fetch_failed")

    debug = runtime.provider_debug()
    assert debug["fetch_failure_kind"] == "timeout"
    assert debug["fetch_stage"] == "residential_detail"
    assert debug["fetch_attempts"] == runtime.FETCH_ATTEMPTS
    assert "timed out" not in str(debug).lower()


def test_fetch_hardening_preserves_ssrf_redirect_and_size_guards():
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert runtime.mea.ALLOWED_HOSTS == {"www.mea.or.th", "mea.or.th", "opendata.mea.or.th"}
    assert runtime.mea.MAX_REDIRECTS == 3
    assert "url = mea._safe_url(url)" in source
    assert "final_url = mea._safe_url(response.geturl())" in source
    assert "mea.MAX_RESPONSE_BYTES" in source
    assert "ssl.create_default_context()" in source


def test_browser_compatible_headers_do_not_disable_tls_or_allowlist():
    assert "Mozilla/5.0" in runtime._HEADERS["User-Agent"]
    assert runtime._HEADERS["Accept-Encoding"] == "identity"
    assert "th-TH" in runtime._HEADERS["Accept-Language"]
    assert runtime.FETCH_TIMEOUT_SEC == 45
    assert runtime.FETCH_ATTEMPTS == 3
