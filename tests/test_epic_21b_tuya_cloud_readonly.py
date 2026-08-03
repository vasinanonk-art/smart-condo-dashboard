import json
import threading
import urllib.error

import pytest

from backend import household_device_registry
from backend import smartlife_ir_discovery as discovery
from backend import tuya_cloud_readonly as cloud


DEVICE_ID = "configured-device-123"


def _configure(monkeypatch):
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "smartlife_cloud")
    monkeypatch.setenv("TUYA_CLOUD_ACCESS_ID", "test-access-id")
    monkeypatch.setenv("TUYA_CLOUD_ACCESS_SECRET", "test-access-secret")
    monkeypatch.setenv("TUYA_CLOUD_DEVICE_ID", DEVICE_ID)
    monkeypatch.setenv("TUYA_CLOUD_REGION", "sg")
    discovery.invalidate_cache()


def _config():
    return cloud.TuyaCloudConfig(
        access_id="client",
        access_secret="secret",
        device_id=DEVICE_ID,
        region="sg",
        endpoint="https://openapi-sg.iotbing.com",
        timeout_sec=2,
    )


def test_official_hmac_signing_fixture():
    assert cloud._signature(
        "secret",
        "client",
        "1680000000000",
        "/v1.0/token?grant_type=1",
    ) == "A66498314AD60889C76CABD3BAFB78BBC73A540D7DC21E2C487A8ECD2B3D2CA5"
    assert cloud._signature(
        "secret",
        "client",
        "1680000000000",
        f"/v1.0/iot-03/devices/{DEVICE_ID}/status",
        "token",
    ) == "58B3BDBD679E3E86AA66875515219500B8321145A5E36901404ED1126F15FC68"


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def test_token_success_and_failure_envelope(monkeypatch):
    client = cloud.TuyaCloudReadOnlyClient(_config())
    calls = []

    def urlopen(request, timeout):
        calls.append((request, timeout))
        return _Response({
            "success": True,
            "result": {"access_token": "temporary-token", "expire_time": 7200},
        })

    monkeypatch.setattr(cloud.urllib.request, "urlopen", urlopen)
    assert client._token() == "temporary-token"
    assert calls[0][0].full_url.endswith("/v1.0/token?grant_type=1")
    assert calls[0][0].get_method() == "GET"
    assert calls[0][1] == 2

    failed = cloud.TuyaCloudReadOnlyClient(_config())
    monkeypatch.setattr(
        cloud.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({
            "success": False,
            "code": 1106,
            "msg": "permission denied",
        }),
    )
    with pytest.raises(cloud.TuyaCloudError, match="tuya_cloud_api_error"):
        failed._token()


def test_expired_token_refresh_is_single_flight(monkeypatch):
    client = cloud.TuyaCloudReadOnlyClient(_config())
    calls = 0
    barrier = threading.Barrier(3)
    original = client._raw_get

    def token_response(path, access_token):
        nonlocal calls
        calls += 1
        return {
            "success": True,
            "result": {"access_token": "shared-token", "expire_time": 60},
        }

    monkeypatch.setattr(client, "_raw_get", token_response)
    results = []

    def worker():
        barrier.wait()
        results.append(client._token())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(1)
    assert results == ["shared-token", "shared-token"]
    assert calls == 1
    client._token_valid_until = 0
    assert client._token() == "shared-token"
    assert calls == 2
    monkeypatch.setattr(client, "_raw_get", original)


def test_timeout_and_api_error_are_safe(monkeypatch):
    client = cloud.TuyaCloudReadOnlyClient(_config())
    monkeypatch.setattr(
        cloud.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(cloud.TuyaCloudError, match="tuya_cloud_timeout"):
        client._raw_get("/v1.0/token?grant_type=1", "")

    monkeypatch.setattr(
        cloud.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        ),
    )
    with pytest.raises(cloud.TuyaCloudError, match="tuya_cloud_unavailable"):
        client._raw_get("/v1.0/token?grant_type=1", "")


