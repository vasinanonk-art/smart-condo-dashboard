import json
import logging
import threading
import time

import bcrypt
import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend import ir_framework as ir
from backend import tuya_cloud_readonly as cloud
from backend.app_entry import app
from tests.frontend_runtime import run_node


class FakeIRClient:
    def __init__(self):
        self.commands = []
        self.status = {"power": "1", "mode": "0", "temp": "26", "wind": "0"}
        self.error = None
        self.entered = None
        self.release = None

    def send_ac_command(self, code, value):
        self.commands.append((code, value))
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait(2)
        if self.error:
            raise self.error
        if code == "power":
            self.status["power"] = str(value)
        if code == "temp":
            self.status["temp"] = str(value)
        return {"success": True, "result": True}

    def ac_status(self):
        return {"success": True, "result": dict(self.status)}


class CloudResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def test_verified_transport_emits_exactly_one_post_and_rejects_other_values(monkeypatch):
    config = cloud.TuyaCloudConfig(
        access_id="client",
        access_secret="secret",
        device_id="configured-device-123",
        region="sg",
        endpoint="https://openapi-sg.iotbing.com",
        timeout_sec=2,
    )
    client = cloud.TuyaCloudIRACClient(config)
    monkeypatch.setattr(client, "_token", lambda: "temporary-token")
    calls = []

    def urlopen(request, timeout):
        calls.append((request.get_method(), request.full_url, request.data, timeout))
        if request.get_method() == "GET":
            return CloudResponse({"success": True, "result": [{
                "remote_name": "Air",
                "brand_name": "Sharp",
                "category_id": 5,
                "remote_id": "verified-remote-123",
            }]})
        return CloudResponse({"success": True, "result": True})

    monkeypatch.setattr(cloud.urllib.request, "urlopen", urlopen)
    assert client.send_ac_command("temp", 27)["result"] is True
    assert [method for method, *_rest in calls].count("POST") == 1
    assert json.loads(next(data for method, _url, data, _timeout in calls if method == "POST")) == {
        "code": "temp",
        "value": 27,
    }
    prior = len(calls)
    for code, value in (("mode", 0), ("temp", 17), ("temp", 31), ("power", 2)):
        with pytest.raises(cloud.TuyaCloudError, match="command_not_allowed"):
            client.send_ac_command(code, value)
    assert len(calls) == prior


def _configure(monkeypatch, fake):
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "smartlife_cloud")
    monkeypatch.setenv("TUYA_CLOUD_ACCESS_ID", "test-access")
    monkeypatch.setenv("TUYA_CLOUD_ACCESS_SECRET", "test-secret")
    monkeypatch.setenv("TUYA_CLOUD_DEVICE_ID", "configured-device-123")
    monkeypatch.setenv("TUYA_CLOUD_REGION", "sg")
    monkeypatch.setattr(cloud, "configured_ir_client", lambda: fake)
    driver = ir.DRIVERS["tuya_ir_ac"]
    driver._last_attempt = 0
    driver._last_commanded.clear()
    driver._last_error = None
    driver._last_response = None
    queue = ir._queue("bed-room-air-conditioner")
    with queue._lock:
        queue._pending.clear()
        queue._draining = False
    return driver


def _auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "ac-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv(
        "DASHBOARD_SESSION_SECRET",
        "epic-22d-session-secret-long-enough",
    )
    client = TestClient(app, base_url="http://testserver")
    login = client.post(
        "/api/auth/login",
        json={"username": "ac-test", "password": "password"},
    )
    return client, login.json()["csrf_token"]


def _post(client, csrf, payload):
    return client.post(
        "/api/ir/bed-room-air-conditioner/command",
        json=payload,
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )


def test_write_requires_authentication_and_csrf(monkeypatch):
    fake = FakeIRClient()
    _configure(monkeypatch, fake)
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "ac-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv(
        "DASHBOARD_SESSION_SECRET",
        "epic-22d-session-secret-long-enough",
    )
    client = TestClient(app, base_url="http://testserver")
    path = "/api/ir/bed-room-air-conditioner/command"
    assert client.post(path, json={"command": "power_on"}).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "ac-test", "password": "password"},
    ).status_code == 200
    assert client.post(path, json={"command": "power_on"}).status_code == 403
    assert fake.commands == []


