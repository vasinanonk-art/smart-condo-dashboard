import asyncio
import inspect
import threading
import time

import backend.sonoff_client as client


def _reset_cache():
    client._cache.update(
        {
            "devices": [],
            "raw_devices": {},
            "last_sync_ts": None,
            "auth": {"at": "cached-token", "appid": "test-app", "region": "as"},
            "auth_expires_at": int(time.time()) + 3600,
            "auth_status": "authenticated",
            "last_error": None,
            "config_loaded": True,
            "config_path": "/test/ewelink.local.json",
            "refresh_diag": None,
        }
    )
    client._device_locks.clear()
    client._device_intents.clear()
    client._device_generations.clear()


def _cached_devices():
    devices = client.configured_devices({})
    for device in devices:
        device["online"] = True
    return devices


def _prepare_command(monkeypatch):
    _reset_cache()
    client._cache["devices"] = _cached_devices()
    monkeypatch.setattr(
        client,
        "config_payload",
        lambda: {"loaded": True, "path": "/test/ewelink.local.json", "config": {"region": "as"}},
    )


def test_valid_session_is_reused_without_login_request(monkeypatch):
    _reset_cache()
    calls = []
    monkeypatch.setattr(client, "request_json", lambda *args, **kwargs: calls.append(args) or {})

    first = client.login({"region": "as"})
    second = client.login({"region": "as"})

    assert first is second
    assert calls == []


def test_command_has_no_pre_refresh_or_fixed_sleep(monkeypatch):
    _prepare_command(monkeypatch)
    refresh_calls = []
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})

    def refresh(*args):
        refresh_calls.append(args)
        return True, client.patch_local_state(_cached_devices(), next(iter(client.EXPECTED)), "BASICR2", {1: "on"}), {}

    monkeypatch.setattr(client, "refresh_live_devices", refresh)
    device_id = next(iter(client.EXPECTED))

    result = client.set_state(device_id, "on", 1)

    assert result["ok"] is True
    assert len(refresh_calls) == 1
    assert "time.sleep" not in inspect.getsource(client.set_state)


def test_command_returns_updated_device_and_confirmation(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})
    monkeypatch.setattr(client, "refresh_live_devices", lambda *args: (False, [], {"refresh_success": False}))

    result = client.set_state(device_id, "on", 1)

    assert result["ok"] is True
    assert result["device"]["deviceid"] == device_id
    assert result["device"]["channel_states"][1] == "on"
    assert result["state_confirmed"] is False
    assert result["confirmation"] == "patched_fallback"


def test_refresh_matching_requested_state_is_confirmed(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    live = client.patch_local_state(_cached_devices(), device_id, "BASICR2", {1: "on"})
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})
    monkeypatch.setattr(
        client,
        "refresh_live_devices",
        lambda *args: (True, live, {"refresh_success": True, "_live_devices": live}),
    )

    result = client.set_state(device_id, "on", 1)

    assert result["state_confirmed"] is True
    assert result["confirmation"] == "cloud_confirmed"
    assert result["device"]["channel_states"][1] == "on"


def test_refresh_stale_state_returns_patched_unconfirmed_state(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    stale = _cached_devices()
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})
    monkeypatch.setattr(
        client,
        "refresh_live_devices",
        lambda *args: (True, stale, {"refresh_success": True, "_live_devices": stale}),
    )

    result = client.set_state(device_id, "on", 1)

    assert result["state_confirmed"] is False
    assert result["confirmation"] == "patched_unconfirmed"
    assert result["device"]["channel_states"][1] == "on"


def test_refresh_missing_target_returns_patched_unconfirmed_state(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    other_devices = [item for item in _cached_devices() if item["deviceid"] != device_id]
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})
    monkeypatch.setattr(
        client,
        "refresh_live_devices",
        lambda *args: (True, other_devices, {"refresh_success": True, "_live_devices": other_devices}),
    )

    result = client.set_state(device_id, "on", 1)

    assert result["state_confirmed"] is False
    assert result["confirmation"] == "patched_unconfirmed"
    assert result["device"]["channel_states"][1] == "on"


def test_post_payload_exposes_updated_device_state(monkeypatch):
    import sonoff_client as route

    device_id = next(iter(client.EXPECTED))
    updated = {"deviceid": device_id, "name": "Test", "channel_states": {1: "on"}}
    result = {
        "ok": True,
        "deviceid": device_id,
        "channel": 1,
        "action": "on",
        "auth_status": "authenticated",
        "last_error": None,
        "devices": [updated],
        "device": updated,
        "state_confirmed": False,
        "confirmation": "patched_fallback",
    }

    class Request:
        async def json(self):
            return {"deviceid": device_id, "channel": 1, "action": "on"}

    async def in_threadpool(function, *args):
        return function(*args)

    monkeypatch.setattr(route, "set_state", lambda *args: result)
    monkeypatch.setattr(route, "config_payload", lambda: {"path": "/test/ewelink.local.json"})
    monkeypatch.setattr(route, "run_in_threadpool", in_threadpool)

    payload = asyncio.run(route._sonoff_post_handler(Request()))

    assert payload["device"] == updated
    assert payload["state_confirmed"] is False
    assert payload["confirmation"] == "patched_fallback"


