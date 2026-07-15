(() => {
  'use strict';
  if (window.__dashboardChartInteractionInstalled) return;
  window.__dashboardChartInteractionInstalled = true;

  const originalDrawChart = window.drawChart;
  const originalChartReset = window.chartReset;
  const CHART_IDS = new Set(['overviewChart','overviewPmChart','airChart']);
  const state = new Map();
  const DEBUG = window.DASHBOARD_CHART_DEBUG === true;
  const numeric = value => { const number=Number(value); return Number.isFinite(number)?number:null; };
  const visibleRowsFor = (id,rows) => typeof window.visibleRows==='function' ? window.visibleRows(id,rows) : (rows||[]);
  const samplePositions = (count,left,right) => count<=1 ? [(left+right)/2] : Array.from({length:count},(_,index)=>left+index/(count-1)*(right-left));

  function selectSampleIndex(pointerX,positions) {
    if (!positions.length) return -1;
    if (positions.length===1) return 0;
    const firstMid=(positions[0]+positions[1])/2,last=positions.length-1,lastMid=(positions[last-1]+positions[last])/2;
    if(pointerX<=firstMid)return 0;
    if(pointerX>=lastMid)return last;
    let low=0,high=last;
    while(low<high){const mid=Math.floor((low+high)/2);if(positions[mid]<pointerX)low=mid+1;else high=mid;}
    const rightIndex=low,leftIndex=Math.max(0,rightIndex-1);
    return pointerX-positions[leftIndex] <= positions[rightIndex]-pointerX ? leftIndex : rightIndex;
  }

  function clientToSvg(svg,clientX,clientY){const matrix=svg.getScreenCTM();if(!matrix)return null;const point=svg.createSVGPoint();point.x=clientX;point.y=clientY;return point.matrixTransform(matrix.inverse());}
  function svgToClient(svg,x,y){const matrix=svg.getScreenCTM();if(!matrix)return null;const point=svg.createSVGPoint();point.x=x;point.y=y;return point.matrixTransform(matrix);}

  function hide(id){const svg=document.getElementById(id),layer=svg?.querySelector('.hover-layer'),tooltip=svg?.parentElement?.querySelector('.tooltip');if(layer)layer.style.display='none';if(tooltip)tooltip.style.display='none';state.delete(id);svg?.parentElement?.querySelector('.chart-debug-geometry')?.remove();}

  function install(id,rows,series){
    if(!CHART_IDS.has(id))return;
    const svg=document.getElementById(id),wrap=svg?.parentElement;if(!svg||!wrap)return;
    const visible=visibleRowsFor(id,rows),valid=(visible||[]).filter(row=>series.some(item=>numeric(row[item.key])!==null));
    const hit=svg.querySelector('.hit'),layer=svg.querySelector('.hover-layer'),line=layer?.querySelector('.crosshair'),points=layer?.querySelector('.points'),tooltip=wrap.querySelector('.tooltip');
    if(!valid.length||!hit||!layer||!line||!points||!tooltip)return;
    const viewBox=svg.viewBox.baseVal,width=viewBox.width||900,height=viewBox.height||310,plot={left:48,right:width-18,top:18,bottom:height-35};
    const positions=samplePositions(valid.length,plot.left,plot.right),values=[];series.forEach(item=>valid.forEach(row=>{const value=numeric(row[item.key]);if(value!==null)values.push(value);}));
    let min=Math.min(...values),max=Math.max(...values);if(min===max){min-=1;max+=1;}const extra=(max-min)*.12;min-=extra;max+=extra;const yFor=value=>plot.top+(max-value)/(max-min)*(plot.bottom-plot.top);
    line.setAttribute('y1',plot.top);line.setAttribute('y2',plot.bottom);hit.setAttribute('x',plot.left);hit.setAttribute('y',plot.top);hit.setAttribute('width',plot.right-plot.left);hit.setAttribute('height',plot.bottom-plot.top);hit.style.pointerEvents='all';hit.style.fill='transparent';

    const move=event=>{
      const pointer=event.touches?.[0]||event,svgPoint=clientToSvg(svg,pointer.clientX,pointer.clientY);if(!svgPoint)return;
      if(svgPoint.x<plot.left||svgPoint.x>plot.right||svgPoint.y<plot.top||svgPoint.y>plot.bottom)return;
      const pointerX=Math.max(plot.left,Math.min(plot.right,svgPoint.x)),index=selectSampleIndex(pointerX,positions);if(index<0)return;
      const row=valid[index],sampleX=positions[index];line.setAttribute('x1',sampleX);line.setAttribute('x2',sampleX);points.innerHTML='';
      series.forEach(item=>{const value=numeric(row[item.key]);if(value!==null)points.insertAdjacentHTML('beforeend',`<circle class="point" cx="${sampleX}" cy="${yFor(value)}" r="6" fill="${item.color}"/>`);});layer.style.display='block';
      tooltip.innerHTML=`<strong>${new Date(Number(row.ts)*1000).toLocaleString()}</strong><br>${series.map(item=>{const value=numeric(row[item.key]);return `${item.label}: ${value===null?'Not available':value.toFixed(1)}${item.unit}`;}).join('<br>')}`;tooltip.style.display='block';
      const wrapRect=wrap.getBoundingClientRect(),anchor=svgToClient(svg,sampleX,(plot.top+plot.bottom)/2);if(anchor){const localX=anchor.x-wrapRect.left,localY=pointer.clientY-wrapRect.top,tipW=tooltip.offsetWidth||180,tipH=tooltip.offsetHeight||70,gap=12,pad=8;let left=localX+gap;if(left+tipW>wrapRect.width-pad)left=localX-tipW-gap;left=Math.max(pad,Math.min(wrapRect.width-tipW-pad,left));let top=localY-tipH/2;top=Math.max(pad,Math.min(wrapRect.height-tipH-pad,top));tooltip.style.left=`${left}px`;tooltip.style.top=`${top}px`;}
      state.set(id,{index,pointerX,sampleX,count:valid.length});
      if(DEBUG){let debug=wrap.querySelector('.chart-debug-geometry');if(!debug){debug=document.createElement('div');debug.className='chart-debug-geometry';wrap.appendChild(debug);}debug.textContent=`plot ${plot.left.toFixed(1)}-${plot.right.toFixed(1)} · pointer ${pointerX.toFixed(1)} · index ${index} · sample ${sampleX.toFixed(1)} · visible ${valid.length}`;}
    };
    hit.onmousemove=move;hit.onpointermove=move;hit.onpointerenter=move;hit.ontouchstart=event=>{event.preventDefault();move(event);};hit.ontouchmove=event=>{event.preventDefault();move(event);};hit.onmouseleave=()=>hide(id);hit.onpointerleave=()=>hide(id);hit.ontouchend=()=>hide(id);hit.ontouchcancel=()=>hide(id);
  }

  if(typeof originalDrawChart==='function')window.drawChart=function sharedChartDraw(id,rows,series){originalDrawChart(id,rows,series);try{install(id,rows,series);}catch(error){console.error('Chart interaction diagnostics',{name:error?.name||'Error',message:error?.message||'interaction setup failed'});}};
  window.chartReset=function sharedChartReset(id){if(typeof originalChartReset==='function')originalChartReset(id);hide(id);};
  window.DashboardChartInteraction={selectSampleIndex,samplePositions,visibleRowsFor};
})();
