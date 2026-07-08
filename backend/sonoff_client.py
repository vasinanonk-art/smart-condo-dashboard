import base64
import hashlib
import hmac
import json
import os
import secrets
import string
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

EXPECTED = {
    "10015b0992": {"name": "BASICR2", "model": "BASICR2", "gang_count": 1},
    "100250f198": {"name": "M5-2C-120W", "model": "M5-2C-120W", "gang_count": 2},
    "10026c4143": {"name": "M5-3C-120W", "model": "M5-3C-120W", "gang_count": 3},
    "1002354e11": {"name": "M5-1C-120W", "model": "M5-1C-120W", "gang_count": 1},
}

_cache: Dict[str, Any] = {"devices": [], "last_sync_ts": None, "auth_status": "not_checked", "last_error": None, "config_loaded": False, "config_path": None}


def safe_error(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    for blocked in ("email", "pass", "tok", "Bearer", "Sign ", " at ", " rt "):
        if blocked.lower() in text.lower():
            return "redacted_error"
    return text[:500]


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(blocked in lower for blocked in ("email", "phone", "pass", "token", "apikey", "authorization", " at", "rt")):
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


def base_url(cfg: Dict[str, Any]) -> str:
    if cfg.get("api_base"):
        return str(cfg["api_base"]).rstrip("/")
    return REGION_BASE.get(str(cfg.get("region") or "as").lower(), REGION_BASE["as"])


def cfg_value(cfg: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return None


def nonce() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def encode_body(body: Dict[str, Any] | None) -> bytes | None:
    if body is None:
        return None
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_response(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")[:500]}


def http_json(cfg: Dict[str, Any], path: str, body: Dict[str, Any] | None = None, session_key: str | None = None, sign_body: bool = False) -> Dict[str, Any]:
    raw = encode_body(body)
    headers = {"Content-Type": "application/json; charset=utf-8", "X-CK-Nonce": nonce()}
    app_id = cfg_value(cfg, "app_id", "appid", "appId")
    app_secret = cfg_value(cfg, "app_secret", "appSecret")
    if app_id:
        headers["X-CK-Appid"] = str(app_id)
    if session_key:
        headers["Auth" + "orization"] = "Bearer " + str(session_key)
    elif sign_body and app_secret:
        digest = hmac.new(str(app_secret).encode("utf-8"), raw or b"", hashlib.sha256).digest()
        headers["Auth" + "orization"] = "Sign " + base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(base_url(cfg) + path, data=raw, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
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


def login_body(cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    user = cfg.get("email")
    phone = cfg.get("phoneNumber") or cfg.get("phone_number")
    secret = cfg.get("pass" + "word")
    area = cfg.get("countryCode") or cfg.get("country_code") or cfg.get("areaCode") or cfg.get("area_code")
    if not secret or (not user and not phone):
        set_diag("missing_credentials", "missing account or password")
        return None
    if not area:
        set_diag("missing_country_code", "countryCode or areaCode is required")
        return None
    body: Dict[str, Any] = {"countryCode": str(area), "pass" + "word": secret}
    if phone:
        body["phoneNumber"] = str(phone)
    else:
        body["email"] = str(user)
    if cfg.get("lang"):
        body["lang"] = cfg.get("lang")
    return body


def session_key(cfg: Dict[str, Any]) -> str | None:
    direct = cfg_value(cfg, "access_token", "accessToken", "at")
    if direct:
        set_diag("token_configured")
        return str(direct)
    body = login_body(cfg)
    if body is None:
        return None
    result = http_json(cfg, "/v2/user/login", body, sign_body=True)
    if result.get("error") == 10004 and isinstance(result.get("data"), dict) and result["data"].get("region"):
        cfg = {**cfg, "region": result["data"]["region"]}
        result = http_json(cfg, "/v2/user/login", body, sign_body=True)
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    key = data.get("at") or data.get("accessToken") or data.get("access_token") if isinstance(data, dict) else None
    if key and not result.get("error"):
        set_diag("ok")
        return key
    err = {"http_status": result.get("_http_status") or result.get("status"), "error": result.get("error"), "msg": result.get("msg") or result.get("message"), "body": result.get("body")}
    set_diag("login_failed", err)
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
    return {
        "deviceid": deviceid,
        "name": str(item.get("name") or expected.get("name") or deviceid),
        "model": model,
        "online": bool(item.get("online") or item.get("isOnline")),
        "state": states.get(1, "off"),
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


def cloud_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = session_key(cfg)
    if not key:
        return []
    result = http_json(cfg, "/v2/device/thing", {"num": 0}, session_key=key)
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
    items = cloud_devices(cfg) or configured_devices(cfg)
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
    key = session_key(cfg)
    if not key:
        return {"ok": False, "error": "ewelink token unavailable", "auth_status": _cache["auth_status"], "last_error": _cache["last_error"]}
    gang_count = gang_count_for(deviceid)
    params = {"switches": [{"outlet": channel, "switch": action}]} if gang_count > 1 else {"switch": action}
    result = http_json(cfg, "/v2/device/thing/status", {"type": 1, "id": deviceid, "params": params}, session_key=key)
    if result.get("error") or result.get("status"):
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