def test_failed_command_does_not_patch_cached_state(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    before = [dict(device, channel_states=dict(device["channel_states"])) for device in client._cache["devices"]]
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 4002, "_http_status": 200})
    monkeypatch.setattr(client, "refresh_live_devices", lambda *args: (_ for _ in ()).throw(AssertionError("refresh must not run")))

    result = client.set_state(device_id, "on", 1)

    assert result["ok"] is False
    assert client._cache["devices"] == before


def test_commands_to_different_devices_are_independent(monkeypatch):
    _prepare_command(monkeypatch)
    device_ids = list(client.EXPECTED)[:2]
    barrier = threading.Barrier(2, timeout=2)
    entered = []

    def command(*args):
        entered.append(args[2])
        barrier.wait()
        return {"error": 0, "_http_status": 200}

    monkeypatch.setattr(client, "_command_request", command)
    monkeypatch.setattr(client, "refresh_live_devices", lambda *args: (False, [], {"refresh_success": False}))
    results = {}

    def run(device_id):
        results[device_id] = client.set_state(device_id, "on", 1)

    threads = [threading.Thread(target=run, args=(device_id,)) for device_id in device_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(entered) == sorted(device_ids)
    assert all(results[device_id]["ok"] for device_id in device_ids)


def test_concurrent_expired_session_commands_login_once(monkeypatch):
    _prepare_command(monkeypatch)
    client._cache["auth"] = None
    client._cache["auth_expires_at"] = None
    device_ids = list(client.EXPECTED)[:2]
    login_count = 0
    login_entered = threading.Event()

    def perform_login(cfg):
        nonlocal login_count
        login_count += 1
        login_entered.set()
        time.sleep(0.05)
        return client._store_auth({"at": "new-token", "appid": "test-app", "region": "as"})

    monkeypatch.setattr(client, "_login_uncached", perform_login)
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})
    monkeypatch.setattr(client, "refresh_live_devices", lambda *args: (False, [], {"refresh_success": False}))
    results = {}
    threads = [
        threading.Thread(target=lambda device_id=device_id: results.setdefault(device_id, client.set_state(device_id, "on", 1)))
        for device_id in device_ids
    ]
    for thread in threads:
        thread.start()
    assert login_entered.wait(timeout=1)
    for thread in threads:
        thread.join(timeout=2)

    assert login_count == 1
    assert all(results[device_id]["ok"] for device_id in device_ids)


