import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str):
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js is unavailable; frontend runtime behavior requires Node.js")
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def chart_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_pm25_hotfix.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const window={{DASHBOARD_CHART_DEBUG:false}};
const document={{getElementById:()=>null}};
const context={{window,document,console}}; vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const api=window.DashboardChartInteraction;
const positions=api.samplePositions(4,10,100);
process.stdout.write(JSON.stringify({{
  positions,
  single:api.samplePositions(1,10,100),
  before:api.selectSampleIndex(-20,positions),
  firstBoundary:api.selectSampleIndex(24,positions),
  middle:api.selectSampleIndex(54,positions),
  after:api.selectSampleIndex(200,positions)
}}));
"""
    )


def topology_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_topology.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const document={{
  querySelectorAll:()=>[],
  querySelector:(selector)=>selector==='[data-page="topology"]'?{{}}:null,
  getElementById:()=>null
}};
const window={{
  safeText:String,refresh:async()=>{{}},renderPage:()=>{{}},currentPage:()=> 'overview',
  get:async()=>({{nodes:[]}}),addEventListener:()=>{{}},nav:()=>{{}}
}};
const context={{window,document,console,setTimeout,clearTimeout}}; vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const api=window.DashboardTopologyModel;
const normalized=api.normalize({{nodes:[
  {{id:'a',health:'bad',dependencies:null}},
  {{id:'a',health:'healthy'}},
  null
]}});
const nodes=api.normalize({{nodes:api.order.map(id=>({{id,health:'healthy'}}))}}).nodes;
const layout=api.layout(nodes,1400);
const routes=api.routes(layout);
const keys=routes.map(edge=>`${{edge.from}}>${{edge.to}}:${{edge.cat}}`);
process.stdout.write(JSON.stringify({{
  normalized:normalized.nodes,
  unique:new Set(keys).size===keys.length,
  required:keys,
  deterministic:JSON.stringify(api.layout(nodes,1400))===JSON.stringify(layout),
  diagnosticCount:api.diagnostics(nodes,layout,routes,[]).length
}}));
"""
    )
