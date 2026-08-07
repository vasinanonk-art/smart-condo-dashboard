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


def electricity_settings_navigation_behavior():
    settings_source = json.dumps(str(ROOT / "frontend/assets/dashboard_electricity_settings_hotfix17.js"))
    dashboard_source = json.dumps(str(ROOT / "frontend/assets/dashboard_v3.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const elements={{}};
const classList=()=>({{toggle:()=>{{}},add:()=>{{}},remove:()=>{{}}}});
function element(id=''){{
  if(elements[id]) return elements[id];
  const listeners={{}};
  const inputs=new Proxy({{}},{{get:(target,key)=>target[key]||(target[key]={{value:'',checked:false}})}});
  const item={{
    id,dataset:{{}},style:{{}},classList:classList(),hidden:false,disabled:false,textContent:'',
    value:'',checked:false,innerHTML:'',elements:inputs,
    addEventListener:(name,fn)=>{{listeners[name]=fn;}},
    dispatch:(name)=>listeners[name]?.({{preventDefault:()=>{{}}}}),
    querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null,
    setAttribute:()=>{{}},removeAttribute:()=>{{}},insertAdjacentHTML:()=>{{}},appendChild:()=>{{}}
  }};
  elements[id]=item; return item;
}}
const settingsPage=element('settingsPage');
const navButton=element('settingsNav'); navButton.dataset.nav='settings'; navButton.onclick=()=>"canonical";
const document={{
  visibilityState:'visible',documentElement:{{dataset:{{}}}},
  getElementById:id=>element(id),
  querySelector:selector=>selector==='[data-page="settings"]'?element('settingsSection'):selector==='.main'?element('main'):null,
  querySelectorAll:selector=>selector==='[data-nav]'?[navButton]:[],
  createElement:()=>element('created'),addEventListener:()=>{{}}
}};
let settingsVersion=1;
const fetchCalls=[];
const fetch=async url=>{{
  fetchCalls.push(url);
  const settings={{electricity:{{billing_cycle_day:7,timezone:'Asia/Bangkok',tariff:{{
    tariff_name:`Tariff ${{settingsVersion}}`,effective_date:'2026-01-01',source:'manual',version:'v1',
    tiers:[{{up_to_kwh:150,rate:3.1}}],ft_rate:0.1572,service_charge:24.62,vat_percent:7,minimum_charge:0
  }}}},dashboard:{{timezone:'Asia/Bangkok'}},maintenance:{{}}}};
  return {{ok:true,json:async()=>url==='/api/settings'?settings:{{}}}};
}};
let timerId=0;
const window={{safeText:String,DASHBOARD_CHART_DEBUG:false,addEventListener:()=>{{}},scrollTo:()=>{{}}}};
const context={{window,document,location:{{hostname:'dashboard',hash:'#overview'}},fetch,console,
  AbortController,FormData:class {{}},setInterval:()=>++timerId,clearInterval:()=>{{}},Promise}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({settings_source},'utf8'),context);
const handlerPreserved=navButton.onclick() === 'canonical';
const api=window.DashboardElectricitySettings;
(async()=>{{
  await api.activate();
  const first={{...api.diagnostics(),name:element('electricitySettingsForm').elements.tariff_name.value}};
  await api.activate();
  const duplicate={{...api.diagnostics()}};
  element('electricitySettingsForm').elements.tariff_name.value='Unsaved draft';
  element('electricitySettingsForm').dispatch('input');
  settingsVersion=2;
  api.deactivate(); await api.activate();
  const returned={{...api.diagnostics(),name:element('electricitySettingsForm').elements.tariff_name.value}};

  let activated=0,deactivated=0,billingActivated=0,billingDeactivated=0;
  window.DashboardElectricitySettings={{activate:()=>activated++,deactivate:()=>deactivated++}};
  window.DashboardElectricityBilling={{activate:()=>billingActivated++,deactivate:()=>billingDeactivated++}};
  document.querySelectorAll=selector=>selector==='[data-nav]'?[navButton]:selector==='.page'?[]:[];
  document.querySelector=()=>null;
  window.requestAnimationFrame=fn=>fn(); window.matchMedia=()=>({{matches:true}});
  context.history={{replaceState:()=>{{}}}};
  vm.runInContext(fs.readFileSync({dashboard_source},'utf8'),context);
  context.nav('settings'); context.nav('settings'); context.nav('overview');
  context.nav('electricity'); context.nav('electricity'); context.nav('history'); context.nav('overview');
  process.stdout.write(JSON.stringify({{
    handlerPreserved,first,duplicate,returned,
    settingsRequests:fetchCalls.filter(url=>url==='/api/settings').length,
    canonicalNavigation:{{activated,deactivated,billingActivated,billingDeactivated}}
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
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
const interactionBounds=api.interactionBounds(svg,plot);
const endpointConfigurations=[
  ['temperature-portrait',{{left:20,top:10,width:700,height:310}},plot],
  ['humidity-landscape',{{left:100,top:10,width:1100,height:310}},plot],
  ['pm25-landscape',{{left:80,top:10,width:1024,height:300}},plot],
  ['electricity-portrait',{{left:12,top:10,width:744,height:300}},{{left:58,right:880,top:18,bottom:222}}],
].map(([name,configurationRect,configurationPlot])=>{{
  const configuredSvg={{
    viewBox:{{baseVal:{{x:0,y:0,width:900,height:name.startsWith('electricity')?260:310}}}},
    getBoundingClientRect:()=>configurationRect,
    getAttribute:()=>null
  }};
  const configuredRows=Array.from({{length:48}},(_,index)=>({{ts:index+1,temperature:index}}));
  const configuredPositions=api.samplePositions(
    configuredRows.length,configurationPlot.left,configurationPlot.right
  );
  const model=api.createSelectionModel(
    configuredSvg,configurationPlot,configuredRows,configuredPositions
  );
  const coordinates=[
    configurationRect.left-4,
    configurationRect.left,
    configurationRect.left+configurationRect.width,
    configurationRect.left+configurationRect.width+4
  ];
  const mouse=coordinates.map(clientX=>model.select(clientX,80));
  const touch=coordinates.map(clientX=>{{
    const pointer=api.pointerCoordinates({{touches:[{{clientX,clientY:80}}]}});
    return model.select(pointer.clientX,pointer.clientY);
  }});
  return {{
    name,
    indices:mouse.map(item=>item.index),
    timestamps:mouse.map(item=>item.row.ts),
    markerPositions:mouse.map(item=>item.sampleX),
    touchIndices:touch.map(item=>item.index),
    geometry:api.svgViewportGeometry(configuredSvg)
  }};
}});
const wideRect={{left:100,top:10,width:1100,height:310}};
const wideSvg={{
  viewBox:{{baseVal:{{x:0,y:0,width:900,height:310}}}},
  getBoundingClientRect:()=>wideRect,
  getAttribute:()=>null
}};
const wideGeometry=api.svgViewportGeometry(wideSvg);
const firstClient=wideRect.left+wideGeometry.offsetX+plot.left*wideGeometry.scaleX;
const lastClient=wideRect.left+wideGeometry.offsetX+plot.right*wideGeometry.scaleX;
const oldFirstX=(firstClient-wideRect.left)/wideRect.width*900;
const oldLastX=(lastClient-wideRect.left)/wideRect.width*900;
const convertedFirst=api.clientToSvg(wideSvg,firstClient,80).x;
const convertedLast=api.clientToSvg(wideSvg,lastClient,80).x;
const probeRows=[{{ts:1,temperature:10}},{{ts:2,temperature:20}},{{ts:3,temperature:30}}];
const probePositions=api.samplePositions(probeRows.length,plot.left,plot.right);
const probeWrap={{
  getBoundingClientRect:()=>({{left:wideRect.left,top:wideRect.top,width:wideRect.width,height:wideRect.height}}),
  querySelector:()=>null
}};
const probeSvg={{
  viewBox:{{baseVal:{{x:0,y:0,width:900,height:310}}}},
  getBoundingClientRect:()=>wideRect,
  getAttribute:()=>null,
  parentElement:probeWrap,
  appendChild:()=>{{}}
}};
const probeHit={{style:{{}},setAttribute:()=>{{}}}};
const probeLayer={{style:{{}}}};
const probeCross={{
  attributes:{{}},
  setAttribute(name,value){{this.attributes[name]=Number(value);}}
}};
const probePoints={{innerHTML:''}};
const probeTooltip={{style:{{}},innerHTML:'',offsetWidth:190,offsetHeight:72}};
document.getElementById=id=>id==='endpointProbe'?probeSvg:null;
api.attach({{
  id:'endpointProbe',rows:probeRows,positions:probePositions,plot,
  hit:probeHit,layer:probeLayer,crosshair:probeCross,points:probePoints,
  tooltip:probeTooltip,
  renderPoints:({{sampleX}})=>{{probePoints.innerHTML=`marker:${{sampleX}}`;}},
  renderTooltip:({{row}})=>`timestamp:${{row.ts}}`
}});
probeSvg.onpointerdown({{clientX:wideRect.left,clientY:80}});
const renderedLeft={{
  marker:probePoints.innerHTML,
  tooltip:probeTooltip.innerHTML,
  crosshair:probeCross.attributes.x1
}};
probeSvg.onpointerdown({{clientX:wideRect.left+wideRect.width,clientY:80}});
const renderedRight={{
  marker:probePoints.innerHTML,
  tooltip:probeTooltip.innerHTML,
  crosshair:probeCross.attributes.x1
}};
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
  sweep,marginLeft,marginRight,resized,singleSelections,interactionBounds,
  endpointConfigurations,
  wideConversion:{{
    firstClient,lastClient,oldFirstX,oldLastX,convertedFirst,convertedLast,
    offsetX:wideGeometry.offsetX,scaleX:wideGeometry.scaleX
  }},
  renderedEndpoints:{{
    rootPointerBound:typeof probeSvg.onpointerdown==='function',
    left:renderedLeft,right:renderedRight
  }},
  independent:independentBefore===independentAfter && pmSelection===0,
  downsampledTs:downsampled.map(row=>row.ts),
  mouseTouchParity:JSON.stringify(mouse)===JSON.stringify(touch)
}}));
"""
    )


def preview_chart_data_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_preview_chart_data.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
class Response {{
  constructor(body,options={{}}){{this.body=body;this.status=options.status;this.headers=options.headers;}}
  async json(){{return JSON.parse(this.body);}}
}}
const listeners={{}};
const document={{
  head:{{appendChild:()=>{{}}}},
  documentElement:{{}},
  createElement:()=>({{style:{{}},dataset:{{}},innerHTML:'',textContent:''}}),
  addEventListener:(name,handler)=>{{listeners[name]=handler;}},
  getElementById:()=>null
}};
class MutationObserver {{observe(){{}}}}
const originalRequests=[];
const location={{
  search:'?previewChartData=1',
  href:'http://127.0.0.1:8090/?previewChartData=1'
}};
const window={{
  location,
  fetch:async input=>{{originalRequests.push(input);return new Response('{{}}',{{status:404}});}}
}};
const context={{
  window,location,document,MutationObserver,Response,URL,URLSearchParams,
  Intl,Date,Map,JSON,Number,Promise,console
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const productionFetch=async()=>new Response('{{}}',{{status:200}});
const productionWindow={{
  location:{{search:'',href:'https://dashboard.example/'}},
  fetch:productionFetch
}};
const productionContext={{
  window:productionWindow,location:productionWindow.location,
  URL,URLSearchParams,console
}};
vm.createContext(productionContext);
vm.runInContext(fs.readFileSync({source},'utf8'),productionContext);
(async()=>{{
  const api=window.DashboardPreviewChartData;
  const condo=await window.fetch('/api/condo/history?range=24h').then(value=>value.json());
  const electricity=await window.fetch('/api/electricity/history?bucket=30m').then(value=>value.json());
  process.stdout.write(JSON.stringify({{
    sensorCount:api.sensorRows.length,
    electricityCount:api.electricityPoints.length,
    sensorFirst:api.sensorRows[0],
    sensorLast:api.sensorRows.at(-1),
    electricityFirst:api.electricityPoints[0],
    electricityLast:api.electricityPoints.at(-1),
    sensorNulls:api.sensorRows.slice(1,-1).filter(row=>
      row.temperature===null||row.humidity===null||row.pm25_living_room===null
    ).length,
    electricityNulls:api.electricityPoints.slice(1,-1).filter(row=>row.energy_kwh===null).length,
    uniqueSensorTimestamps:new Set(api.sensorRows.map(row=>row.ts)).size,
    uniqueElectricityTimestamps:new Set(api.electricityPoints.map(row=>row.timestamp)).size,
    condoCount:condo.history.length,
    historyCount:electricity.points.length,
    originalRequestCount:originalRequests.length,
    labels:api.labels,
    productionApiInstalled:Boolean(productionWindow.DashboardPreviewChartData),
    productionFetchUnchanged:productionWindow.fetch===productionFetch
  }}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
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
const widths=[1366,1024,768,390];
const responsive=widths.map(width=>{{
  const current=api.layout(nodes,width);
  const currentRoutes=api.routes(current);
  return {{
    width,
    height:current.height,
    overlaps:api.diagnostics(nodes,current,currentRoutes,[]).filter(item=>item.type==='node_overlap').length,
    missingEdges:api.diagnostics(nodes,current,currentRoutes,[]).filter(item=>item.type==='missing_required_edge').length,
    nodeCount:Object.keys(current.pos).length
  }};
}});
const mixed=api.normalize({{nodes:[
  {{id:'internet',health:'healthy'}},
  {{id:'cloudflare_wan',health:'warning'}},
  {{id:'condo_router',health:'offline'}},
  {{id:'tinkerboard',health:'bad'}}
]}}).nodes;
const mixedMap=new Map(mixed.map(node=>[node.id,node]));
process.stdout.write(JSON.stringify({{
  normalized:normalized.nodes,
  unique:new Set(keys).size===keys.length,
  required:keys,
  deterministic:JSON.stringify(api.layout(nodes,1400))===JSON.stringify(layout),
  diagnosticCount:api.diagnostics(nodes,layout,routes,[]).length,
  responsive,
  summary:api.summary(mixed),
  linkStates:[
    api.linkHealth('internet','internet',mixedMap),
    api.linkHealth('internet','cloudflare_wan',mixedMap),
    api.linkHealth('cloudflare_wan','condo_router',mixedMap),
    api.linkHealth('internet','tinkerboard',mixedMap)
  ],
  statusLabels:mixed.map(api.statusLabel)
  ,midpoint:api.pathMidpoint([{{x:0,y:0}},{{x:0,y:10}},{{x:30,y:10}},{{x:30,y:20}}])
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


def electricity_billing_owner_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_electricity.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const calls=[]; const pending=[]; let timerCreates=0,timerClears=0;
const document={{
  readyState:'loading',body:{{appendChild:()=>{{}}}},
  querySelectorAll:()=>[],querySelector:()=>({{}}),getElementById:()=>null,
  createElement:()=>({{}})
}};
const get=url=>{{
  calls.push(url);
  if(url.startsWith('/api/electricity/billing-cycle')) {{
    return new Promise(resolve=>pending.push(resolve));
  }}
  return Promise.resolve({{}});
}};
const window={{
  safeText:String,get,refresh:async()=>{{}},renderPage:()=>{{}},currentPage:()=> 'overview',
  nav:()=>{{}},setInterval:()=>{{timerCreates++;return timerCreates;}},
  clearInterval:()=>{{timerClears++;}}
}};
const context={{window,document,console,fetch:async()=>({{ok:true,json:async()=>({{}})}}),
  URL,URLSearchParams,Intl,Date,setTimeout,clearTimeout,AbortController,Promise}};
vm.createContext(context); vm.runInContext(fs.readFileSync({source},'utf8'),context);
const owner=window.DashboardElectricityBilling;
(async()=>{{
  const first=owner.activate();
  owner.activate(); owner.refresh();
  const whilePending=calls.filter(url=>url.startsWith('/api/electricity/billing-cycle')).length;
  pending.splice(0).forEach(resolve=>resolve({{}})); await first;
  const firstCycle={{calls:whilePending,diagnostics:owner.diagnostics(),timerCreates,timerClears}};
  owner.deactivate(); owner.refresh();
  const afterLeave={{calls:calls.filter(url=>url.startsWith('/api/electricity/billing-cycle')).length,diagnostics:owner.diagnostics(),timerCreates,timerClears}};
  const second=owner.activate();
  pending.splice(0).forEach(resolve=>resolve({{}})); await second;
  const afterReturn={{calls:calls.filter(url=>url.startsWith('/api/electricity/billing-cycle')).length,diagnostics:owner.diagnostics(),timerCreates,timerClears}};
  process.stdout.write(JSON.stringify({{firstCycle,afterLeave,afterReturn}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )


def electricity_summary_store_behavior():
    source = json.dumps(str(ROOT / "frontend/assets/dashboard_electricity_summary_store.js"))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const events={{requests:0,adds:0,removes:0,timers:0,aborted:false}};
let resolveRequest;
let now=1000;
const DateShim={{now:()=>now}};
const window={{
  get:(url,options)=>{{
    events.requests++;
    options?.signal?.addEventListener('abort',()=>{{events.aborted=true;}});
    return new Promise(resolve=>{{resolveRequest=resolve;}});
  }},
  addEventListener:(name,handler)=>{{if(name==='beforeunload'){{events.adds++;window.unload=handler;}}}},
  removeEventListener:(name,handler)=>{{if(name==='beforeunload'&&window.unload===handler)events.removes++;}}
}};
const context={{
  window,console,Date:DateShim,AbortController,Promise,Set,
  setInterval:()=>{{events.timers++;}},clearInterval:()=>{{}}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({source},'utf8'),context);
const store=window.DashboardElectricitySummaryStore;
const first=store.get();
const duplicate=store.get();
resolveRequest({{today_kwh:1.25}});
Promise.all([first,duplicate]).then(async values=>{{
  const cached=await store.get();
  const requestsBeforeCleanup=events.requests;
  let notifications=0;
  const unsubscribe=store.subscribe(()=>notifications++);
  unsubscribe();
  now+=16000;
  const pending=store.get().catch(()=>null);
  store.dispose();
  resolveRequest({{today_kwh:2.5}});
  await pending;
  process.stdout.write(JSON.stringify({{
    requestsBeforeCleanup,
    requestsTotal:events.requests,
    sameResult:values[0]===values[1]&&values[0]===cached,
    timers:events.timers,
    listenerAdds:events.adds,
    listenerRemoves:events.removes,
    aborted:events.aborted,
    notifications
  }}));
}}).catch(error=>{{console.error(error);process.exit(1);}});
"""
    )
