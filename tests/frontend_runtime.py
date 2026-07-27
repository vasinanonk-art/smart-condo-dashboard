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


def electricity_export_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_electricity.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
(async()=>{{
  const events={{fetches:[],clicks:0,appends:0,removes:0,revokes:0}};
  const anchor={{click:()=>events.clicks++,remove:()=>events.removes++}};
  const document={{
    readyState:'loading',body:{{appendChild:()=>events.appends++}},
    querySelectorAll:()=>[],
    querySelector:(selector)=>selector==='[data-page="electricity"]'?{{}}:null,
    getElementById:()=>null,createElement:()=>anchor
  }};
  const window={{
    safeText:String,refresh:async()=>{{}},renderPage:()=>{{}},currentPage:()=> 'overview',
    get:async()=>({{}}),nav:()=>{{}}
  }};
  let resolveFetch;
  let fetchImpl=(url,options)=>{{
    events.fetches.push({{url,options}});
    return new Promise(resolve=>{{resolveFetch=resolve;}});
  }};
  const fetch=(url,options)=>fetchImpl(url,options);
  const URLShim={{
    createObjectURL:()=> 'blob:test',
    revokeObjectURL:()=>events.revokes++
  }};
  const immediateTimeout=callback=>{{callback();return 1;}};
  const context={{
    window,document,console,fetch,URL:URLShim,URLSearchParams,Intl,Date,
    setTimeout:immediateTimeout,clearTimeout:()=>{{}}
  }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync({source},'utf8'),context);
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  const api=window.DashboardElectricityHistory;
  api.state.history={{
    start:'2026-07-26T00:00:00+07:00',
    end:'2026-07-27T00:00:00+07:00',
    bucket:'30m'
  }};
  const first=api.csvExport();
  const duplicate=api.csvExport();
  const duplicateCount=events.fetches.length;
  resolveFetch({{
    ok:false,status:503,
    headers:{{get:name=>name.toLowerCase()==='content-type'?'application/json':null}},
    json:async()=>({{detail:'history temporarily unavailable'}}),
    text:async()=>''
  }});
  await Promise.all([first,duplicate]);
  const displayedError=api.state.historyError;
  api.state.history={{
    start:'2026-07-26T00:00:00+07:00',
    end:'2026-07-27T00:00:00+07:00',
    bucket:'30m'
  }};
  fetchImpl=async(url,options)=>{{
    events.fetches.push({{url,options}});
    return {{
      ok:true,status:200,
      headers:{{get:name=>name.toLowerCase()==='content-disposition'?'attachment; filename="safe.csv"':'text/csv; charset=utf-8'}},
      blob:async()=>({{size:12}})
    }};
  }};
  await api.csvExport();
  const selected=new URLSearchParams(events.fetches[0].url.split('?')[1]);
  process.stdout.write(JSON.stringify({{
    duplicateCount,displayedError,
    start:selected.get('start'),end:selected.get('end'),bucket:selected.get('bucket'),
    format:selected.get('format'),credentials:events.fetches[0].options.credentials,
    csrf:Object.keys(events.fetches[0].options.headers).some(key=>key.toLowerCase()==='x-csrf-token'),
    clicks:events.clicks,appends:events.appends,removes:events.removes,revokes:events.revokes,
    strides:['15m','30m','hour','3h','day'].map(bucket=>api.axisLabelStride(bucket,30))
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
