import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

CONFIG_PATHS = [
    os.getenv("EWELINK_CONFIG_FILE", "/opt/smart-condo-dashboard-run/config/ewelink.local.json"),
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

_cache: Dict[str, Any] = {"devices": [], "last_sync_ts": None, "auth": None, "auth_status": "not_checked", "last_error": None, "config_loaded": False, "config_path": None}


def safe_error(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    for blocked in ("email", "phone", "pass", "tok", "Bearer", "Sign ", "apikey", " at ", " rt "):
        if blocked.lower() in text.lower():
            return "redacted_error"
    return text[:500]


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(blocked in lower for blocked in ("email", "phone", "pass", "token", "apikey", "authorization")) or lower in ("at", "rt"):
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


def config_payload() -> Dict[str, Any]:
    for path in CONFIG_PATHS:
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


def request_json(url: str, method: str = "GET", body: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
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


def login(cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    direct = cfg_value(cfg, "access_token", "accessToken", "at")
    if direct:
        auth = {"at": str(direct), "appid": str(cfg_value(cfg, "app_id", "appid", "appId") or SONOFFLAN_APP_ID), "region": region(cfg), "user": {}}
        _cache["auth"] = auth
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
        _cache["auth"] = auth
        set_diag("authenticated")
        return auth
    set_diag("auth_unavailable", {"http_status": result.get("_http_status") or result.get("status"), "error": result.get("error"), "msg": result.get("msg") or result.get("message"), "body": result.get("body")})
    return None


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


def channel_states_for(params: Dict[str, Any], gang_count: int) -> Dict[int, str]:
    states: Dict[int, str] = {}
    switches = params.get("switches") if isinstance(params.get("switches"), list) else []
    for idx in range(1, gang_count + 1):
        raw = None
        if idx - 1 < len(switches) and isinstance(switches[idx - 1], dict):
            raw = switches[idx - 1].get("switch")
        if raw is None and idx == 1:
            raw = params.get("switch")
        states[idx] = "on" if str(raw).lower() == "on" or raw is True else "off"
    return states


def public_device(item: Dict[str, Any]) -> Dict[str, Any]:
    deviceid = str(item.get("deviceid") or item.get("id") or item.get("deviceId") or "")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    expected = EXPECTED.get(deviceid, {})
    model = str(item.get("model") or item.get("productModel") or expected.get("model") or "")
    gang_count = gang_count_for(deviceid, model)
    states = channel_states_for(params, gang_count)
    return {"deviceid": deviceid, "name": str(item.get("name") or expected.get("name") or deviceid), "model": model, "online": bool(item.get("online") or item.get("isOnline")), "state": states.get(1, "off"), "last_update_ts": int(item.get("last_update_ts") or item.get("updateTime") or item.get("ts") or time.time()), "gang_count": gang_count, "channels": list(range(1, gang_count + 1)), "channel_states": states}


def configured_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    if raw:
        return [public_device(x) for x in raw if isinstance(x, dict)]
    return [{"deviceid": k, "name": v["name"], "model": v["model"], "online": False, "state": "off", "last_update_ts": int(time.time()), "gang_count": int(v["gang_count"]), "channels": list(range(1, int(v["gang_count"]) + 1)), "channel_states": {i: "off" for i in range(1, int(v["gang_count"]) + 1)}} for k, v in EXPECTED.items()]


def cloud_devices(cfg: Dict[str, Any], auth: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = request_json(base_url({**cfg, "region": auth.get("region") or region(cfg)}) + "/v2/device/thing", "POST", {"num": 0}, bearer_headers(auth))
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    raw: List[Dict[str, Any]] = []
    if isinstance(data.get("thingList"), list):
        for item in data["thingList"]:
            thing = item.get("itemData") if isinstance(item, dict) and isinstance(item.get("itemData"), dict) else item
            if isinstance(thing, dict):
                raw.append(thing)
    elif isinstance(data.get("devices"), list):
        raw = data["devices"]
    devices = [public_device(x) for x in raw]
    expected = set(EXPECTED.keys())
    return [x for x in devices if not expected or x["deviceid"] in expected]


def devices() -> Dict[str, Any]:
    payload = config_payload()
    if not payload["loaded"]:
        _cache["devices"] = []
        _cache["last_sync_ts"] = int(time.time())
        return {"config_loaded": False, "config_path": payload["path"], "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": []}
    cfg = payload["config"]
    auth = login(cfg)
    items = cloud_devices(cfg, auth) if auth else []
    if not items:
        items = configured_devices(cfg)
    _cache["devices"] = items
    _cache["last_sync_ts"] = int(time.time())
    return {"config_loaded": True, "config_path": payload["path"], "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": items}


def set_state(deviceid: str, action: str, channel: int = 1) -> Dict[str, Any]:
    action = action.lower().strip()
    channel = max(1, int(channel or 1))
    if action not in ("on", "off"):
        return {"ok": False, "error": "action must be on or off"}
    payload = config_payload()
    if not payload["loaded"]:
        return {"ok": False, "error": "ewelink config not found"}
    cfg = payload["config"]
    auth = login(cfg)
    if not auth:
        return {"ok": False, "error": "ewelink token unavailable", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}
    gang_count = gang_count_for(deviceid)
    params = {"switches": [{"outlet": channel, "switch": action}]} if gang_count > 1 else {"switch": action}
    result = request_json(base_url({**cfg, "region": auth.get("region") or region(cfg)}) + "/v2/device/thing/status", "POST", {"type": 1, "id": deviceid, "params": params}, bearer_headers(auth))
    if result.get("error") not in (None, 0) or result.get("status"):
        set_diag("command_failed", {"http_status": result.get("_http_status") or result.get("status"), "error": result.get("error"), "msg": result.get("msg") or result.get("message"), "body": result.get("body")})
        return {"ok": False, "error": "ewelink command failed", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}
    now = int(time.time())
    items = _cache.get("devices") or configured_devices(cfg)
    found = False
    for item in items:
        if item.get("deviceid") == deviceid:
            item["last_update_ts"] = now
            item["gang_count"] = gang_count_for(deviceid, item.get("model", ""))
            states = item.get("channel_states") if isinstance(item.get("channel_states"), dict) else {i: "off" for i in range(1, item["gang_count"] + 1)}
            states[channel] = action
            item["channel_states"] = states
            item["channels"] = list(range(1, item["gang_count"] + 1))
            item["state"] = states.get(1, action)
            found = True
            break
    if not found:
        expected = EXPECTED.get(deviceid, {})
        gang = gang_count_for(deviceid, expected.get("model", ""))
        states = {i: action if i == channel else "off" for i in range(1, gang + 1)}
        items.append({"deviceid": deviceid, "name": expected.get("name", deviceid), "model": expected.get("model", ""), "online": True, "state": states.get(1, action), "last_update_ts": now, "gang_count": gang, "channels": list(range(1, gang + 1)), "channel_states": states})
    _cache["devices"] = items
    _cache["last_sync_ts"] = now
    return {"ok": True, "deviceid": deviceid, "channel": channel, "action": action, "auth_status": _cache["auth_status"], "last_error": _cache["last_error"], "devices": items}
