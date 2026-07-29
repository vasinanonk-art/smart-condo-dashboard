import json
from pathlib import Path

from tests.frontend_runtime import run_node


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend/assets/dashboard_tplink_provider.js"


def frontend_behavior():
    source = json.dumps(str(SOURCE))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const requests=[];
const host={{
  innerHTML:'',
  attributes:{{}},
  setAttribute(name,value){{this.attributes[name]=value;}},
  removeAttribute(name){{delete this.attributes[name];}}
}};
const payloads={{
  '/api/tplink/providers/status':{{providers:{{tplink_camera:{{status:'healthy',ready:true,last_seen:'2026-07-29T14:15:16Z'}}}}}},
  '/api/tplink/providers/metadata':{{providers:{{tplink_camera:{{provider_name:'TP-Link Camera Provider',implementation_status:'read_only_skeleton'}}}}}},
  '/api/tplink/providers/capabilities':{{providers:{{tplink_camera:{{inventory:'Supported',health:'Supported',snapshot:'Not Supported',ptz:'Not Supported'}}}}}},
  '/api/tplink/providers/diagnostics':{{providers:{{tplink_camera:{{supported_capability_count:2,unsupported_capability_count:7,last_response:{{status:'Ready'}},empty_value:{{}}}}}}}},
  '/api/tplink/cameras':{{cameras:[{{display_name:'Living <Camera>',model:'Verified Model',online:true}}]}}
}};
const fetch=async (url,options)=>{{
  requests.push([url,options]);
  return {{ok:true,json:async()=>payloads[url]}};
}};
const document={{
  getElementById:id=>id==='tplinkProviderDashboard'?host:null,
  addEventListener:()=>{{}}
}};
const window={{}};
const context={{window,document,fetch,console}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
(async()=>{{
  const api=window.TPLinkProviderDashboard;
  const rows=api.capabilityRows(payloads['/api/tplink/providers/capabilities'].providers.tplink_camera);
  await api.load();
  process.stdout.write(JSON.stringify({{
    rows,
    requests,
    html:host.innerHTML,
    busy:host.attributes['aria-busy'] || null,
    endpoints:Object.keys(api.endpoints)
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )


def test_capabilities_render_supported_and_not_supported_explicitly():
    result = frontend_behavior()

    assert result["rows"] == [
        {"name": "inventory", "status": "Supported", "supported": True},
        {"name": "health", "status": "Supported", "supported": True},
        {"name": "snapshot", "status": "Not Supported", "supported": False},
        {"name": "ptz", "status": "Not Supported", "supported": False},
    ]
    assert "Not Supported" in result["html"]


def test_dashboard_loads_each_read_endpoint_once_and_escapes_inventory():
    result = frontend_behavior()

    assert result["endpoints"] == [
        "status",
        "metadata",
        "capabilities",
        "diagnostics",
        "inventory",
    ]
    assert len(result["requests"]) == 5
    assert len({request[0] for request in result["requests"]}) == 5
    assert all(
        request[1] == {"credentials": "same-origin"}
        for request in result["requests"]
    )
    assert "Living &lt;Camera&gt;" in result["html"]
    assert "Living <Camera>" not in result["html"]
    assert result["busy"] is None


def test_dashboard_has_no_operational_camera_controls():
    result = frontend_behavior()
    html = result["html"].casefold()

    assert "<button" not in html
    assert "snapshot" in html
    assert "not supported" in html


def test_diagnostics_format_objects_and_timestamps_for_people():
    result = frontend_behavior()

    assert "[object Object]" not in result["html"]
    assert "Ready" in result["html"]
    assert "Not available" in result["html"]
    assert "29 Jul 2026" in result["html"]
