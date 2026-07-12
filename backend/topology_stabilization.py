"""Production stabilization for topology health and LG TV state ownership.

This module is intentionally read-only for infrastructure checks and installs no
per-node polling loops. Probes run only when /api/topology is requested and are
cached for the dashboard refresh cadence.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Mapping, Optional

from backend import app as app_module
from backend import topology_runtime

MQTT_STATE_TOPIC = os.getenv("MQTT_STATE_TOPIC", "home/lgtv/state")
MQTT_HEARTBEAT_TOPIC = os.getenv("MQTT_HEARTBEAT_TOPIC", "home/lgtv/heartbeat")
TV_OFFLINE_SEC = int(os.getenv("LG_TV_STATUS_OFFLINE_SEC", "120"))
PROBE_CACHE_SEC = max(5, int(os.getenv("TOPOLOGY_PROBE_CACHE_SEC", "15")))
PROBE_TIMEOUT_SEC = max(0.5, float(os.getenv("TOPOLOGY_PROBE_TIMEOUT_SEC", "2")))
HIGH_LATENCY_MS = max(1.0, float(os.getenv("TOPOLOGY_HIGH_LATENCY_MS", "250")))

_lock = threading.RLock()
_probe_cache: Dict[str, Any] = {"ts": 0, "items": {}}


def _now() -> int:
    return int(time.time())


def _age(ts: Any, now: Optional[int] = None) -> Optional[int]:
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return None
    return max(0, int(now or _now()) - value)


def _safe_error(exc: Any) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return type(exc).__name__


def _is_heartbeat(payload: Mapping[str, Any]) -> bool:
    if payload.get("heartbeat") is True:
        return True
    keys = set(payload.keys())
    state_keys = {"power", "app", "current_app", "input", "source", "volume", "vol", "mute", "muted"}
    return not bool(keys.intersection(state_keys)) and str(payload.get("status") or "").lower() == "online"


def _normalize_full_tv(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = [payload]
    for key in ("tv", "state", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for item in candidates:
        values = {
            "power": item.get("power"),
            "app": item.get("app", item.get("current_app")),
            "input": item.get("input", item.get("source")),
            "volume": item.get("volume", item.get("vol")),
            "mute": item.get("mute", item.get("muted")),
        }
        if any(value is not None for value in values.values()):
            return values
    return None


def _store_tv_payload(payload: Any, topic: str) -> None:
    if not isinstance(payload, Mapping):
        return
    now = _now()
    if topic == MQTT_HEARTBEAT_TOPIC or _is_heartbeat(payload):
        app_module.state["lg_tv_last_heartbeat_ts"] = now
        app_module.state["lg_tv_bridge_online"] = True
        return
    full = _normalize_full_tv(payload)
    if not full:
        return
    previous = app_module.state.get("lg_tv_last_state")
    merged = dict(previous) if isinstance(previous, Mapping) else {}
    for key, value in full.items():
        if value is not None:
            merged[key] = value
    app_module.state["lg_tv_last_state"] = merged
    app_module.state["lg_tv_last_state_ts"] = now
    app_module.state["lg_tv_last_full_state_ts"] = now
    app_module.state["lg_tv_last_heartbeat_ts"] = now
    app_module.state["lg_tv_bridge_online"] = True


def _install_mqtt_state_ownership() -> None:
    if getattr(app_module, "_lg_tv_state_ownership_installed", False):
        return
    original_connect = app_module.mqttc.on_connect
    original_message = app_module.mqttc.on_message

    def wrapped_connect(client, userdata, flags, reason_code, properties=None):
        original_connect(client, userdata, flags, reason_code, properties)
        client.subscribe(MQTT_HEARTBEAT_TOPIC)
        topics = list(app_module.state.get("mqtt_subscribed_topics", []))
        if MQTT_HEARTBEAT_TOPIC not in topics:
            topics.append(MQTT_HEARTBEAT_TOPIC)
        app_module.state["mqtt_subscribed_topics"] = topics

    def wrapped_message(client, userdata, msg):
        if msg.topic in (MQTT_STATE_TOPIC, MQTT_HEARTBEAT_TOPIC):
            try:
                payload = json.loads(msg.payload.decode(errors="ignore"))
            except (ValueError, UnicodeDecodeError):
                payload = None
            _store_tv_payload(payload, msg.topic)
        original_message(client, userdata, msg)

    app_module.mqttc.on_connect = wrapped_connect
    app_module.mqttc.on_message = wrapped_message
    app_module._lg_tv_state_ownership_installed = True


def _tv_payload(now: int) -> Dict[str, Any]:
    snapshot = app_module.state.get("lg_tv_last_state")
    snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    full_ts = app_module.state.get("lg_tv_last_full_state_ts") or app_module.state.get("lg_tv_last_state_ts")
    heartbeat_ts = app_module.state.get("lg_tv_last_heartbeat_ts")
    full_age = _age(full_ts, now)
    heartbeat_age = _age(heartbeat_ts, now)
    bridge_online = heartbeat_age is not None and heartbeat_age <= TV_OFFLINE_SEC
    state_fresh = full_age is not None and full_age <= TV_OFFLINE_SEC
    power = str(snapshot.get("power") or "").strip().lower()
    explicit_off = power in {"off", "false", "0", "offline"}
    tv_online: Optional[bool]
    if not snapshot or full_ts is None:
        tv_online = None
    elif explicit_off:
        tv_online = False
    elif state_fresh:
        tv_online = True
    else:
        tv_online = False
    if tv_online is True:
        health = "healthy"
    elif bridge_online and tv_online is None:
        health = "warning"
    elif bridge_online and tv_online is False:
        health = "warning" if not state_fresh else "offline"
    elif tv_online is False:
        health = "offline"
    else:
        health = "unknown"
    return {
        "online": tv_online,
        "tv_online": tv_online,
        "bridge_online": bridge_online,
        "state_fresh": state_fresh,
        "health": health,
        "last_update_ts": int(full_ts) if full_ts else None,
        "last_full_state_ts": int(full_ts) if full_ts else None,
        "last_heartbeat_ts": int(heartbeat_ts) if heartbeat_ts else None,
        "state_age_sec": full_age,
        "heartbeat_age_sec": heartbeat_age,
        "power": snapshot.get("power"),
        "app": snapshot.get("app"),
        "input": snapshot.get("input"),
        "volume": snapshot.get("volume"),
        "mute": snapshot.get("mute"),
        "source": "mqtt_state",
    }


def _run(command: list[str]) -> tuple[bool, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC, check=False)
        latency = round((time.monotonic() - started) * 1000, 1)
        return result.returncode == 0, (result.stdout or result.stderr or "").strip(), latency
    except Exception as exc:
        return False, _safe_error(exc), round((time.monotonic() - started) * 1000, 1)


def _ping(host: str) -> Dict[str, Any]:
    if not host:
        return {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    ok, output, latency = _run(["ping", "-c", "1", "-W", str(max(1, int(PROBE_TIMEOUT_SEC))), host])
    if ok:
        health = "warning" if latency > HIGH_LATENCY_MS else "healthy"
        return {"health": health, "online": True, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": "icmp", "host": host}}
    return {"health": "offline", "online": False, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": "icmp", "host": host, "last_error": output[:120]}}


def _tcp(host: str, port: int, source: str) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=PROBE_TIMEOUT_SEC):
            latency = round((time.monotonic() - started) * 1000, 1)
            return {"health": "warning" if latency > HIGH_LATENCY_MS else "healthy", "online": True, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": source, "host": host, "port": int(port)}}
    except Exception as exc:
        return {"health": "offline", "online": False, "latency_ms": round((time.monotonic() - started) * 1000, 1), "last_update_ts": _now(), "diagnostics": {"source": source, "host": host, "port": int(port), "last_error": _safe_error(exc)}}


def _http(url: str, source: str, token: str = "") -> Dict[str, Any]:
    if not url:
        return {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    started = time.monotonic()
    try:
        headers = {"User-Agent": "smart-condo-dashboard-health"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SEC) as response:
            status = int(getattr(response, "status", 200))
        latency = round((time.monotonic() - started) * 1000, 1)
        healthy = 200 <= status < 500
        return {"health": ("warning" if latency > HIGH_LATENCY_MS else "healthy") if healthy else "offline", "online": healthy, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": source, "status": status}}
    except Exception as exc:
        return {"health": "offline", "online": False, "latency_ms": round((time.monotonic() - started) * 1000, 1), "last_update_ts": _now(), "diagnostics": {"source": source, "last_error": _safe_error(exc)}}


def _zerotier_local() -> Dict[str, Any]:
    ok_info, info, latency = _run(["zerotier-cli", "info"])
    ok_networks, networks, _ = _run(["zerotier-cli", "listnetworks"])
    if not ok_info:
        return {"health": "offline", "online": False, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": "zerotier-cli", "last_error": info[:120]}}
    parts = info.split()
    node_id = parts[2] if len(parts) > 2 else None
    version = parts[3] if len(parts) > 3 else None
    online = any(token.upper() == "ONLINE" for token in parts)
    managed_ip = None
    network_status = "unknown"
    if ok_networks:
        for line in networks.splitlines():
            tokens = line.split()
            if any(token.upper() == "OK" for token in tokens):
                network_status = "OK"
            for token in tokens:
                if "/" in token and token[0].isdigit():
                    managed_ip = token.split(",")[0]
                    break
    return {"health": "healthy" if online and ok_networks else "warning", "online": online, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": "zerotier-cli", "node_id": node_id, "version": version, "network_status": network_status, "managed_ip": managed_ip}}


def _dns_check() -> Dict[str, Any]:
    host = os.getenv("DNS_HEALTH_HOST", "").strip()
    if not host:
        return {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    started = time.monotonic()
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        latency = round((time.monotonic() - started) * 1000, 1)
        return {"health": "warning" if latency > HIGH_LATENCY_MS else "healthy", "online": True, "latency_ms": latency, "last_update_ts": _now(), "diagnostics": {"source": "dns", "host": host}}
    except Exception as exc:
        return {"health": "offline", "online": False, "last_update_ts": _now(), "diagnostics": {"source": "dns", "host": host, "last_error": _safe_error(exc)}}


def _sonoff_status() -> Dict[str, Any]:
    try:
        import sonoff_client
        data = sonoff_client.devices()
        devices = data.get("devices", []) if isinstance(data, Mapping) else []
        configured = bool(data.get("config_loaded")) if isinstance(data, Mapping) else False
        auth_status = str(data.get("auth_status") or "") if isinstance(data, Mapping) else ""
        authenticated = auth_status == "authenticated"
        online_count = sum(1 for item in devices if isinstance(item, Mapping) and item.get("online"))
        if authenticated and devices:
            health = "healthy"
        elif configured and not authenticated:
            health = "warning"
        elif not configured:
            health = "unknown"
        else:
            health = "offline"
        return {"health": health, "online": True if health == "healthy" else (False if health == "offline" else None), "last_update_ts": app_module.state.get("sonoff_last_sync_ts"), "diagnostics": {"source": "sonoff_client", "configured": configured, "authenticated": authenticated, "device_count": len(devices), "online_count": online_count, "last_error": data.get("last_error") if isinstance(data, Mapping) else None}}
    except Exception as exc:
        return {"health": "offline", "online": False, "diagnostics": {"source": "sonoff_client", "last_error": _safe_error(exc)}}


def _camera_status() -> Dict[str, Any]:
    try:
        payload = app_module.camera_config_payload()
        cameras = payload.get("cameras", []) if isinstance(payload, Mapping) else []
        if not cameras:
            return {"health": "unknown", "online": None, "diagnostics": {"source": "camera_config", "configured": bool(payload.get("loaded"))}}
        measured = []
        online_count = 0
        for camera in cameras:
            if not isinstance(camera, Mapping) or not camera.get("ip"):
                continue
            public = app_module.public_camera(dict(camera))
            measured.append(public)
            online_count += 1 if public.get("online") else 0
        if not measured:
            return {"health": "unknown", "online": None, "diagnostics": {"source": "camera_config", "configured": True, "measurable_count": 0}}
        health = "healthy" if online_count == len(measured) else ("warning" if online_count else "offline")
        return {"health": health, "online": online_count > 0, "last_update_ts": _now(), "diagnostics": {"source": "existing_camera_tcp_check", "configured_count": len(cameras), "measured_count": len(measured), "online_count": online_count}}
    except Exception as exc:
        return {"health": "unknown", "online": None, "diagnostics": {"source": "existing_camera_tcp_check", "last_error": _safe_error(exc)}}


def _probe_all() -> Dict[str, Dict[str, Any]]:
    now = _now()
    with _lock:
        if now - int(_probe_cache.get("ts") or 0) < PROBE_CACHE_SEC:
            return dict(_probe_cache.get("items") or {})
    gateway = os.getenv("CONDO_GATEWAY_IP", "").strip()
    home_ip = os.getenv("ZEROTIER_HOME_IP", "").strip()
    truenas_url = os.getenv("TRUENAS_HEALTH_URL", "").strip()
    truenas_host = os.getenv("TRUENAS_HOST", "").strip()
    internet_url = os.getenv("INTERNET_HEALTH_URL", "").strip()
    ha_base = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
    ha_token = os.getenv("HA_TOKEN", "").strip()
    checks = {
        "condo_router": lambda: _ping(gateway),
        "dns": _dns_check,
        "internet": lambda: _http(internet_url, "https"),
        "zerotier_condo": _zerotier_local,
        "zerotier_home": lambda: _ping(home_ip),
        "sonoff": _sonoff_status,
        "camera": _camera_status,
        "home_assistant": lambda: _http(f"{ha_base}/api/" if ha_base else "", "home_assistant_api", ha_token),
    }
    if truenas_url:
        checks["truenas"] = lambda: _http(truenas_url, "truenas_health_url")
    elif truenas_host:
        checks["truenas"] = lambda: _tcp(truenas_host, int(os.getenv("TRUENAS_PORT", "443")), "tcp")
    else:
        checks["truenas"] = lambda: {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="topology-probe") as pool:
        futures = {pool.submit(fn): name for name, fn in checks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {"health": "unknown", "online": None, "diagnostics": {"source": "probe", "last_error": _safe_error(exc)}}
    zt_local = results.get("zerotier_condo", {})
    zt_home = results.get("zerotier_home", {})
    if not home_ip:
        results["zerotier_tunnel"] = {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    elif zt_local.get("online") and zt_home.get("online"):
        results["zerotier_tunnel"] = {"health": "warning" if zt_home.get("health") == "warning" else "healthy", "online": True, "latency_ms": zt_home.get("latency_ms"), "last_update_ts": now, "diagnostics": {"source": "local_zerotier_plus_home_probe"}}
    else:
        results["zerotier_tunnel"] = {"health": "offline", "online": False, "last_update_ts": now, "diagnostics": {"source": "local_zerotier_plus_home_probe"}}
    dns = results.get("dns", {})
    https = results.get("internet", {})
    if dns.get("health") == "unknown" and https.get("health") == "unknown":
        results["cloudflare_wan"] = {"health": "unknown", "online": None, "diagnostics": {"source": "not_configured"}}
    elif dns.get("online") and https.get("online"):
        results["cloudflare_wan"] = {"health": "healthy", "online": True, "last_update_ts": now, "diagnostics": {"source": "dns_and_https"}}
    elif dns.get("online") or https.get("online"):
        results["cloudflare_wan"] = {"health": "warning", "online": True, "last_update_ts": now, "diagnostics": {"source": "partial_dns_https"}}
    else:
        results["cloudflare_wan"] = {"health": "offline", "online": False, "last_update_ts": now, "diagnostics": {"source": "dns_and_https"}}
    with _lock:
        _probe_cache["ts"] = now
        _probe_cache["items"] = dict(results)
    return results


def _base_nodes(now: int) -> Dict[str, Dict[str, Any]]:
    nodes = topology_runtime._original_base_nodes(now)
    probes = _probe_all()
    for node_id in ("internet", "cloudflare_wan", "condo_router", "zerotier_condo", "zerotier_tunnel", "zerotier_home", "truenas", "home_assistant", "sonoff", "camera"):
        if node_id in probes:
            nodes[node_id] = probes[node_id]
    tv = _tv_payload(now)
    nodes["lg_tv"] = {**tv, "diagnostics": {"source": "mqtt_state", "bridge_online": tv["bridge_online"], "state_age_sec": tv["state_age_sec"], "heartbeat_age_sec": tv["heartbeat_age_sec"]}}
    return nodes


def _health_summary(nodes: Dict[str, Dict[str, Any]]) -> tuple[int, int, int]:
    weights = {"dashboard": 3, "mqtt": 3, "home_assistant": 3, "zerotier_tunnel": 2, "internet": 2, "condo_router": 2, "sonoff": 1, "tuya": 1, "lg_tv": 1, "camera": 1, "pm25": 1, "truenas": 2}
    values = {"healthy": 1.0, "warning": 0.55, "offline": 0.0}
    total = 0.0
    score = 0.0
    measured = 0
    unknown = 0
    for node_id, node in nodes.items():
        health = str(node.get("health") or "unknown")
        if health not in values:
            unknown += 1
            continue
        measured += 1
        weight = weights.get(node_id, 1)
        total += weight
        score += values[health] * weight
    return (round(score / total * 100) if total else 0, measured, unknown)


def _topology_endpoint() -> Dict[str, Any]:
    now = _now()
    nodes = _base_nodes(now)
    roots = topology_runtime._apply_dependency_health(nodes)
    topology_runtime._capture_events(nodes)
    dependents = topology_runtime._dependents()
    public_nodes = []
    for node_id in topology_runtime.NODE_ORDER:
        node = dict(nodes[node_id])
        node.update({"id": node_id, "name": topology_runtime.NODE_LABELS[node_id], "dependencies": topology_runtime.DEPENDENCIES.get(node_id, []), "dependents": dependents.get(node_id, []), "capabilities": ["status", "diagnostics"]})
        public_nodes.append(node)
    score, measured, unknown = _health_summary(nodes)
    with topology_runtime._lock:
        events = list(topology_runtime._events)
    return {"ok": True, "ts": now, "overall_health": score, "measured_node_count": measured, "unknown_node_count": unknown, "nodes": public_nodes, "root_causes": roots, "events": events, "tv": _tv_payload(now)}


def _install_topology_override() -> None:
    if not hasattr(topology_runtime, "_original_base_nodes"):
        topology_runtime._original_base_nodes = topology_runtime._base_nodes
    topology_runtime._base_nodes = _base_nodes
    topology_runtime._tv_payload = _tv_payload
    topology_runtime._overall_health = lambda nodes: _health_summary(nodes)[0]
    for route in app_module.app.routes:
        if getattr(route, "path", None) == "/api/topology":
            route.endpoint = _topology_endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = _topology_endpoint


def _install_sonoff_log_classifier() -> None:
    try:
        import sonoff_client
        backend = sonoff_client._backend_sonoff

        def classify(detail):
            safe = backend.redact_payload(detail)
            if not isinstance(safe, Mapping):
                return
            status = safe.get("result_status")
            pre_ok = safe.get("pre_refresh_success") is not False
            post_ok = safe.get("post_refresh_success") is not False
            success = status in (None, 0, 200, "200", "ok", "success") and pre_ok and post_ok
            if success:
                return
            partial = status in (200, "200", "ok", "success") or pre_ok or post_ok
            label = "warning" if partial else "error"
            print(f"sonoff command {label}: " + json.dumps(safe, ensure_ascii=False), flush=True)

        backend.log_command_diag = classify
    except Exception:
        return


_install_mqtt_state_ownership()
_install_topology_override()
_install_sonoff_log_classifier()