def test_allowlist_boundaries_and_exactly_one_post(monkeypatch):
    fake = FakeIRClient()
    driver = _configure(monkeypatch, fake)
    client, csrf = _auth_client(monkeypatch)

    assert _post(client, csrf, {"capability": "temperature", "value": 17}).status_code == 422
    assert _post(client, csrf, {"capability": "temperature", "value": 31}).status_code == 422
    assert _post(client, csrf, {"command": "mode_cool"}).status_code == 422
    assert _post(client, csrf, {"command": "fan_high"}).status_code == 422
    assert _post(client, csrf, {"command": "swing_on"}).status_code == 422

    response = _post(client, csrf, {"capability": "temperature", "value": 18})
    assert response.status_code == 200
    assert fake.commands == [("temp", 18)]
    assert response.json()["physical_state_confirmed"] is False
    assert response.json()["last_commanded"]["target_temperature"] == 18

    driver._last_attempt = 0
    response = _post(client, csrf, {"capability": "temperature", "value": 30})
    assert response.status_code == 200
    assert fake.commands == [("temp", 18), ("temp", 30)]


def test_rate_limit_and_zero_retry_on_timeout(monkeypatch):
    fake = FakeIRClient()
    driver = _configure(monkeypatch, fake)
    client, csrf = _auth_client(monkeypatch)

    assert _post(client, csrf, {"command": "power_on"}).status_code == 200
    limited = _post(client, csrf, {"command": "power_off"})
    assert limited.status_code == 429
    assert fake.commands == [("power", 1)]

    driver._last_attempt = 0
    fake.error = cloud.TuyaCloudError("tuya_cloud_timeout")
    timed_out = _post(client, csrf, {"command": "power_off"})
    assert timed_out.status_code == 504
    assert fake.commands == [("power", 1), ("power", 0)]
    assert timed_out.json()["attempts"] == 1


def test_per_device_inflight_command_is_rejected(monkeypatch):
    fake = FakeIRClient()
    _configure(monkeypatch, fake)
    fake.entered = threading.Event()
    fake.release = threading.Event()
    results = []

    def first():
        results.append(ir.execute_command(
            "bed-room-air-conditioner",
            ir.IRCommandRequest(command="power_on"),
            authenticated_user="first-user",
        ))

    thread = threading.Thread(target=first)
    thread.start()
    assert fake.entered.wait(1)
    second = ir.execute_command(
        "bed-room-air-conditioner",
        ir.IRCommandRequest(command="power_off"),
        authenticated_user="second-user",
    )
    assert isinstance(second, JSONResponse)
    assert second.status_code == 409
    assert fake.commands == [("power", 1)]
    fake.release.set()
    thread.join(2)
    assert results and results[0]["ok"] is True


def test_audit_is_sanitized_and_status_is_explicitly_assumed(monkeypatch, caplog):
    fake = FakeIRClient()
    _configure(monkeypatch, fake)
    caplog.set_level(logging.INFO, logger="smart_condo.ir.command")
    result = ir.execute_command(
        "bed-room-air-conditioner",
        ir.IRCommandRequest(command="temperature_27"),
        authenticated_user="owner@example.test",
    )
    assert result["ok"] is True
    log = caplog.text
    assert "user=owner@example.test" in log
    assert "device=bed-room-air-conditioner" in log
    assert "command=temperature_27" in log
    assert "value=27" in log
    assert "test-secret" not in log
    assert "configured-device-123" not in log

    status = ir.bedroom_ac_status()
    assert status["last_commanded"] == {
        "power": 1,
        "target_temperature": 27,
        "retrieved_at": status["last_commanded"]["retrieved_at"],
    }
    assert status["state_quality"] == "assumed"
    assert status["physical_state_confirmed"] is False