def test_concurrent_commands_preserve_both_resulting_states(monkeypatch):
    _prepare_command(monkeypatch)
    device_ids = list(client.EXPECTED)[:2]
    refresh_barrier = threading.Barrier(2, timeout=2)
    monkeypatch.setattr(client, "_command_request", lambda *args: {"error": 0, "_http_status": 200})

    def refresh(cfg, auth):
        refresh_barrier.wait()
        stale = _cached_devices()
        live = client.patch_local_state(stale, device_ids[0], client.expected_model(device_ids[0]), {1: "on"})
        live = client.patch_local_state(live, device_ids[1], client.expected_model(device_ids[1]), {1: "on", 2: "off"})
        raw_map = {item["deviceid"]: {"deviceid": item["deviceid"]} for item in live}
        merged = client._merge_refreshed_devices(raw_map, live)
        return True, merged, {"refresh_success": True, "_live_devices": live}

    monkeypatch.setattr(client, "refresh_live_devices", refresh)
    results = {}
    threads = [
        threading.Thread(target=lambda device_id=device_id: results.setdefault(device_id, client.set_state(device_id, "on", 1)))
        for device_id in device_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    final = {item["deviceid"]: item for item in client.fallback_devices({})}
    assert all(results[device_id]["ok"] for device_id in device_ids)
    assert final[device_ids[0]]["channel_states"][1] == "on"
    assert final[device_ids[1]]["channel_states"][1] == "on"


def test_stale_refresh_does_not_overwrite_newer_command_state(monkeypatch):
    _prepare_command(monkeypatch)
    device_ids = list(client.EXPECTED)[:2]
    target = device_ids[1]
    client._store_command_intent(target, client.expected_model(target), {1: "on", 2: "off"}, {})
    stale = _cached_devices()
    raw_map = {item["deviceid"]: {"deviceid": item["deviceid"]} for item in stale}

    merged = client._merge_refreshed_devices(raw_map, stale)
    preserved = next(item for item in merged if item["deviceid"] == target)

    assert preserved["channel_states"][1] == "on"
    assert client._device_intents[target]["states"][1] == "on"


def test_older_refresh_finishing_last_is_ignored():
    _reset_cache()
    device_id = next(iter(client.EXPECTED))
    client._cache["devices"] = _cached_devices()
    older_generation = client._begin_refresh()
    newer_generation = client._begin_refresh()
    newer = client.patch_local_state(_cached_devices(), device_id, client.expected_model(device_id), {1: "on"})
    older = _cached_devices()

    client._merge_refreshed_devices({}, newer, newer_generation)
    client._merge_refreshed_devices({}, older, older_generation)

    current = next(item for item in client.fallback_devices({}) if item["deviceid"] == device_id)
    assert current["channel_states"][1] == "on"


def test_newer_refresh_wins_and_confirmed_state_remains_confirmed():
    _reset_cache()
    device_id = next(iter(client.EXPECTED))
    client._cache["devices"] = _cached_devices()
    client._increment_device_generation(device_id)
    client._store_command_intent(device_id, client.expected_model(device_id), {1: "on"}, {})
    older_generation = client._begin_refresh()
    newer_generation = client._begin_refresh()
    confirmed = client.patch_local_state(_cached_devices(), device_id, client.expected_model(device_id), {1: "on"})
    stale = _cached_devices()

    client._merge_refreshed_devices({}, confirmed, newer_generation)
    assert device_id not in client._device_intents
    client._merge_refreshed_devices({}, stale, older_generation)

    current = next(item for item in client.fallback_devices({}) if item["deviceid"] == device_id)
    assert current["channel_states"][1] == "on"
    assert client._device_generations[device_id] == newer_generation[device_id]


def test_sequential_multigang_command_prefers_unconfirmed_patched_state(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = list(client.EXPECTED)[1]
    raw = {
        "deviceid": device_id,
        "name": "M5-2C-120W",
        "model": "M5-2C-120W",
        "online": True,
        "params": {
            "switches": [
                {"outlet": 0, "switch": "on"},
                {"outlet": 1, "switch": "off"},
            ]
        },
    }
    client._cache["raw_devices"] = {device_id: raw}
    client._cache["devices"] = [client.public_device(raw)]
    requests = []
    stale = [client.public_device(raw)]

    def command(cfg, auth, target, params):
        requests.append(params)
        return {"error": 0, "_http_status": 200}

    monkeypatch.setattr(client, "_command_request", command)
    monkeypatch.setattr(
        client,
        "refresh_live_devices",
        lambda *args: (True, stale, {"refresh_success": True, "_live_devices": stale}),
    )

    first = client.set_state(device_id, "off", 1)
    second = client.set_state(device_id, "on", 2)

    assert first["state_confirmed"] is False
    assert second["state_confirmed"] is False
    assert requests[1]["switches"] == [
        {"outlet": 0, "switch": "off"},
        {"outlet": 1, "switch": "on"},
    ]


def test_confirmed_multigang_state_reenables_raw_state_source():
    _reset_cache()
    device_id = list(client.EXPECTED)[1]
    confirmed_raw = {
        "deviceid": device_id,
        "model": "M5-2C-120W",
        "params": {
            "switches": [
                {"outlet": 0, "switch": "off"},
                {"outlet": 1, "switch": "on"},
            ]
        },
    }
    confirmed = client.public_device(confirmed_raw)
    client._store_command_intent(device_id, client.expected_model(device_id), {1: "off", 2: "on"}, {})
    generation = client._begin_refresh()
    client._merge_refreshed_devices({device_id: confirmed_raw}, [confirmed], generation)

    items, raw, has_active_intent = client._cached_command_context({}, device_id)
    states = client.best_current_states(device_id, client.expected_model(device_id), raw, items, 2)

    assert has_active_intent is False
    assert states == {1: "off", 2: "on"}


def test_same_device_commands_are_serialized(monkeypatch):
    _prepare_command(monkeypatch)
    device_id = next(iter(client.EXPECTED))
    command_entered = threading.Event()
    release_command = threading.Event()

    def command(*args):
        command_entered.set()
        assert release_command.wait(timeout=2)
        return {"error": 0, "_http_status": 200}

    monkeypatch.setattr(client, "_command_request", command)
    monkeypatch.setattr(client, "refresh_live_devices", lambda *args: (False, [], {"refresh_success": False}))
    first_result = {}
    thread = threading.Thread(target=lambda: first_result.update(client.set_state(device_id, "on", 1)))
    thread.start()
    assert command_entered.wait(timeout=1)

    second = client.set_state(device_id, "off", 1)
    release_command.set()
    thread.join(timeout=2)

    assert second["ok"] is False
    assert second["error"] == "sonoff command already in progress"
    assert first_result["ok"] is True


def test_active_frontend_uses_post_response_without_duplicate_get():
    source = open("frontend/assets/dashboard_v3.js", encoding="utf-8").read()
    start = source.index("async function sonoff(deviceId")
    end = source.index("async function sonoffDevice", start)
    handler = source[start:end]

    assert "applySonoffResponse(response, deviceId)" in handler
    assert "await loadSonoff()" not in handler
    assert "restoreSonoffGang(deviceId, channel)" in handler


def test_bulk_frontend_does_not_refetch_after_successful_post():
    source = open("frontend/sonoff_bulk.js", encoding="utf-8").read()
    start = source.index("async function refreshAfterBulk")
    end = source.index("async function sonoffBulkAll", start)

    assert "getJson('/api/sonoff')" not in source[start:end]
