import json

import bcrypt
from fastapi.testclient import TestClient

from backend import device_health
from backend.app_entry import app


def _auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "health-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv(
        "DASHBOARD_SESSION_SECRET",
        "health-test-session-secret-long-enough",
    )
    client = TestClient(app)
    response = client.post(
        "/api/auth/login",
        json={"username": "health-test", "password": "password"},
    )
    assert response.status_code == 200
    return client


def _device(identifier="lamp", *, online=True, health="healthy", updated_at=None):
    return {
        "id": identifier,
        "room": "living_room",
        "display_name": "Living Room Lamp",
        "category": "light",
        "online": online,
        "health": health,
        "capabilities": {},
        "state": {"updated_at": updated_at},
        "state_quality": "confirmed",
        "unavailable_reason": None,
    }


def test_tracker_records_heartbeat_last_seen_and_response_time():
    tracker = device_health.DeviceHealthTracker(stale_after_seconds=30)

    health = tracker.observe(
        _device(updated_at=90),
        response_time_ms=12.345,
        observed_at=100,
    )

    assert health.online is True
    assert health.health == "healthy"
    assert health.health_indicator == "green"
    assert health.heartbeat_at == "1970-01-01T00:01:40Z"
    assert health.last_seen == "1970-01-01T00:01:30Z"
    assert health.response_time_ms == 12.3


def test_tracker_marks_explicit_offline_without_advancing_last_seen():
    tracker = device_health.DeviceHealthTracker(stale_after_seconds=30)
    tracker.observe(_device(updated_at=90), response_time_ms=5, observed_at=100)

    health = tracker.observe(
        _device(online=False, health="unavailable"),
        response_time_ms=7,
        observed_at=110,
    )

    assert health.online is False
    assert health.health == "offline"
    assert health.health_indicator == "red"
    assert health.last_seen == "1970-01-01T00:01:30Z"
    assert health.heartbeat_age_seconds == 10


def test_tracker_expires_unknown_provider_state_after_heartbeat_threshold():
    tracker = device_health.DeviceHealthTracker(stale_after_seconds=30)
    tracker.observe(_device(), response_time_ms=5, observed_at=100)

    still_recent = tracker.observe(
        _device(online=None, health="unknown"),
        response_time_ms=3,
        observed_at=129,
    )
    expired = tracker.observe(
        _device(online=None, health="unknown"),
        response_time_ms=3,
        observed_at=131,
    )

    assert still_recent.online is True
    assert still_recent.health_indicator == "yellow"
    assert expired.online is False
    assert expired.health_indicator == "red"


def test_health_endpoint_is_authenticated_and_returns_safe_model(monkeypatch):
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(
        device_health,
        "_timed_devices",
        lambda: iter([(_device(updated_at=90), 8.2)]),
    )
    device_health.tracker.clear()

    unauthenticated = TestClient(app)
    assert unauthenticated.get("/api/device-health").status_code == 401

    response = client.get("/api/device-health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "total": 1,
        "online": 1,
        "offline": 0,
        "unknown": 0,
        "healthy": 1,
        "degraded": 0,
    }
    assert set(payload["devices"][0]) == {
        "id", "display_name", "room", "category", "health",
        "health_indicator", "online", "heartbeat_at",
        "heartbeat_age_seconds", "last_seen", "response_time_ms",
        "observed_at",
    }
    serialized = json.dumps(payload).casefold()
    assert not any(secret in serialized for secret in (
        "password", "token", "deviceid", "rtsp", "client_key", "mac_address",
    ))


def test_health_snapshot_preserves_registry_order_and_counts(monkeypatch):
    monkeypatch.setattr(
        device_health,
        "_timed_devices",
        lambda: iter([
            (_device("online"), 4),
            (_device("offline", online=False, health="unavailable"), 6),
            (_device("unknown", online=None, health="unknown"), 2),
        ]),
    )
    device_health.tracker.clear()

    payload = device_health.health_snapshot(observed_at=200)

    assert [item["id"] for item in payload["devices"]] == [
        "online", "offline", "unknown",
    ]
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["online"] == 1
    assert payload["summary"]["offline"] == 1
    assert payload["summary"]["unknown"] == 1
