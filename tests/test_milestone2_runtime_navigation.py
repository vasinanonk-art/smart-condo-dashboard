import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime is not installed")
def test_final_runtime_navigation_replaces_legacy_six_item_dom():
    script = r"""
const fs = require('fs');
class Button {
  constructor(route, html) { this.dataset={nav:route}; this.html=html; this.attrs={}; this.classList={toggle:(name,on)=>{this.attrs[name]=on}}; }
  setAttribute(name,value){this.attrs[name]=value}
  removeAttribute(name){delete this.attrs[name]}
}
class Host {
  constructor(html=''){this.innerHTML=html}
  set innerHTML(value){this.html=value;this.buttons=[...value.matchAll(/<button[^>]*data-nav="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map(match=>new Button(match[1],match[0]))}
  get innerHTML(){return this.html}
}
const desktop = new Host('<button data-nav="overview">Home</button><button data-nav="devices">Devices</button><button data-nav="electricity">Electricity</button><button data-nav="camera">Cameras</button><button data-nav="more">More</button><button data-nav="history">History</button>');
const mobile = new Host(desktop.innerHTML);
global.window={nav:()=>{},renderPage:()=>{},currentPage:()=> 'history',S:{}};
global.document={
  querySelector(selector){if(selector==='.sidebar .nav')return desktop;if(selector==='.mobile-nav')return mobile;return null},
  querySelectorAll(selector){if(selector==='.nav [data-nav],.mobile-nav [data-nav]')return [...desktop.buttons,...mobile.buttons];return []},
  getElementById(){return null}
};
eval(fs.readFileSync(process.argv[1],'utf8'));
console.log(JSON.stringify({
  desktop:desktop.buttons.map(button=>button.dataset.nav),
  mobile:mobile.buttons.map(button=>button.dataset.nav),
  icons:mobile.buttons.filter(button=>button.html.includes('primary-nav-icon')).length,
  current:mobile.buttons.filter(button=>button.attrs['aria-current']==='page').map(button=>button.dataset.nav)
}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT / "frontend/assets/dashboard_milestone2.js")],
        check=True,
        capture_output=True,
        text=True,
    )
    runtime = json.loads(result.stdout)
    expected = ["overview", "devices", "electricity", "camera", "more"]
    assert runtime == {"desktop": expected, "mobile": expected, "icons": 5, "current": ["more"]}
