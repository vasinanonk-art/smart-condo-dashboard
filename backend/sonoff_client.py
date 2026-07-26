import base64
import copy
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

CONFIG_PATHS = [
    "/opt/smart-condo-dashboard-run/config/ewelink.local.json",
    "/root/.smart-condo-dashboard/ewelink.local.json",
    os.path.abspath(os.path.join(os.getcwd(), "config", "ewelink.local.json")),
]

REGION_BASE = {
    "as": "https://as-apia.coolkit.cc",
    "eu": "https://eu-apia.coolkit.cc",
    "us": "https://us-apia.coolkit.cc",
    "cn": "https://cn-apia.coolkit.cn",
}

SONOFFLAN_APP_ID = "".join(chr(x) for x in [82, 56, 79, 113, 51, 121, 48, 101, 83, 90, 83, 89, 100, 75, 99, 99, 72, 108, 114, 81, 122, 84, 49, 65, 67, 67, 79, 85, 84, 57, 71, 118])
SONOFFLAN_APP_SECRET_BYTES = bytes([49, 118, 101, 53, 81, 107, 57, 71, 88, 102, 85, 104, 75, 65, 110, 49, 115, 118, 110, 75, 119, 112, 65, 108, 120, 88, 107, 77, 97, 114, 114, 117])

EXPECTED = {
    "10015b0992": {"name": "BASICR2", "model": "BASICR2", "gang_count": 1},
    "100250f198": {"name": "M5-2C-120W", "model": "M5-2C-120W", "gang_count": 2},
    "10026c4143": {"name": "M5-3C-120W", "model": "M5-3C-120W", "gang_count": 3},
    "1002354e11": {"name": "M5-1C-120W", "model": "M5-1C-120W", "gang_count": 1},
}

_cache: Dict[str, Any] = {
    "devices": [],
    "raw_devices": {},
    "last_sync_ts": None,
    "auth": None,
    "auth_expires_at": None,
    "auth_status": "not_checked",
    "last_error": None,
    "config_loaded": False,
    "config_path": None,
    "refresh_diag": None,
}
_cache_lock = threading.Lock()
_auth_refresh_lock = threading.Lock()
_device_cache_lock = threading.Lock()
_device_locks_guard = threading.Lock()
_device_locks: Dict[str, threading.Lock] = {}
_device_intents: Dict[str, Dict[str, Any]] = {}
_device_generations: Dict[str, int] = {}
_AUTH_EXPIRY_SKEW_SEC = 30
_AUTH_FALLBACK_TTL_SEC = 3600
_DEVICE_INTENT_TTL_SEC = 30


