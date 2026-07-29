"""Authenticated read-only dashboard projection for TP-Link providers."""

from __future__ import annotations

from typing import Any

from backend import app as app_module
from backend.tplink_camera_provider import (
    TPLinkCameraProvider,
    register_camera_provider,
)
from backend.tplink_connector import TPLinkConnector


app = app_module.app
_connector = TPLinkConnector()
_camera_provider = TPLinkCameraProvider()
register_camera_provider(_connector, _camera_provider)


def _providers(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "providers": payload}


@app.on_event("startup")
async def initialize_tplink_dashboard_provider() -> None:
    await _connector.initialize()


@app.on_event("shutdown")
async def shutdown_tplink_dashboard_provider() -> None:
    await _connector.shutdown()


@app.get("/api/tplink/providers/status")
async def tplink_provider_status() -> dict[str, Any]:
    health = await _connector.health()
    return _providers({
        provider_id: provider_health.to_dict()
        for provider_id, provider_health in health.items()
    })


@app.get("/api/tplink/providers/metadata")
async def tplink_provider_metadata() -> dict[str, Any]:
    return _providers({
        _camera_provider.provider_id: _camera_provider.describe().to_dict(),
    })


@app.get("/api/tplink/providers/capabilities")
async def tplink_provider_capabilities() -> dict[str, Any]:
    return _providers({
        _camera_provider.provider_id:
            _camera_provider.capability_discovery(),
    })


@app.get("/api/tplink/providers/diagnostics")
async def tplink_provider_diagnostics() -> dict[str, Any]:
    return _providers({
        _camera_provider.provider_id: _camera_provider.diagnostics(),
    })


@app.get("/api/tplink/cameras")
async def tplink_camera_inventory() -> dict[str, Any]:
    inventory = await _connector.inventory()
    cameras = [
        device.to_dict()
        for device in inventory
        if device.kind.value == "camera"
    ]
    return {
        "ok": True,
        "provider_id": _camera_provider.provider_id,
        "camera_count": len(cameras),
        "cameras": cameras,
    }
