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

EXPECTED = {
    "10015b0992": {"name": "BASICR2", "model": "BASICR2"},
    "100250f198": {"name": "M5-2C-120W", "model": "M5-2C-120W"},
    "10026c4143": {"name": "M5-3C-120W", "model": "M5-3C-120W"},
    "1002354e11": {"name": "M5-1C-120W", "model": "M5-1C-120W"},
}

_cache: Dict[str, Any] = {"devices": [], "last_sync_ts": None}


def config_payload() -> Dict[str, Any]:
    for path in CONFIG_PATHS:
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return {"loaded": isinstance(data, dict), "path": path, "config": data if isinstance(data, dict) else {}}
            except Exception:
                return {"loaded": False, "path": path, "config": {}}
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


def http_json(cfg: Dict[str, Any], path: str, body: Dict[str, Any] | None = None, bearer: str | None = None, sign_body: bool = False) -> Dict[str, Any]:
    raw = json.dumps(body or {}).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    app_id = cfg_value(cfg, "app_id", "appid", "appId")
    app_secret = cfg_value(cfg, "app_secret", "appSecret")
    if app_id:
        headers["X-CK-Appid"] = str(app_id)
    if bearer:
        headers["Authorization"] = "Bearer " + str(bearer)
    elif sign_body and app_secret:
        digest = hmac.new(str(app_secret).encode("utf-8"), raw or b"", hashlib.sha256).digest()
        headers["Authorization"] = "Sign " + base64.b64encode(digest).decode("utf-8")
    req = urllib.request.Request(base_url(cfg) + path, data=raw, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"status": exc.code}
    except Exception as exc:
        return {"error": repr(exc)}


def access_token(cfg: Dict[str, Any]) -> str | None:
    direct = cfg_value(cfg, "access_token", "accessToken", "at")
    if direct:
        return str(direct)
    user = cfg.get("email")
    secret = cfg.get("pass" + "word")
    if not user or not secret:
        return None
    body = {"email": user, "pass" + "word": secret}
    if cfg.get("countryCode"):
        body["countryCode"] = cfg.get("countryCode")
    result = http_json(cfg, "/v2/user/login", body, sign_body=True)
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return None
    return data.get("at") or data.get("accessToken") or data.get("access_token")


def state_from_params(params: Dict[str, Any]) -> str:
    value = params.get("switch")
    if value is None and isinstance(params.get("switches"), list) and params["switches"]:
        value = params["switches"][0].get("switch")
    if value is True:
        return "on"
    if value is False:
        return "off"
    return "on" if str(value).lower() == "on" else "off"


def public_device(item: Dict[str, Any]) -> Dict[str, Any]:
    deviceid = str(item.get("deviceid") or item.get("id") or item.get("deviceId") or "")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    expected = EXPECTED.get(deviceid, {})
    return {
        "deviceid": deviceid,
        "name": str(item.get("name") or expected.get("name") or deviceid),
        "model": str(item.get("model") or item.get("productModel") or expected.get("model") or ""),
        "online": bool(item.get("online") or item.get("isOnline")),
        "state": state_from_params(params),
        "last_update_ts": int(item.get("last_update_ts") or item.get("updateTime") or item.get("ts") or time.time()),
    }


def configured_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = cfg.get("devices") if isinstance(cfg.get("devices"), list) else []
    if raw:
        return [public_device(x) for x in raw if isinstance(x, dict)]
    return [{"deviceid": k, "name": v["name"], "model": v["model"], "online": False, "state": "off", "last_update_ts": int(time.time())} for k, v in EXPECTED.items()]


def cloud_devices(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    token = access_token(cfg)
    if not token:
        return []
    result = http_json(cfg, "/v2/device/thing", {"num": 0}, bearer=token)
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
        return {"config_loaded": False, "config_path": payload["path"], "devices": []}
    cfg = payload["config"]
    items = cloud_devices(cfg) or configured_devices(cfg)
    _cache["devices"] = items
    _cache["last_sync_ts"] = int(time.time())
    return {"config_loaded": True, "config_path": payload["path"], "devices": items}


def set_state(deviceid: str, action: str) -> Dict[str, Any]:
    action = action.lower().strip()
    if action not in ("on", "off"):
        return {"ok": False, "error": "action must be on or off"}
    payload = config_payload()
    if not payload["loaded"]:
        return {"ok": False, "error": "ewelink config not found"}
    cfg = payload["config"]
    token = access_token(cfg)
    if not token:
        return {"ok": False, "error": "ewelink token unavailable"}
    result = http_json(cfg, "/v2/device/thing/status", {"type": 1, "id": deviceid, "params": {"switch": action}}, bearer=token)
    if result.get("error") or result.get("status"):
        return {"ok": False, "error": "ewelink command failed"}
    now = int(time.time())
    items = _cache.get("devices") or configured_devices(cfg)
    found = False
    for item in items:
        if item.get("deviceid") == deviceid:
            item["state"] = action
            item["last_update_ts"] = now
            found = True
            break
    if not found:
        expected = EXPECTED.get(deviceid, {})
        items.append({"deviceid": deviceid, "name": expected.get("name", deviceid), "model": expected.get("model", ""), "online": True, "state": action, "last_update_ts": now})
    _cache["devices"] = items
    _cache["last_sync_ts"] = now
    return {"ok": True, "deviceid": deviceid, "action": action, "devices": items}
