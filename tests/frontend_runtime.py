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


def chart_scrub_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_pm25_hotfix.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
let renderedRows=[];
const window={{
  DASHBOARD_CHART_DEBUG:false,
  drawChart:(id,rows)=>{{renderedRows=rows;}}
}};
const document={{getElementById:()=>null}};
const context={{window,document,console}}; vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const api=window.DashboardChartInteraction;
const series=[{{key:'temperature'}},{{key:'pm25'}}];
const raw=[
  {{ts:3,temperature:23,pm25:13}},
  {{ts:1,temperature:21,pm25:11}},
  {{ts:2,temperature:22,pm25:12}},
  {{ts:2,temperature:24,pm25:null}},
  {{ts:4,temperature:null,pm25:null}},
  {{ts:5,temperature:'bad',pm25:''}}
];
const canonical=api.canonicalRows(raw,series);
const visible=api.buildVisibleSamples('overviewChart',raw,series);
window.drawChart('overviewChart',raw,series);

let rect={{left:100,top:20,width:450,height:155}};
const svg={{
  viewBox:{{baseVal:{{x:0,y:0,width:900,height:310}}}},
  getBoundingClientRect:()=>rect
}};
const plot={{left:48,right:882,top:18,bottom:275}};
const positions=api.samplePositions(visible.length,plot.left,plot.right);
const temperature=api.createSelectionModel(svg,plot,visible,positions);
const pm25=api.createSelectionModel(svg,plot,visible,positions);
const clientAtSvgX=x=>rect.left+x/900*rect.width;
const sweep=[-100,48,180,400,600,882,1200].map(x=>temperature.select(clientAtSvgX(x),60).index);
const marginLeft=temperature.select(rect.left,60).index;
const marginRight=temperature.select(rect.left+rect.width,60).index;
const pmSelection=pm25.select(clientAtSvgX(48),60).index;
const independentBefore=temperature.selectedIndex();
pm25.select(clientAtSvgX(882),60);
const independentAfter=temperature.selectedIndex();

rect={{left:40,top:10,width:900,height:310}};
const resized=temperature.select(40+441,60).index;
const singleRows=[{{ts:1,temperature:20}}];
const single=api.createSelectionModel(
  svg,plot,singleRows,api.samplePositions(1,plot.left,plot.right)
);
const singleSelections=[40,490,940].map(x=>single.select(x,60).index);
window.visibleRows=(id,rows)=>rows.filter((row,index)=>index%2===0);
const downsampled=api.buildVisibleSamples('overviewChart',[
  {{ts:1,temperature:10}},{{ts:2,temperature:20}},
  {{ts:3,temperature:30}},{{ts:4,temperature:40}},
  {{ts:5,temperature:50}}
],[{{key:'temperature'}}]);
const mouse=api.pointerCoordinates({{clientX:123,clientY:45}});
const touch=api.pointerCoordinates({{touches:[{{clientX:123,clientY:45}}]}});
process.stdout.write(JSON.stringify({{
  canonicalTs:canonical.map(row=>row.ts),
  duplicateValue:canonical.find(row=>row.ts===2).temperature,
  visibleTs:visible.map(row=>row.ts),
  renderedTs:renderedRows.map(row=>row.ts),
  nullNumeric:api.numeric(null),
  emptyNumeric:api.numeric(''),
  sweep,marginLeft,marginRight,resized,singleSelections,
  independent:independentBefore===independentAfter && pmSelection===0,
  downsampledTs:downsampled.map(row=>row.ts),
  mouseTouchParity:JSON.stringify(mouse)===JSON.stringify(touch)
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


def electricity_analytics_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_electricity.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const document={{
  readyState:'loading',body:{{appendChild:()=>{{}}}},querySelectorAll:()=>[],
  querySelector:(selector)=>selector==='[data-page="electricity"]'?{{}}:null,
  getElementById:()=>null,createElement:()=>({{}})
}};
const window={{
  safeText:String,refresh:async()=>{{}},renderPage:()=>{{}},currentPage:()=> 'overview',
  get:async()=>({{}}),nav:()=>{{}}
}};
const context={{
  window,document,console,fetch:async()=>({{}}),URL,URLSearchParams,Intl,Date,
  setTimeout,clearTimeout
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const api=window.DashboardElectricityHistory;
api.state.history={{bucket:'30m'}};
const rows=[
  {{timestamp:'2026-07-26T00:00:00+07:00',interval_start:'2026-07-26T00:00:00+07:00',interval_end:'2026-07-26T00:30:00+07:00',energy_kwh:1,cost_thb:5,data_quality:'valid'}},
  {{timestamp:'2026-07-26T00:30:00+07:00',interval_start:'2026-07-26T00:30:00+07:00',interval_end:'2026-07-26T01:00:00+07:00',energy_kwh:2,cost_thb:10,data_quality:'valid'}},
  {{timestamp:'2026-07-26T01:00:00+07:00',interval_start:'2026-07-26T01:00:00+07:00',interval_end:'2026-07-26T01:30:00+07:00',energy_kwh:3,cost_thb:15,data_quality:'valid'}},
  {{timestamp:'2026-07-26T04:00:00+07:00',interval_start:'2026-07-26T04:00:00+07:00',interval_end:'2026-07-26T04:30:00+07:00',energy_kwh:4,cost_thb:20,data_quality:'partial'}}
];
const average=api.movingAverage(rows,3,2700).map(row=>row.moving_average_kwh);
const statistics=api.analyticsStatistics(rows);
const tooltip=api.tooltipContent(rows[0]);
process.stdout.write(JSON.stringify({{
  average,
  highest:statistics.highest.energy,
  lowest:statistics.lowest.energy,
  averageHourly:statistics.averageHourly.energy,
  maximum:statistics.maximum.energy_kwh,
  minimum:statistics.minimum.energy_kwh,
  tooltip,
  bucket:api.bucketLabel('30m')
}}));
"""
    )