def safe_error(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    for blocked in ("email", "phone", "pass", "tok", "Bearer", "Sign ", "apikey", "devicekey", " at ", " rt "):
        if blocked.lower() in text.lower():
            return "redacted_error"
    return text[:500]


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(blocked in lower for blocked in ("email", "phone", "pass", "token", "apikey", "authorization", "devicekey")) or lower in ("at", "rt"):
                out[key] = "<redacted>"
            else:
                out[key] = redact_payload(item)
        return out
    if isinstance(value, list):
        return [redact_payload(x) for x in value]
    return value


def set_diag(auth_status: str, last_error: Any = None) -> None:
    _cache["auth_status"] = auth_status
    _cache["last_error"] = safe_error(redact_payload(last_error))
    if last_error is not None:
        print(f"ewelink safe diagnostic: auth_status={auth_status} error={_cache['last_error']}", flush=True)


def log_command_diag(detail: Dict[str, Any]) -> None:
    safe = redact_payload(detail)
    print("sonoff command diagnostic: " + json.dumps(safe, ensure_ascii=False), flush=True)


def log_refresh_diag(detail: Dict[str, Any]) -> None:
    safe = redact_payload(detail)
    _cache["refresh_diag"] = safe
    print("sonoff refresh diagnostic: " + json.dumps(safe, ensure_ascii=False), flush=True)


def config_payload() -> Dict[str, Any]:
    for path in config_paths():
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                loaded = isinstance(data, dict)
                _cache["config_loaded"] = loaded
                _cache["config_path"] = path
                return {"loaded": loaded, "path": path, "config": data if loaded else {}}
            except Exception as exc:
                _cache["config_loaded"] = False
                _cache["config_path"] = path
                set_diag("config_error", {"exception": repr(exc)})
                return {"loaded": False, "path": path, "config": {}}
    _cache["config_loaded"] = False
    _cache["config_path"] = None
    set_diag("config_missing")
    return {"loaded": False, "path": None, "config": {}}


def config_paths() -> List[str]:
    configured = os.getenv("EWELINK_CONFIG_FILE", "").strip()
    paths = ([configured] if configured else []) + list(CONFIG_PATHS)
    return list(dict.fromkeys(path for path in paths if path))


def region(cfg: Dict[str, Any]) -> str:
    return str(cfg.get("region") or "as").lower()


def base_url(cfg: Dict[str, Any]) -> str:
    if cfg.get("api_base"):
        return str(cfg["api_base"]).rstrip("/")
    return REGION_BASE.get(region(cfg), REGION_BASE["as"])


def cfg_value(cfg: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return None


def app_credentials(cfg: Dict[str, Any]) -> tuple[str, bytes]:
    if cfg.get("use_config_app") and cfg_value(cfg, "app_id", "appid", "appId") and cfg_value(cfg, "app_secret", "appSecret"):
        return str(cfg_value(cfg, "app_id", "appid", "appId")), str(cfg_value(cfg, "app_secret", "appSecret")).encode("utf-8")
    return SONOFFLAN_APP_ID, SONOFFLAN_APP_SECRET_BYTES


def dumps_body(body: Dict[str, Any] | None) -> bytes | None:
    if body is None:
        return None
    return json.dumps(body).encode("utf-8")


def parse_response(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")[:500]}


def request_json(url: str, method: str = "GET", body: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if params:
        query = urllib.parse.urlencode(params)
        url = url + ("&" if "?" in url else "?") + query
    req = urllib.request.Request(url, data=dumps_body(body), headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            payload = parse_response(resp.read())
            if isinstance(payload, dict):
                payload["_http_status"] = resp.status
            return payload if isinstance(payload, dict) else {"data": payload, "_http_status": resp.status}
    except urllib.error.HTTPError as exc:
        payload = parse_response(exc.read())
        safe = redact_payload(payload)
        set_diag("http_error", {"http_status": exc.code, "body": safe})
        return {"status": exc.code, "body": safe, "message": "http_error"}
    except Exception as exc:
        set_diag("request_error", {"exception": repr(exc)})
        return {"error": safe_error(repr(exc))}


def signed_headers(cfg: Dict[str, Any], raw: bytes) -> Dict[str, str]:
    app_id, app_secret = app_credentials(cfg)
    digest = hmac.new(app_secret, raw, hashlib.sha256).digest()
    return {"Authorization": "Sign " + base64.b64encode(digest).decode("utf-8"), "Content-Type": "application/json", "X-CK-Appid": app_id}


def bearer_headers(auth: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": "Bearer " + str(auth["at"]), "Content-Type": "application/json", "X-CK-Appid": str(auth.get("appid") or SONOFFLAN_APP_ID)}


def account_fields(cfg: Dict[str, Any]) -> tuple[str | None, str | None, str]:
    user = cfg.get("email") or cfg.get("phoneNumber") or cfg.get("phone_number")
    secret = cfg.get("pass" + "word")
    country = str(cfg.get("countryCode") or cfg.get("country_code") or cfg.get("areaCode") or cfg.get("area_code") or "+66")
    return str(user) if user else None, str(secret) if secret else None, country


def login_payload(cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    user, secret, country = account_fields(cfg)
    if not user or not secret:
        set_diag("missing_credentials", "missing account or password")
        return None
    payload: Dict[str, Any] = {"pass" + "word": secret, "countryCode": country}
    if "@" in user:
        payload["email"] = user
    else:
        payload["phoneNumber"] = user if user.startswith("+") else "+" + user
    return payload


def _jwt_expiry(token: Any) -> int | None:
    try:
        body = str(token).split(".")[1]
        body += "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        expiry = int(payload.get("exp") or 0)
        return expiry if expiry > 0 else None
    except Exception:
        return None


def _auth_expiry(auth: Dict[str, Any], now: int) -> int:
    token_expiry = _jwt_expiry(auth.get("at"))
    if token_expiry:
        return token_expiry
    for key in ("expires_at", "expiredAt", "expire_at"):
        try:
            value = int(auth.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > now:
            return value
    for key in ("expires_in", "expires", "expire"):
        try:
            value = int(auth.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return now + value
    return now + _AUTH_FALLBACK_TTL_SEC


def _cached_auth(now: int | None = None) -> Dict[str, Any] | None:
    now = int(now or time.time())
    with _cache_lock:
        auth = _cache.get("auth")
        expiry = int(_cache.get("auth_expires_at") or 0)
        if isinstance(auth, dict) and auth.get("at") and expiry > now + _AUTH_EXPIRY_SKEW_SEC:
            return auth
    return None


def _store_auth(auth: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    with _cache_lock:
        _cache["auth"] = auth
        _cache["auth_expires_at"] = _auth_expiry(auth, now)
    return auth


def _invalidate_auth() -> None:
    with _cache_lock:
        _cache["auth"] = None
        _cache["auth_expires_at"] = None


def _login_uncached(cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    direct = cfg_value(cfg, "access_token", "accessToken", "at")
    if direct:
        auth = {"at": str(direct), "appid": str(cfg_value(cfg, "app_id", "appid", "appId") or SONOFFLAN_APP_ID), "region": region(cfg), "user": {}}
        _store_auth(auth)
        set_diag("authenticated")
        return auth
    payload = login_payload(cfg)
    if payload is None:
        return None
    raw = dumps_body(payload) or b""
    result = request_json(base_url(cfg) + "/v2/user/login", "POST", payload, signed_headers(cfg, raw))
    if result.get("error") == 10004 and isinstance(result.get("data"), dict) and result["data"].get("region"):
        cfg = {**cfg, "region": result["data"]["region"]}
        result = request_json(base_url(cfg) + "/v2/user/login", "POST", payload, signed_headers(cfg, raw))
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if result.get("error") == 0 and data.get("at"):
        auth = {**data, "appid": app_credentials(cfg)[0], "region": data.get("region") or region(cfg)}
        _store_auth(auth)
        set_diag("authenticated")
        return auth
    _invalidate_auth()
    set_diag("auth_unavailable", {"http_status": result.get("_http_status") or result.get("status"), "error": result.get("error"), "msg": result.get("msg") or result.get("message"), "body": result.get("body")})
    return None


def login(cfg: Dict[str, Any], force: bool = False, rejected_auth: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    if not force:
        cached = _cached_auth()
        if cached:
            set_diag("authenticated")
            return cached
    with _auth_refresh_lock:
        cached = _cached_auth()
        if cached:
            rejected_token = str((rejected_auth or {}).get("at") or "")
            if not force or (rejected_token and str(cached.get("at") or "") != rejected_token):
                set_diag("authenticated")
                return cached
        if force:
            _invalidate_auth()
        return _login_uncached(cfg)


def expected_model(deviceid: str) -> str:
    return str(EXPECTED.get(deviceid, {}).get("model") or "")


def model_for(deviceid: str, item: Dict[str, Any] | None = None) -> str:
    item = item or {}
    return str(item.get("model") or item.get("productModel") or expected_model(deviceid) or "")


def gang_count_for(deviceid: str, model: str = "") -> int:
    expected = EXPECTED.get(deviceid)
    if expected:
        return int(expected["gang_count"])
    model = model.upper()
    if "M5-3" in model:
        return 3
    if "M5-2" in model:
        return 2
    return 1


def is_m5(deviceid: str, model: str = "") -> bool:
    return "M5-" in (model or expected_model(deviceid)).upper()


def uses_switches(deviceid: str, model: str = "", params: Dict[str, Any] | None = None) -> bool:
    if isinstance(params, dict) and isinstance(params.get("switches"), list):
        return True
    return is_m5(deviceid, model) or gang_count_for(deviceid, model) > 1


def normalize_switch(value: Any) -> str:
    return "on" if str(value).lower() == "on" or value is True else "off"


def normalize_states(states: Dict[Any, Any], gang_count: int) -> Dict[int, str]:
    result = {i: "off" for i in range(1, gang_count + 1)}
    for key, value in (states or {}).items():
        try:
            channel = int(key)
        except Exception:
            continue
        if 1 <= channel <= gang_count:
            result[channel] = normalize_switch(value)
    return result


def aggregate_state(states: Dict[Any, Any], gang_count: int) -> str:
    normalized = normalize_states(states, gang_count)
    values = [normalized[i] for i in range(1, gang_count + 1)]
    if all(v == "on" for v in values):
        return "on"
    if all(v == "off" for v in values):
        return "off"
    return "mixed"


def device_online(item: Dict[str, Any], params: Dict[str, Any]) -> bool:
    if "online" in params:
        return bool(params.get("online"))
    if "online" in item:
        return bool(item.get("online"))
    if "isOnline" in item:
        return bool(item.get("isOnline"))
    return False


def channel_states_for(deviceid: str, model: str, params: Dict[str, Any], gang_count: int) -> Dict[int, str]:
    states: Dict[int, str] = {i: "off" for i in range(1, gang_count + 1)}
    switches = params.get("switches") if isinstance(params.get("switches"), list) else []
    if switches:
        for idx, item in enumerate(switches):
            if not isinstance(item, dict):
                continue
            outlet = item.get("outlet")
            try:
                channel = int(outlet) + 1 if outlet is not None else idx + 1
            except Exception:
                channel = idx + 1
            if 1 <= channel <= gang_count:
                states[channel] = normalize_switch(item.get("switch"))
        return states
    if not uses_switches(deviceid, model, params):
        states[1] = normalize_switch(params.get("switch"))
    return states


def public_device(item: Dict[str, Any]) -> Dict[str, Any]:
    deviceid = str(item.get("deviceid") or item.get("id") or item.get("deviceId") or "")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    expected = EXPECTED.get(deviceid, {})
    model = model_for(deviceid, item)
    gang_count = gang_count_for(deviceid, model)
    states = channel_states_for(deviceid, model, params, gang_count)
    return {
        "deviceid": deviceid,
        "name": str(item.get("name") or expected.get("name") or deviceid),
        "model": model,
        "online": device_online(item, params),
        "state": aggregate_state(states, gang_count),
        "last_update_ts": int(item.get("last_update_ts") or item.get("updateTime") or item.get("ts") or time.time()),
        "gang_count": gang_count,
        "channels": list(range(1, gang_count + 1)),
        "channel_states": states,
    }


def configured_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    if raw:
        return [public_device(x) for x in raw if isinstance(x, dict)]
    return [{"deviceid": k, "name": v["name"], "model": v["model"], "online": False, "state": "off", "last_update_ts": int(time.time()), "gang_count": int(v["gang_count"]), "channels": list(range(1, int(v["gang_count"]) + 1)), "channel_states": {i: "off" for i in range(1, int(v["gang_count"]) + 1)}} for k, v in EXPECTED.items()]


def fallback_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    with _device_cache_lock:
        cached = copy.deepcopy(_cache.get("devices") or [])
    if cached:
        return cached
    return configured_devices(cfg)


def _active_intent_locked(deviceid: str, now: float | None = None) -> Dict[str, Any] | None:
    intent = _device_intents.get(deviceid)
    if intent and float(now or time.time()) - float(intent.get("created_at") or 0) > _DEVICE_INTENT_TTL_SEC:
        _device_intents.pop(deviceid, None)
        return None
    return intent


def _cached_command_context(cfg: Dict[str, Any], deviceid: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    with _device_cache_lock:
        devices = copy.deepcopy(_cache.get("devices") or [])
        raw_devices = _cache.get("raw_devices") or {}
        raw = copy.deepcopy(raw_devices.get(deviceid) or {}) if isinstance(raw_devices, dict) else {}
        has_active_intent = _active_intent_locked(deviceid) is not None
    return (devices or configured_devices(cfg)), raw, has_active_intent


def _device_matches_states(item: Dict[str, Any] | None, expected_states: Dict[int, str]) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("channel_states"), dict):
        return False
    actual = normalize_states(item["channel_states"], len(expected_states))
    expected = normalize_states(expected_states, len(expected_states))
    return actual == expected


def _begin_refresh() -> Dict[str, int]:
    with _device_cache_lock:
        deviceids = set(EXPECTED)
        deviceids.update(
            str(item.get("deviceid") or "")
            for item in (_cache.get("devices") or [])
            if isinstance(item, dict) and item.get("deviceid")
        )
        generations = {}
        for deviceid in deviceids:
            generation = int(_device_generations.get(deviceid) or 0) + 1
            _device_generations[deviceid] = generation
            generations[deviceid] = generation
        return generations


def _increment_device_generation(deviceid: str) -> int:
    with _device_cache_lock:
        generation = int(_device_generations.get(deviceid) or 0) + 1
        _device_generations[deviceid] = generation
        return generation


def _merge_refreshed_devices(
    raw_map: Dict[str, Dict[str, Any]],
    devices: List[Dict[str, Any]],
    refresh_generations: Dict[str, int] | None = None,
) -> List[Dict[str, Any]]:
    now = time.time()
    with _device_cache_lock:
        refresh_generations = refresh_generations or dict(_device_generations)
        current_devices = copy.deepcopy(_cache.get("devices") or [])
        current_by_id = {str(item.get("deviceid") or ""): item for item in current_devices if isinstance(item, dict)}
        current_raw = copy.deepcopy(_cache.get("raw_devices") or {})
        merged_by_id = dict(current_by_id)
        for item in devices:
            deviceid = str(item.get("deviceid") or "")
            if int(refresh_generations.get(deviceid) or 0) != int(_device_generations.get(deviceid) or 0):
                continue
            intent = _active_intent_locked(deviceid, now)
            if intent and not _device_matches_states(item, intent["states"]):
                continue
            merged_by_id[deviceid] = copy.deepcopy(item)
            if deviceid in raw_map:
                current_raw[deviceid] = copy.deepcopy(raw_map[deviceid])
            if intent:
                _device_intents.pop(deviceid, None)
        ordered_ids = [str(item.get("deviceid") or "") for item in current_devices]
        ordered_ids.extend(deviceid for deviceid in merged_by_id if deviceid not in ordered_ids)
        merged = [merged_by_id[deviceid] for deviceid in ordered_ids if deviceid in merged_by_id]
        _cache["raw_devices"] = current_raw
        _cache["devices"] = merged
        _cache["last_sync_ts"] = int(now)
        return copy.deepcopy(merged)


def _store_command_intent(deviceid: str, model: str, patched_states: Dict[int, str], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    with _device_cache_lock:
        items = copy.deepcopy(_cache.get("devices") or [])
        if not items:
            items = configured_devices(cfg)
        patch_local_state(items, deviceid, model, patched_states)
        _cache["devices"] = items
        _cache["last_sync_ts"] = int(time.time())
        _device_intents[deviceid] = {"states": dict(patched_states), "created_at": time.time()}
        return copy.deepcopy(items)


def extract_raw_devices(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw: List[Dict[str, Any]] = []
    if isinstance(data.get("thingList"), list):
        for item in data["thingList"]:
            thing = item.get("itemData") if isinstance(item, dict) and isinstance(item.get("itemData"), dict) else item
            if isinstance(thing, dict) and (thing.get("deviceid") or thing.get("id") or thing.get("deviceId")):
                raw.append(thing)
    elif isinstance(data.get("devices"), list):
        raw = [x for x in data["devices"] if isinstance(x, dict)]
    return raw


def refresh_live_devices(cfg: Dict[str, Any], auth: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any]]:
    endpoint = "/v2/device/thing"
    refresh_generations = _begin_refresh()
    result = request_json(base_url({**cfg, "region": auth.get("region") or region(cfg)}) + endpoint, "GET", None, bearer_headers(auth), params={"num": 0})
    http_status = result.get("_http_status") or result.get("status")
    raw = extract_raw_devices(result)
    error = result.get("error")
    ok = not result.get("status") and error in (None, 0) and isinstance(result.get("data"), dict)
    diag = {
        "endpoint": endpoint,
        "method": "GET",
        "params": {"num": 0},
        "http_status": http_status,
        "safe_error_body": result.get("body") or {"error": result.get("error"), "msg": result.get("msg") or result.get("message")},
        "thingList_count": len(raw),
        "refresh_success": bool(ok),
    }
    log_refresh_diag(diag)
    if not ok:
        set_diag("refresh_failed", diag)
        return False, [], diag
    raw_map = {str(x.get("deviceid") or x.get("id") or x.get("deviceId")): x for x in raw}
    devices = [public_device(x) for x in raw]
    expected = set(EXPECTED.keys())
    devices = [x for x in devices if not expected or x["deviceid"] in expected]
    diag["_live_devices"] = copy.deepcopy(devices)
    devices = _merge_refreshed_devices(raw_map, devices, refresh_generations)
    set_diag("authenticated")
    return True, devices, diag


def cloud_devices(cfg: Dict[str, Any], auth: Dict[str, Any]) -> List[Dict[str, Any]]:
    ok, devices, _ = refresh_live_devices(cfg, auth)
    return devices if ok else []


def devices() -> Dict[str, Any]:
    payload = config_payload()
    if not payload["loaded"]:
        with _device_cache_lock:
            _cache["devices"] = []
            _cache["raw_devices"] = {}
            _cache["last_sync_ts"] = int(time.time())
        return {"config_loaded": False, "config_path": payload["path"], "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": []}
    cfg = payload["config"]
    auth = login(cfg)
    if auth:
        ok, live_items, _ = refresh_live_devices(cfg, auth)
        if ok:
            items = live_items
        else:
            items = fallback_devices(cfg)
            _cache["auth_status"] = "refresh_failed"
    else:
        items = fallback_devices(cfg)
    return {"config_loaded": True, "config_path": payload["path"], "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": items}


def best_current_states(deviceid: str, model: str, raw: Dict[str, Any], items: List[Dict[str, Any]], gang_count: int) -> Dict[int, str]:
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    raw_states = channel_states_for(deviceid, model, params, gang_count)
    raw_has_switches = isinstance(params.get("switches"), list) and len(params.get("switches")) > 0
    raw_has_switch = "switch" in params and not uses_switches(deviceid, model, params)
    if raw_has_switches or raw_has_switch:
        return raw_states
    for item in items or []:
        if item.get("deviceid") == deviceid and isinstance(item.get("channel_states"), dict):
            return normalize_states(item["channel_states"], gang_count)
    return raw_states


def full_switches_payload(channel: int, action: str, previous_states: Dict[int, str], gang_count: int) -> List[Dict[str, Any]]:
    patched = normalize_states(previous_states, gang_count)
    patched[channel] = action
    return [{"outlet": i - 1, "switch": patched[i]} for i in range(1, gang_count + 1)]


def command_params(deviceid: str, model: str, channel: int, action: str, previous_states: Dict[int, str]) -> tuple[Dict[str, Any], str, int | None, Dict[int, str], List[Dict[str, Any]] | None]:
    gang_count = gang_count_for(deviceid, model)
    channel = max(1, min(gang_count, int(channel or 1)))
    patched_states = normalize_states(previous_states, gang_count)
    patched_states[channel] = action
    if uses_switches(deviceid, model):
        switches = full_switches_payload(channel, action, previous_states, gang_count)
        return {"switches": switches}, "switches", channel - 1, patched_states, switches
    return {"switch": action}, "switch", None, patched_states, None


def patch_local_state(items: List[Dict[str, Any]], deviceid: str, model: str, patched_states: Dict[int, str]) -> List[Dict[str, Any]]:
    now = int(time.time())
    gang = gang_count_for(deviceid, model)
    states = normalize_states(patched_states, gang)
    found = False
    for item in items:
        if item.get("deviceid") == deviceid:
            item["last_update_ts"] = now
            item["gang_count"] = gang
            item["channels"] = list(range(1, gang + 1))
            item["channel_states"] = dict(states)
            item["state"] = aggregate_state(states, gang)
            found = True
            break
    if not found:
        expected = EXPECTED.get(deviceid, {})
        items.append({"deviceid": deviceid, "name": expected.get("name", deviceid), "model": model or expected.get("model", ""), "online": True, "state": aggregate_state(states, gang), "last_update_ts": now, "gang_count": gang, "channels": list(range(1, gang + 1)), "channel_states": dict(states)})
    return items


def _device_lock(deviceid: str) -> threading.Lock:
    with _device_locks_guard:
        return _device_locks.setdefault(str(deviceid), threading.Lock())


def _known_device(deviceid: str, items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict) and str(item.get("deviceid") or item.get("id") or item.get("deviceId") or "") == str(deviceid):
            return item
    if deviceid in EXPECTED:
        expected = EXPECTED[deviceid]
        return {
            "deviceid": deviceid,
            "name": expected["name"],
            "model": expected["model"],
            "online": False,
            "gang_count": int(expected["gang_count"]),
            "channel_states": {channel: "off" for channel in range(1, int(expected["gang_count"]) + 1)},
        }
    return None


def _auth_rejected(result: Dict[str, Any]) -> bool:
    status = result.get("_http_status") or result.get("status")
    error = result.get("error")
    message = str(result.get("msg") or result.get("message") or "").lower()
    return status in (401, 403) or error in (401, 403) or any(term in message for term in ("token expired", "invalid token", "unauthorized"))


def _command_request(cfg: Dict[str, Any], auth: Dict[str, Any], deviceid: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return request_json(
        base_url({**cfg, "region": auth.get("region") or region(cfg)}) + "/v2/device/thing/status",
        "POST",
        {"type": 1, "id": deviceid, "params": params},
        bearer_headers(auth),
    )


def set_state(deviceid: str, action: str, channel: int = 1) -> Dict[str, Any]:
    action = action.lower().strip()
    channel = max(1, int(channel or 1))
    if action not in ("on", "off"):
        return {"ok": False, "error": "action must be on or off"}
    payload = config_payload()
    if not payload["loaded"]:
        return {"ok": False, "error": "ewelink config not found"}
    cfg = payload["config"]
    lock = _device_lock(deviceid)
    if not lock.acquire(blocking=False):
        return {"ok": False, "error": "sonoff command already in progress", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}
    try:
        auth = login(cfg)
        if not auth:
            return {"ok": False, "error": "ewelink token unavailable", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}

        cached_items, raw, has_active_intent = _cached_command_context(cfg, deviceid)
        known = _known_device(deviceid, cached_items)
        if not known:
            return {"ok": False, "error": "sonoff device not found", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}
        model = model_for(deviceid, raw or known)
        gang = gang_count_for(deviceid, model)
        channel = max(1, min(gang, channel))
        previous_states = best_current_states(deviceid, model, {} if has_active_intent else raw, cached_items, gang)
        params, shape, outlet, patched_states, switches_payload = command_params(deviceid, model, channel, action, previous_states)

        result = _command_request(cfg, auth, deviceid, params)
        if _auth_rejected(result):
            auth = login(cfg, force=True, rejected_auth=auth)
            if auth:
                result = _command_request(cfg, auth, deviceid, params)
        ok = result.get("error") in (None, 0) and not result.get("status")
        if not ok:
            log_command_diag({"deviceid": deviceid, "model": model, "action": action, "requested_channel": channel, "previous_channel_states": previous_states, "outgoing_switches_payload": switches_payload, "patched_channel_states": patched_states, "payload_shape": shape, "resolved_outlet": outlet, "endpoint": "/v2/device/thing/status", "result_status": result.get("_http_status") or result.get("status") or result.get("error"), "post_refresh_success": False})
            set_diag("command_failed", {"http_status": result.get("_http_status") or result.get("status"), "error": result.get("error"), "msg": result.get("msg") or result.get("message"), "body": result.get("body")})
            return {"ok": False, "error": "ewelink command failed", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}

        _increment_device_generation(deviceid)
        _store_command_intent(deviceid, model, patched_states, cfg)
        post_ok, live_items, post_diag = refresh_live_devices(cfg, auth)
        refreshed_devices = post_diag.pop("_live_devices", [])
        refreshed_device = _known_device(deviceid, refreshed_devices) if post_ok else None
        state_confirmed = bool(post_ok and _device_matches_states(refreshed_device, patched_states))
        if state_confirmed:
            items = live_items
            confirmation = "cloud_confirmed"
        else:
            items = fallback_devices(cfg)
            with _cache_lock:
                if not post_ok:
                    _cache["auth_status"] = "refresh_failed"
            confirmation = "patched_unconfirmed" if post_ok else "patched_fallback"
        device = _known_device(deviceid, items)
        log_command_diag({"deviceid": deviceid, "model": model, "action": action, "requested_channel": channel, "previous_channel_states": previous_states, "outgoing_switches_payload": switches_payload, "patched_channel_states": patched_states, "payload_shape": shape, "resolved_outlet": outlet, "endpoint": "/v2/device/thing/status", "result_status": result.get("_http_status") or result.get("status") or result.get("error") or "ok", "post_refresh_success": post_ok, "post_refresh_diag": post_diag, "refresh_result": confirmation})
        return {"ok": True, "deviceid": deviceid, "channel": channel, "action": action, "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": items, "device": device, "state_confirmed": state_confirmed, "confirmation": confirmation}
    finally:
        lock.release()


def _public_payload(items: List[Dict[str, Any]], results: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    payload = {
        "config_loaded": bool(_cache.get("config_loaded")),
        "config_path": _cache.get("config_path"),
        "auth_status": _cache.get("auth_status"),
        "last_error": _cache.get("last_error"),
        "devices": items,
    }
    if results is not None:
        payload["results"] = results
    return payload


def _device_by_id(deviceid: str, items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for item in items:
        if str(item.get("deviceid")) == str(deviceid):
            return item
    return None


def _safe_bulk_result(deviceid: str, channel: int, action: str, result: Dict[str, Any] | None = None, error: Any = None) -> Dict[str, Any]:
    out = {"deviceid": str(deviceid), "channel": int(channel), "action": action, "ok": bool(result and result.get("ok")) and error is None}
    if error is not None:
        out["error"] = safe_error(error)
    elif result and not result.get("ok"):
        out["error"] = safe_error(result.get("error"))
    return out


def bulk_device_state(deviceid: str, action: str) -> Dict[str, Any]:
    action = action.lower().strip()
    if action not in ("on", "off"):
        return {"ok": False, "error": "action must be on or off"}
    current = devices()
    target = _device_by_id(deviceid, current.get("devices", []))
    if not target:
        return {"ok": False, "error": "sonoff device not found", **current}
    results: List[Dict[str, Any]] = []
    for channel in target.get("channels") or [1]:
        try:
            result = set_state(deviceid, action, int(channel))
            results.append(_safe_bulk_result(deviceid, int(channel), action, result))
        except Exception as exc:
            results.append(_safe_bulk_result(deviceid, int(channel), action, None, repr(exc)))
    refreshed = devices()
    refreshed["results"] = results
    refreshed["ok"] = all(item.get("ok") for item in results)
    return refreshed


def bulk_all_state(action: str) -> Dict[str, Any]:
    action = action.lower().strip()
    if action not in ("on", "off"):
        return {"ok": False, "error": "action must be on or off"}
    current = devices()
    results: List[Dict[str, Any]] = []
    for device in current.get("devices", []):
        deviceid = str(device.get("deviceid") or "")
        if not deviceid:
            continue
        for channel in device.get("channels") or [1]:
            try:
                result = set_state(deviceid, action, int(channel))
                results.append(_safe_bulk_result(deviceid, int(channel), action, result))
            except Exception as exc:
                results.append(_safe_bulk_result(deviceid, int(channel), action, None, repr(exc)))
    refreshed = devices()
    refreshed["results"] = results
    refreshed["ok"] = all(item.get("ok") for item in results)
    return refreshed
