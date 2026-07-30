import json
from pathlib import Path

from tests.frontend_runtime import run_node


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend/assets/dashboard_device_health.js"


def frontend_behavior():
    source = json.dumps(str(SOURCE))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const listeners={{}};
const requests=[];
const host={{
  innerHTML:'',
  attributes:{{}},
  setAttribute(name,value){{this.attributes[name]=value;}}
}};
const payload={{
  summary:{{total:3,online:1,offline:1,unknown:1}},
  devices:[
    {{id:'tv',display_name:'LG <TV>',health:'healthy',health_indicator:'green',online:true,last_seen:'2026-07-30T03:00:00Z',response_time_ms:12.34}},
    {{id:'camera',display_name:'Camera',health:'offline',health_indicator:'red',online:false,last_seen:null,response_time_ms:null}},
    {{id:'fan',display_name:'Fan',health:'unknown',health_indicator:'yellow',online:null,last_seen:'invalid',response_time_ms:2}}
  ]
}};
const document={{
  hidden:false,
  getElementById:id=>id==='deviceHealthDashboard'?host:null,
  addEventListener:(name,handler)=>{{listeners[name]=handler;}}
}};
const window={{
  setInterval:(handler,delay)=>({{handler,delay}})
}};
const fetch=async (url,options)=>{{
  requests.push([url,options]);
  return {{ok:true,json:async()=>payload}};
}};
const context={{window,document,fetch,console,Date,Number}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
(async()=>{{
  const api=window.DeviceHealthDashboard;
  await api.load();
  process.stdout.write(JSON.stringify({{
    html:host.innerHTML,
    requests,
    busy:host.attributes['aria-busy'],
    statuses:payload.devices.map(api.statusLabel),
    missing:api.relativeTime(null),
    invalid:api.relativeTime('invalid'),
    timerDelay:(api.state.timer || {{}}).delay || null
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )


def test_device_health_card_renders_safe_status_and_metrics():
    result = frontend_behavior()

    assert "LG &lt;TV&gt;" in result["html"]
    assert "LG <TV>" not in result["html"]
    assert result["statuses"] == ["Online", "Offline", "Unknown"]
    assert "12.3 ms" in result["html"]
    assert "Not seen" in result["html"]
    assert "Not available" in result["html"]
    assert 'data-health="green"' in result["html"]
    assert 'data-health="red"' in result["html"]
    assert 'data-health="yellow"' in result["html"]


def test_device_health_uses_one_authenticated_read_request():
    result = frontend_behavior()

    assert result["requests"] == [[
        "/api/device-health",
        {
            "headers": {"Accept": "application/json"},
            "credentials": "same-origin",
        },
    ]]
    assert result["busy"] == "false"


def test_device_health_source_is_wired_into_dashboard():
    index = (ROOT / "frontend/index.html").read_text()

    assert 'id="deviceHealthDashboard"' in index
    assert "/assets/dashboard_device_health.css" in index
    assert "/assets/dashboard_device_health.js" in index
