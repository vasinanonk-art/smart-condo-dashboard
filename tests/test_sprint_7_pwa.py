import json
import struct
from pathlib import Path

from backend import frontend_asset_version
from frontend_runtime import run_node


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
ASSETS = FRONTEND / "assets"


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def _worker_behavior() -> dict:
    source = json.dumps(str(FRONTEND / "service-worker.js"))
    script = f"""
const fs=require('fs'),vm=require('vm');
const handlers={{}};
const entries=new Map();
const events={{fetches:[],opens:0,puts:0}};
let offline=false;
const caches={{
  open:async()=>{{events.opens++;return {{
    match:async request=>entries.get(request.url),
    put:async(request,response)=>{{events.puts++;entries.set(request.url,response);}},
  }};}},
  keys:async()=>[],delete:async()=>true,
}};
const self={{
  location:{{origin:'https://condo.test'}},
  addEventListener:(name,handler)=>{{handlers[name]=handler;}},
  skipWaiting:async()=>{{}},clients:{{claim:async()=>{{}}}},
}};
const fetch=async request=>{{
  events.fetches.push(request.url);
  if(offline) throw new Error('offline');
  return new Response(request.url.includes('/assets/')?'asset':'network',{{status:200}});
}};
const context={{self,caches,fetch,URL,Response,Promise,console}};vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8').replaceAll('__ASSET_VERSION__','build123'),context);
async function dispatch(path,mode='cors'){{
  const request={{url:`https://condo.test${{path}}`,method:'GET',mode}};
  let responsePromise;
  handlers.fetch({{request,respondWith:value=>{{responsePromise=Promise.resolve(value);}}}});
  return responsePromise?await responsePromise:null;
}}
(async()=>{{
  const apiBefore={{opens:events.opens,puts:events.puts}};
  await dispatch('/api/auth/status');
  const apiAfter={{opens:events.opens,puts:events.puts}};
  await dispatch('/assets/dashboard_home.js?v=build123');
  const versioned={{opens:events.opens,puts:events.puts}};
  await dispatch('/assets/dashboard_home.js?v=old');
  const oldVersion={{opens:events.opens,puts:events.puts}};
  await dispatch('/assets/dashboard_home.js?v=build123&variant=extra');
  const extraQuery={{opens:events.opens,puts:events.puts}};
  await dispatch('/assets/dashboard_home.js');
  const unversioned={{opens:events.opens,puts:events.puts}};
  const online=await dispatch('/#topology','navigate');
  offline=true;
  const unavailable=await dispatch('/#topology','navigate');
  process.stdout.write(JSON.stringify({{
    apiBefore,apiAfter,versioned,oldVersion,extraQuery,unversioned,
    onlineStatus:online.status,
    unavailableStatus:unavailable.status,
    unavailableBody:await unavailable.text(),
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    return run_node(script)


def test_manifest_is_installable_and_icons_are_valid_pngs():
    manifest = json.loads((ASSETS / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["name"] == "Smart Condo"
    assert manifest["short_name"] == "Smart Condo"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["theme_color"] == "#09090B"
    assert manifest["background_color"] == "#09090B"
    assert _png_size(ASSETS / "icon-192.png") == (192, 192)
    assert _png_size(ASSETS / "icon-512.png") == (512, 512)
    assert _png_size(ASSETS / "apple-touch-icon.png") == (180, 180)


def test_root_scoped_worker_is_versioned_and_never_http_cached():
    response = frontend_asset_version.service_worker()
    body = bytes(response.body).decode("utf-8")

    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["service-worker-allowed"] == "/"
    assert "__ASSET_VERSION__" not in body
    assert frontend_asset_version.BUILD_VERSION in body
    assert any(
        getattr(route, "path", None) == "/service-worker.js"
        for route in frontend_asset_version.app.routes
    )


def test_worker_caches_only_current_version_static_assets():
    result = _worker_behavior()

    assert result["apiAfter"] == result["apiBefore"]
    assert result["versioned"] == {"opens": 1, "puts": 1}
    assert result["oldVersion"] == result["versioned"]
    assert result["extraQuery"] == result["versioned"]
    assert result["unversioned"] == result["versioned"]
    assert result["onlineStatus"] == 200
    assert result["unavailableStatus"] == 503
    assert "Connection unavailable" in result["unavailableBody"]
    assert "dashboard" in result["unavailableBody"]


def test_registration_and_mobile_standalone_contract_are_present():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    registration = (ASSETS / "dashboard_pwa.js").read_text(encoding="utf-8")
    worker = (FRONTEND / "service-worker.js").read_text(encoding="utf-8")

    assert 'name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert "apple-touch-icon.png?v=__ASSET_VERSION__" in html
    assert "dashboard_pwa.js?v=__ASSET_VERSION__" in html
    assert "window.isSecureContext" in registration
    assert "register('/service-worker.js'" in registration
    assert "updateViaCache:'none'" in registration
    assert "setInterval" not in registration + worker


def test_sensitive_routes_are_explicitly_excluded_from_cache_logic():
    worker = (FRONTEND / "service-worker.js").read_text(encoding="utf-8")

    assert "pathname.startsWith('/api/')" in worker
    assert "pathname === '/login'" in worker
    assert "pathname === '/logout'" in worker
    assert "isSensitivePath(url.pathname)" in worker
    assert "event.respondWith(fetch(request))" in worker
    assert "request.mode === 'navigate'" in worker
    assert "networkNavigation(request)" in worker