def test_only_allowlisted_get_paths_are_callable():
    client = cloud.TuyaCloudReadOnlyClient(_config())
    with pytest.raises(cloud.TuyaCloudError, match="method_not_allowed"):
        client.request("POST", f"/v1.0/iot-03/devices/{DEVICE_ID}/commands")
    with pytest.raises(cloud.TuyaCloudError, match="path_not_allowed"):
        client.request("GET", "/v1.0/iot-03/devices/another-device/status")


def test_cloud_inventory_normalizes_specification_and_redacts(monkeypatch):
    _configure(monkeypatch)

    class FakeClient:
        config = _config()

        def device_information(self):
            return {"success": True, "result": {
                "id": DEVICE_ID,
                "uuid": "private-uuid",
                "local_key": "private-local-key",
                "ip": "192.0.2.10",
                "category": "wnykq",
                "product_id": "private-product-id",
                "product_name": "IR Remote Control with T&H",
                "model": "Verified model",
                "online": True,
            }}

        def device_specification(self):
            return {"success": True, "result": {
                "category": "wnykq",
                "status": [
                    {"code": "temp_current", "type": "Integer"},
                    {"code": "humidity_value", "type": "Integer"},
                    {"code": "ir_raw", "type": "Raw"},
                ],
                "functions": [{"code": "ir_send", "type": "Raw"}],
            }}

        def device_status(self):
            return {"success": True, "result": [
                {"code": "temp_current", "value": 251},
                {"code": "humidity_value", "value": 61},
                {"code": "ir_raw", "value": "opaque-secret-payload"},
                {"code": "unknown_dp", "value": 42},
            ]}

    monkeypatch.setattr(cloud, "configured_client", lambda: FakeClient())
    payload = discovery.inventory(force=True)
    device = payload["devices"][0]
    assert payload["provider_detected"] is True
    assert payload["available_capabilities"] == []
    assert device["online"] is True
    assert device["supported_command_categories"] == []
    assert device["state"] == {"humidity_value": 61, "temp_current": 251}
    assert {item["code"] for item in device["dp_metadata"]} == {
        "humidity_value", "ir_raw", "ir_send", "temp_current"
    }
    assert all(item["writable"] is False for item in device["dp_metadata"])
    rendered = json.dumps(payload)
    for secret in (
        DEVICE_ID,
        "private-uuid",
        "private-local-key",
        "192.0.2.10",
        "private-product-id",
        "opaque-secret-payload",
    ):
        assert secret not in rendered


def test_missing_dp_metadata_keeps_inventory_non_controllable_without_verified_driver(
    monkeypatch,
):
    _configure(monkeypatch)

    class FakeClient:
        config = _config()

        def device_information(self):
            return {"result": {
                "id": DEVICE_ID,
                "category": "wnykq",
                "product_name": "IR Remote Control with T&H",
                "online": True,
            }}

        def device_specification(self):
            return {"result": {"category": "wnykq", "status": []}}

        def device_functions(self):
            return {"result": {"category": "wnykq", "functions": []}}

        def device_status(self):
            return {"result": []}

    monkeypatch.setattr(cloud, "configured_client", lambda: FakeClient())
    payload = discovery.inventory(force=True)
    assert payload["devices"][0]["discovery_reason"] == "tuya_cloud_dp_metadata_incomplete"
    assert payload["available_capabilities"] == []

    monkeypatch.setattr(household_device_registry, "_tapo_detected", lambda: False)
    monkeypatch.setattr(
        household_device_registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {"bridge_online": None, "remotes": []},
    )
    public_devices = household_device_registry.ir_framework.public_devices
    monkeypatch.setattr(
        household_device_registry.ir_framework,
        "public_devices",
        lambda: [
            {
                **item,
                "runtime_status": {
                    **(item.get("runtime_status") or {}),
                    "healthy": False,
                },
            }
            for item in public_devices()
        ],
    )
    bedroom = next(
        item for item in household_device_registry._ir_devices()
        if item["id"] == "bed-room-air-conditioner"
    )
    assert bedroom["capabilities"] == {}