def test_cloud_error_fails_closed(monkeypatch):
    fake = FakeIRClient()
    _configure(monkeypatch, fake)
    fake.error = cloud.TuyaCloudError("tuya_cloud_unavailable")
    result = ir.execute_command(
        "bed-room-air-conditioner",
        ir.IRCommandRequest(command="power_off"),
    )
    assert isinstance(result, JSONResponse)
    assert result.status_code == 502
    assert fake.commands == [("power", 0)]


def test_frontend_confirmation_inflight_and_supported_controls_only():
    source = json.dumps("frontend/assets/dashboard_household_devices.js")
    result = run_node(f"""
const fs=require('fs'),vm=require('vm');
const listeners=new Map(); let commandFetches=0,confirmResult=false,resolveCommand;
const makeControl=(command,confirm='false')=>({{
  dataset:{{householdIrDevice:'bed-room-air-conditioner',householdIrCommand:command,householdIrConfirm:confirm}},
  disabled:false,addEventListener:(name,fn)=>listeners.set(command+name,fn)
}});
const on=makeControl('power_on','true'),off=makeControl('power_off','true'),temp=makeControl('', 'false');
temp.dataset.householdIrCapability='temperature'; temp.value='27';
const host={{querySelectorAll:selector=>{{
  if(selector==='[data-household-ir-device]') return [on,off,temp];
  if(selector==='[data-household-ir-command]') return [on,off];
  if(selector==='input[type="range"][data-household-ir-capability]') return [temp];
  return [];
}}}};
const UI={{safe:String,actionButton:options=>options.label,deviceDetails:()=>'',deviceCard:()=>'',toast:()=>{{}}}};
const document={{querySelector:()=>null,getElementById:()=>null}};
const fetch=(url,options={{}})=>{{
  if(url==='/api/devices') return Promise.resolve({{ok:true,json:async()=>({{devices:[]}})}});
  commandFetches++;
  return new Promise(resolve=>{{resolveCommand=()=>resolve({{ok:true,json:async()=>({{last_commanded:{{power:1}}}})}});}});
}};
const window={{HouseholdUI:UI,renderPage:()=>{{}},confirm:()=>confirmResult}};
const context={{window,document,fetch,console,setTimeout,clearTimeout}};vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
window.DashboardHouseholdDevices.bindIrCommands(host);
(async()=>{{
  listeners.get('power_onclick')(); await new Promise(r=>setImmediate(r));
  const cancelled=commandFetches===0;
  confirmResult=true; listeners.get('power_onclick')(); await new Promise(r=>setImmediate(r));
  const disabledDuring=on.disabled&&off.disabled&&temp.disabled;
  resolveCommand(); await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));
  const enabledAfter=!on.disabled&&!off.disabled&&!temp.disabled;
  const device={{id:'bed-room-air-conditioner',capabilities:{{ir:[
    {{id:'power',type:'toggle',confirm:true,commands:[{{id:'power_on',label:'Power On'}},{{id:'power_off',label:'Power Off'}}]}},
    {{id:'temperature',type:'range',min:18,max:30,step:1,unit:'°C',commands:Array.from({{length:13}},(_,i)=>({{id:'temperature_'+(18+i),value:18+i}}))}}
  ]}},state:{{ir_diagnostics:{{last_commanded:{{target_temperature:27}}}}}}}};
  const rendered=window.DashboardHouseholdDevices.irActions(device);
  process.stdout.write(JSON.stringify({{cancelled,disabledDuring,enabledAfter,commandFetches,
    hasPower:rendered.includes('Power On')&&rendered.includes('Power Off'),
    hasTemperature:rendered.includes('data-household-ir-capability'),
    unsupported:['Mode','Fan','Swing','Scene','Learning'].some(label=>rendered.includes(label))}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
""")
    assert result == {
        "cancelled": True,
        "disabledDuring": True,
        "enabledAfter": True,
        "commandFetches": 1,
        "hasPower": True,
        "hasTemperature": True,
        "unsupported": False,
    }
