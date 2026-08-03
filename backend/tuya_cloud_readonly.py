"""Minimal, fail-closed Tuya Cloud client for read-only device inventory."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping


_REGION_ENDPOINTS = {"sg": "https://openapi-sg.iotbing.com"}
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MAX_RESPONSE_BYTES = 1_000_000


class TuyaCloudError(RuntimeError):
    """Safe Tuya Cloud failure with no credential or identifier content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TuyaCloudConfig:
    access_id: str
    access_secret: str
    device_id: str
    region: str
    endpoint: str
    timeout_sec: float

    @classmethod
    def from_environment(cls) -> "TuyaCloudConfig":
        access_id = os.getenv("TUYA_CLOUD_ACCESS_ID", "").strip()
        access_secret = os.getenv("TUYA_CLOUD_ACCESS_SECRET", "").strip()
        device_id = os.getenv("TUYA_CLOUD_DEVICE_ID", "").strip()
        region = os.getenv("TUYA_CLOUD_REGION", "sg").strip().casefold()
        if not access_id or not access_secret or not device_id:
            raise TuyaCloudError("tuya_cloud_not_configured")
        if region not in _REGION_ENDPOINTS:
            raise TuyaCloudError("tuya_cloud_region_unsupported")
        if not _DEVICE_ID.fullmatch(device_id):
            raise TuyaCloudError("tuya_cloud_device_id_invalid")
        try:
            timeout = float(os.getenv("TUYA_CLOUD_TIMEOUT_SEC", "5"))
        except ValueError as exc:
            raise TuyaCloudError("tuya_cloud_timeout_invalid") from exc
        return cls(
            access_id=access_id,
            access_secret=access_secret,
            device_id=device_id,
            region=region,
            endpoint=_REGION_ENDPOINTS[region],
            timeout_sec=max(1.0, min(10.0, timeout)),
        )

    def fingerprint(self) -> str:
        raw = "\0".join((
            self.access_id,
            self.access_secret,
            self.device_id,
            self.region,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_path(path: str, query: Mapping[str, Any] | None = None) -> str:
    if not query:
        return path
    encoded = urllib.parse.urlencode(
        sorted((str(key), str(value)) for key, value in query.items()),
        quote_via=urllib.parse.quote,
        safe="",
    )
    return f"{path}?{encoded}"


def _string_to_sign(method: str, canonical_path: str, body: bytes = b"") -> str:
    content_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{content_hash}\n\n{canonical_path}"


def _signature(
    secret: str,
    client_id: str,
    timestamp_ms: str,
    canonical_path: str,
    access_token: str = "",
    method: str = "GET",
    body: bytes = b"",
) -> str:
    payload = (
        client_id
        + access_token
        + timestamp_ms
        + _string_to_sign(method, canonical_path, body)
    )
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


class TuyaCloudReadOnlyClient:
    """Signed Tuya OpenAPI client restricted to one configured device."""

    def __init__(self, config: TuyaCloudConfig) -> None:
        self.config = config
        escaped = re.escape(config.device_id)
        self._allowed_paths = (
            re.compile(rf"^/v1\.0/iot-03/devices/{escaped}$"),
            re.compile(rf"^/v1\.2/iot-03/devices/{escaped}/specification$"),
            re.compile(rf"^/v1\.0/iot-03/devices/{escaped}/status$"),
            re.compile(rf"^/v1\.0/iot-03/devices/{escaped}/functions$"),
        )
        self._token_lock = threading.Lock()
        self._access_token = ""
        self._token_valid_until = 0.0

    def _path_allowed(self, path: str) -> bool:
        return any(pattern.fullmatch(path) for pattern in self._allowed_paths)

    def request(self, method: str, path: str) -> Mapping[str, Any]:
        if method.upper() != "GET":
            raise TuyaCloudError("tuya_cloud_method_not_allowed")
        if not self._path_allowed(path):
            raise TuyaCloudError("tuya_cloud_path_not_allowed")
        return self._signed_get(path, self._token())

    def _token(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._token_valid_until:
            return self._access_token
        with self._token_lock:
            now = time.monotonic()
            if self._access_token and now < self._token_valid_until:
                return self._access_token
            canonical = _canonical_path("/v1.0/token", {"grant_type": 1})
            payload = self._raw_get(canonical, access_token="")
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise TuyaCloudError("tuya_cloud_token_invalid")
            token = result.get("access_token")
            expires = result.get("expire_time")
            if not isinstance(token, str) or not token:
                raise TuyaCloudError("tuya_cloud_token_invalid")
            try:
                lifetime = max(1.0, float(expires))
            except (TypeError, ValueError) as exc:
                raise TuyaCloudError("tuya_cloud_token_invalid") from exc
            margin = min(60.0, lifetime * 0.1)
            self._access_token = token
            self._token_valid_until = time.monotonic() + lifetime - margin
            return token

    def _signed_get(self, path: str, access_token: str) -> Mapping[str, Any]:
        return self._raw_get(path, access_token=access_token)

    def _raw_get(
        self,
        canonical_path: str,
        access_token: str,
    ) -> Mapping[str, Any]:
        return self._raw_request("GET", canonical_path, access_token, b"")

    def _raw_request(
        self,
        method: str,
        canonical_path: str,
        access_token: str,
        body: bytes,
    ) -> Mapping[str, Any]:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "client_id": self.config.access_id,
            "sign": _signature(
                self.config.access_secret,
                self.config.access_id,
                timestamp,
                canonical_path,
                access_token,
                method,
                body,
            ),
            "sign_method": "HMAC-SHA256",
            "t": timestamp,
        }
        if access_token:
            headers["access_token"] = access_token
        if body:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.config.endpoint + canonical_path,
            data=body or None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.config.timeout_sec,
            ) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except TimeoutError as exc:
            raise TuyaCloudError("tuya_cloud_timeout") from exc
        except urllib.error.HTTPError as exc:
            raise TuyaCloudError("tuya_cloud_http_error") from exc
        except urllib.error.URLError as exc:
            reason = (
                "tuya_cloud_timeout"
                if isinstance(exc.reason, TimeoutError)
                else "tuya_cloud_unavailable"
            )
            raise TuyaCloudError(reason) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TuyaCloudError("tuya_cloud_response_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TuyaCloudError("tuya_cloud_response_invalid") from exc
        if not isinstance(payload, Mapping):
            raise TuyaCloudError("tuya_cloud_response_invalid")
        if payload.get("success") is not True:
            raise TuyaCloudError("tuya_cloud_api_error")
        return payload

    def device_information(self) -> Mapping[str, Any]:
        return self.request(
            "GET",
            f"/v1.0/iot-03/devices/{self.config.device_id}",
        )

    def device_specification(self) -> Mapping[str, Any]:
        return self.request(
            "GET",
            f"/v1.2/iot-03/devices/{self.config.device_id}/specification",
        )

    def device_status(self) -> Mapping[str, Any]:
        return self.request(
            "GET",
            f"/v1.0/iot-03/devices/{self.config.device_id}/status",
        )

    def device_functions(self) -> Mapping[str, Any]:
        return self.request(
            "GET",
            f"/v1.0/iot-03/devices/{self.config.device_id}/functions",
        )


class TuyaCloudIRACClient(TuyaCloudReadOnlyClient):
    """Narrow verified T3/Sharp AC transport.

    Inventory and status remain GET-only. The sole write path accepts only the
    two verified AC properties and always emits exactly one HTTP request.
    """

    _REMOTE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
    _COMMAND_VALUES = {
        "power": frozenset({0, 1}),
        "temp": frozenset(range(18, 31)),
    }

    def _ir_path(self, suffix: str) -> str:
        return f"/v2.0/infrareds/{self.config.device_id}{suffix}"

    def remote_inventory(self) -> Mapping[str, Any]:
        return self._signed_get(self._ir_path("/remotes"), self._token())

    def _verified_air_remote_id(self) -> str:
        payload = self.remote_inventory()
        remotes = payload.get("result")
        if not isinstance(remotes, list):
            raise TuyaCloudError("tuya_ir_remote_inventory_invalid")
        matching = [
            item for item in remotes
            if isinstance(item, Mapping)
            and item.get("remote_name") == "Air"
            and item.get("brand_name") == "Sharp"
            and item.get("category_id") == 5
        ]
        if len(matching) != 1:
            raise TuyaCloudError("tuya_ir_verified_remote_not_found")
        remote_id = str(matching[0].get("remote_id") or "")
        if not self._REMOTE_ID.fullmatch(remote_id):
            raise TuyaCloudError("tuya_ir_remote_id_invalid")
        return remote_id

    def ac_status(self) -> Mapping[str, Any]:
        remote_id = self._verified_air_remote_id()
        return self._signed_get(
            self._ir_path(f"/remotes/{remote_id}/ac/status"),
            self._token(),
        )

    def send_ac_command(
        self,
        code: str,
        value: int,
        *,
        on_post_attempt: Callable[[], None] | None = None,
    ) -> Mapping[str, Any]:
        if code not in self._COMMAND_VALUES or value not in self._COMMAND_VALUES[code]:
            raise TuyaCloudError("tuya_ir_command_not_allowed")
        remote_id = self._verified_air_remote_id()
        path = self._ir_path(f"/air-conditioners/{remote_id}/command")
        body = json.dumps(
            {"code": code, "value": value},
            separators=(",", ":"),
        ).encode("utf-8")
        if on_post_attempt is not None:
            on_post_attempt()
        return self._raw_request("POST", path, self._token(), body)


_CLIENT_LOCK = threading.Lock()
_CLIENT: TuyaCloudReadOnlyClient | None = None
_CLIENT_FINGERPRINT = ""
_IR_CLIENT: TuyaCloudIRACClient | None = None
_IR_CLIENT_FINGERPRINT = ""


def configured_client() -> TuyaCloudReadOnlyClient:
    global _CLIENT, _CLIENT_FINGERPRINT
    config = TuyaCloudConfig.from_environment()
    fingerprint = config.fingerprint()
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_FINGERPRINT != fingerprint:
            _CLIENT = TuyaCloudReadOnlyClient(config)
            _CLIENT_FINGERPRINT = fingerprint
        return _CLIENT


def configured_ir_client() -> TuyaCloudIRACClient:
    global _IR_CLIENT, _IR_CLIENT_FINGERPRINT
    config = TuyaCloudConfig.from_environment()
    fingerprint = config.fingerprint()
    with _CLIENT_LOCK:
        if _IR_CLIENT is None or _IR_CLIENT_FINGERPRINT != fingerprint:
            _IR_CLIENT = TuyaCloudIRACClient(config)
            _IR_CLIENT_FINGERPRINT = fingerprint
        return _IR_CLIENT


def reset_client() -> None:
    global _CLIENT, _CLIENT_FINGERPRINT, _IR_CLIENT, _IR_CLIENT_FINGERPRINT
    with _CLIENT_LOCK:
        _CLIENT = None
        _CLIENT_FINGERPRINT = ""
        _IR_CLIENT = None
        _IR_CLIENT_FINGERPRINT = ""
