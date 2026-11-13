(() => {
'use strict';
const el=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
const pct=v=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2})+'%';
const names={sarimax:'SARIMAX',holt_winters:'Holt-Winters',lightgbm:'LightGBM',tsb:'TSB',baseline:'上周同日',equal:'等权组合',ensemble:'动态组合'};
let data=null,filtered=[],page=1,version=0,chartChoices=[];
const chartSkus=new Set(),owners=window.dailySalesOwners;
const cloudName=r=>r.cloud_warehouse_name||r.inventory?.cloud_warehouse_name||r.warehouse_name;
const ownerName=r=>owners.forCloud(cloudName(r));
el('futureOwner').innerHTML='<option value="">全部负责人</option>'+owners.names.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
const groups=rows=>{const map=new Map();for(const r of rows){if(!map.has(r.sku))map.set(r.sku,[]);map.get(r.sku).push(r)}return [...map.values()]};
const sum=rows=>rows.some(r=>r.ensemble_pred==null)?null:rows.reduce((a,r)=>a+r.ensemble_pred,0);
function render(){
 const rows=data?.rows||[],text=el('futureSku').value.trim().toLowerCase(),owner=el('futureOwner').value,day=el('futureDay').value;
 const ownerRows=rows.filter(r=>!owner||ownerName(r)===owner),warehouseRows=new Map(ownerRows.map(r=>[r.warehouse_name,r])),previousWarehouse=el('futureWarehouse').value;
 el('futureWarehouse').innerHTML='<option value="">全部云仓</option>'+[...warehouseRows.keys()].sort((a,b)=>a.localeCompare(b,'zh-CN')).map(v=>`<option value="${esc(v)}">${esc(cloudName(warehouseRows.get(v)))}</option>`).join('');
 el('futureWarehouse').value=warehouseRows.has(previousWarehouse)?previousWarehouse:'';
 const warehouse=el('futureWarehouse').value;
 const selected=ownerRows.filter(r=>(!warehouse||r.warehouse_name===warehouse)&&(!text||`${r.sku} ${r.product_name}`.toLowerCase().includes(text)));
 const allGroups=groups(selected).sort((a,b)=>{
  const gapA=a[0].inventory?.seven_day_stock_gap,gapB=b[0].inventory?.seven_day_stock_gap;
  const knownA=Number.isFinite(gapA),knownB=Number.isFinite(gapB);
  return Number(knownB)-Number(knownA)||(knownA&&knownB?gapB-gapA:0)||a[0].warehouse_name.localeCompare(b[0].warehouse_name,'zh-CN')||a[0].sku.localeCompare(b[0].sku);
 });
 filtered=allGroups.flat().filter(r=>!day||r.target_date.slice(0,10)===day);
 const valid=selected.filter(r=>r.ensemble_pred!=null);el('futureSkuCount').textContent=num(allGroups.length);
 el('futureTotal').innerHTML=valid.length?num(valid.reduce((a,r)=>a+r.ensemble_pred,0))+(valid.length<selected.length?'<small>（部分）</small>':''):'—';
 const metric=data?.evaluation?.find(r=>r.level==='overall'&&r.model==='ensemble');el('futureWmape').textContent=pct(metric?.wmape);el('futureCoverage').textContent=pct(metric?.coverage==null?null:metric.coverage*100);
 const stocks=allGroups.map(g=>g[0].inventory),known=stocks.filter(s=>s?.cloud_stock!=null),gaps=stocks.filter(s=>s?.seven_day_stock_gap!=null);
 el('futureStockTotal').innerHTML=known.length?num(known.reduce((a,s)=>a+s.cloud_stock,0))+(known.length<stocks.length?'<small>（部分）</small>':''):'—';
 el('futureStockGap').innerHTML=gaps.length?num(gaps.reduce((a,s)=>a+s.seven_day_stock_gap,0))+(gaps.length<stocks.length?'<small>（部分）</small>':''):'—';
 const days=[...new Set(rows.map(r=>r.target_date.slice(0,10)))].sort().filter(d=>!day||d===day);
 el('futureHead').innerHTML='<tr><th>云仓</th><th>商品 / SKU</th><th class="numeric">云仓库存<small>滚动＋在途</small></th>'+days.map(d=>`<th class="numeric">${esc(d.slice(5))}<small>预测数量</small></th>`).join('')+'<th class="numeric">7日合计</th><th class="numeric" aria-sort="descending">7日静态缺口 ↓<small>含采购＋调拨在途</small></th><th>预测状态</th><th>走势</th></tr>';
 page=Math.min(page,Math.max(1,Math.ceil(allGroups.length/25)));
 el('futureBody').innerHTML=allGroups.slice((page-1)*25,page*25).map(g=>{
  const r=g[0],stock=r.inventory,ready=g.every(v=>v.ensemble_pred!=null),byDate=new Map(g.map(v=>[v.target_date.slice(0,10),v]));let weights={};try{weights=JSON.parse(r.weights_json)}catch{}
  const reason=r.forecast_reason||({insufficient_history:'有效历史不足90天',missing_latest_day:'缺少昨日销量',model_failed:'模型暂不可用',mapping_required:'仓库或SKU映射待补充'}[r.forecast_status]||'暂无预测');
  return `<tr><td>${esc(cloudName(r))}<small>${esc(r.cloud_warehouse_code||r.inventory?.cloud_warehouse_code||'')}</small><small>负责人：${esc(ownerName(r))}</small></td><td class="future-product">${esc(r.product_name)}<small>${esc(r.sku_code||r.sku.split('::').pop())}</small></td><td class="numeric">${num(stock?.cloud_stock)}<small>${stock?.rolling_stock!=null?'滚动 '+num(stock.rolling_stock)+' ＋ 在途 '+num(stock.transit_stock):'库存未匹配'}</small>${['stale','retained'].includes(stock?.status)?'<small class="future-stock-warning">历史库存快照</small>':''}</td>${days.map(d=>`<td class="numeric future-quantity">${num(byDate.get(d)?.ensemble_pred)}</td>`).join('')}<td class="numeric future-quantity">${num(sum(g))}</td><td class="numeric ${stock?.seven_day_stock_gap>0?'future-gap-positive':''}">${num(stock?.seven_day_stock_gap)}</td><td class="future-state">${ready?'预测可用':esc(reason)}<small>${r.observed_days!=null?'有效历史 '+num(r.observed_days)+' 天':''}</small>${ready?`<small>${r.backtest_status==='evaluated'?'已纳入原回测样本':'待独立回测 · 先验权重'}</small><details><summary>${r.available_models} 个可用模型</summary>${Object.entries(weights).filter(([,w])=>w>0).map(([m,w])=>`${names[m]} ${pct(w*100)}`).join('<br>')}<small>${r.lower==null?'预测区间暂不可用':'区间详见走势和导出'}</small></details>`:''}</td><td><button type="button" class="future-chart-button" data-sku="${esc(r.sku)}">查看走势</button></td></tr>`;
 }).join('')||`<tr><td colspan="${days.length+7}" class="inventory-empty">没有匹配的仓库 / SKU</td></tr>`;
 el('futureStatus').textContent=`${data?.synthetic?'演示数据 · ':''}${allGroups.length} 个仓库 / SKU · 每个组合一行 · 数量保留0位小数`;
 el('futurePageLabel').textContent=allGroups.length?`${(page-1)*25+1}–${Math.min(page*25,allGroups.length)} / ${allGroups.length}`:'0 / 0';
 el('futurePrev').disabled=page<=1;el('futureNext').disabled=page*25>=allGroups.length;el('futureExport').disabled=!filtered.length;
 chartChoices=allGroups.map(g=>g[0]);const visibleSkus=new Set(chartChoices.map(r=>r.sku));
 for(const sku of chartSkus)if(!visibleSkus.has(sku))chartSkus.delete(sku);
 renderChoices();chart();
 if(location.hash==='#future-sales'){document.querySelector('.sync-label').textContent=data?.run_id?'预测结果已读取':'预测待准备';document.querySelector('.sidebar-footer').textContent=data?.run_id?`预测截止：${data.training_cutoff.slice(0,10)}`:'暂无正式预测'}
}
function renderChoices(){
 const search=el('futureChartSearch').value.trim().toLowerCase();
 const options=chartChoices.filter(r=>!search||`${cloudName(r)} ${r.sku} ${r.product_name}`.toLowerCase().includes(search));
 el('futureChartSku').innerHTML=options.map(r=>`<label class="future-picker-option"><input type="checkbox" value="${esc(r.sku)}" ${chartSkus.has(r.sku)?'checked':''}><span>${esc(r.product_name)}<small>${esc(cloudName(r))} / ${esc(r.sku_code||r.sku)}</small></span></label>`).join('')||'<p class="future-picker-empty">没有匹配的走势商品</p>';
}
function chart(){
 const bySku=new Map(groups((data?.rows||[]).filter(r=>chartSkus.has(r.sku))).map(g=>[g[0].sku,g.sort((a,b)=>a.horizon-b.horizon)]));
 const colors=['#a67c00','#a96635','#ad5968','#1a827f','#6679a3','#748345','#8b6595','#86513a'];
 const picked=[...chartSkus].map((sku,i)=>({sku,rows:bySku.get(sku),color:colors[i]||`hsl(${Math.round(i*137.508)%360} 65% 36%)`})).filter(s=>s.rows?.length);
 for(const item of picked)item.ready=item.rows.length===7&&item.rows.every(r=>r.ensemble_pred!=null);
 const series=picked.filter(s=>s.ready);
 el('futureChartSummary').textContent=picked.length?`已选择 ${picked.length} 个云仓 / SKU`:'请选择云仓 / SKU 查看走势';
 el('futureChartSelectionCount').textContent=`已选 ${picked.length} 项`;el('futureChartClear').disabled=!picked.length;
 for(const input of el('futureChartSku').querySelectorAll('input'))input.checked=chartSkus.has(input.value);
 for(const button of el('futureBody').querySelectorAll('[data-sku]')){const on=chartSkus.has(button.dataset.sku);button.textContent=on?'移出走势':'加入走势';button.setAttribute('aria-pressed',String(on))}
 el('futureChartLegend').innerHTML=picked.map(s=>{const r=s.rows[0];return `<div class="future-legend-item"><i style="background:${s.color}" aria-hidden="true"></i><span>${esc(cloudName(r))} · ${esc(r.product_name)}<small>${esc(r.sku_code||r.sku)}${s.ready?'':' · '+esc(r.forecast_reason||'暂无可用预测')}</small></span><button type="button" data-remove-sku="${esc(s.sku)}" aria-label="移除走势 ${esc(r.warehouse_name+' '+(r.sku_code||r.sku))}">×</button></div>`}).join('');
 el('futureChartHint').textContent=picked.length?`已选 ${picked.length} 项 · ${series.length} 条可用曲线${picked.length>series.length?' · 其余商品数据不足，未绘制曲线':''}`:'可多选云仓 / SKU 对比走势，数量保留0位小数';
 if(!series.length){el('futureChart').textContent=picked.length?'所选商品暂不可预测，请查看上方原因':'请选择云仓 / SKU 查看走势';return}
 const height=Math.max(280,Math.min(600,series.length*20+120)),single=picked.length===1;
 const max=Math.max(1,...series.flatMap(s=>s.rows.map(r=>Math.max(r.ensemble_pred,single?r.upper||0:0))));
 const days=series[0].rows.map(r=>r.target_date.slice(0,10)),x=i=>75+i*770/6,y=v=>height-50-v/max*(height-100);
 const positions=series.map(s=>s.rows.map(r=>y(r.ensemble_pred)-12));
 for(let i=0;i<7;i++){
  const labels=series.map((s,j)=>({j,y:positions[j][i]})).sort((a,b)=>a.y-b.y);
  for(let j=1;j<labels.length;j++)labels[j].y=Math.max(labels[j].y,labels[j-1].y+16);
  if(labels.at(-1).y>height-62){labels.at(-1).y=height-62;for(let j=labels.length-2;j>=0;j--)labels[j].y=Math.min(labels[j].y,labels[j+1].y-16)}
  for(const label of labels)positions[label.j][i]=label.y;
 }
 const r=series[0].rows,band=single&&r.every(v=>v.lower!=null)?`<polygon points="${r.map((v,i)=>`${x(i)},${y(v.lower)}`).join(' ')} ${r.map((v,i)=>`${x(i)},${y(v.upper)}`).reverse().join(' ')}" fill="#fff1c6"/>`:'';
 el('futureChart').innerHTML=`<svg viewBox="0 0 920 ${height}" role="img" aria-label="${series.length}个云仓SKU未来七天预测数量对比"><title>未来七天预测数量：${series.map(s=>esc(s.sku)).join('、')}</title>${[0,.5,1].map(f=>`<line x1="75" y1="${y(f*max)}" x2="845" y2="${y(f*max)}" stroke="#eaecf0"/><text x="63" y="${y(f*max)+4}" text-anchor="end" font-size="12" fill="#667085">${num(f*max)}</text>`).join('')}${band}${series.map((s,j)=>`<g data-series="${esc(s.sku)}"><polyline points="${s.rows.map((r,i)=>`${x(i)},${y(r.ensemble_pred)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5"/>${s.rows.map((r,i)=>`<circle cx="${x(i)}" cy="${y(r.ensemble_pred)}" r="4" fill="white" stroke="${s.color}" stroke-width="2.2"><title>${esc(s.sku)} ${r.target_date.slice(0,10)}：${num(r.ensemble_pred)}</title></circle><text class="future-point-label" x="${x(i)}" y="${positions[j][i]}" text-anchor="middle" font-size="13" font-weight="650" fill="${s.color}" stroke="white" stroke-width="3" paint-order="stroke">${num(r.ensemble_pred)}</text>`).join('')}</g>`).join('')}${days.map((d,i)=>`<text x="${x(i)}" y="${height-18}" text-anchor="middle" font-size="12" fill="#667085">${d.slice(5)}</text>`).join('')}</svg>`;
}
async function load(){
 const n=++version;el('futureReload').disabled=true;el('futureError').hidden=true;
 try{
  const response=await fetch(`/api/future-sales?mode=${el('futureSource').value}`,{cache:'no-store'}),payload=await response.json();if(n!==version)return;if(!response.ok||!payload.ok)throw new Error(payload.error||'预测结果读取失败');data=payload;page=1;chartSkus.clear();el('futureChartSearch').value='';
  el('futureDay').innerHTML='<option value="">全部 7 天</option>'+[...new Set((data.rows||[]).map(r=>r.target_date.slice(0,10)))].sort().map(v=>`<option>${esc(v)}</option>`).join('');
  el('futureAudit').textContent=data.run_id?`批次 ${data.run_id}；原回测范围 ${data.evaluation_scope?.series||10} 个仓库 / SKU，指标独立于当前筛选；新增组合未独立回测，区间暂不可用。`:'暂无预测批次。';
  const metrics=(data.evaluation||[]).filter(r=>r.level==='overall');el('futureEvaluation').innerHTML=metrics.length?'<table><thead><tr><th>原样本留出期模型</th><th>有效样本</th><th>WMAPE</th><th>MAE</th><th>Bias</th><th>FVA</th></tr></thead><tbody>'+metrics.map(r=>`<tr><td>${esc(names[r.model])}</td><td>${num(r.n)}</td><td>${pct(r.wmape)}</td><td>${num(r.mae)}</td><td>${pct(r.bias)}</td><td>${pct(r.fva_percent)}</td></tr>`).join('')+'</tbody></table>':'';
  render();
 }catch(error){if(n!==version)return;data=null;chartSkus.clear();render();el('futureError').textContent=error.message;el('futureError').hidden=false}
 finally{if(n===version)el('futureReload').disabled=false}
}
function route(){const active=location.hash==='#future-sales';el('futureSales').hidden=!active;if(active&&!data)load()}
el('futureReload').addEventListener('click',load);el('futureSource').addEventListener('change',load);el('futureSku').addEventListener('input',()=>{page=1;render()});for(const id of ['futureWarehouse','futureDay','futureOwner'])el(id).addEventListener('change',()=>{page=1;render()});
el('futureChartSearch').addEventListener('input',renderChoices);
el('futureChartSku').addEventListener('change',e=>{const input=e.target;if(input.type!=='checkbox')return;if(input.checked)chartSkus.add(input.value);else chartSkus.delete(input.value);chart()});
el('futureChartClear').addEventListener('click',()=>{chartSkus.clear();chart()});
el('futureChartLegend').addEventListener('click',e=>{const button=e.target.closest('[data-remove-sku]');if(button){chartSkus.delete(button.dataset.removeSku);chart()}});
document.addEventListener('click',e=>{if(!el('futureChartPicker').contains(e.target))el('futureChartPicker').open=false});
el('futureChartPicker').addEventListener('keydown',e=>{if(e.key==='Escape'){el('futureChartPicker').open=false;el('futureChartSummary').focus()}});
el('futureBody').addEventListener('click',e=>{const button=e.target.closest('[data-sku]');if(button){const sku=button.dataset.sku;if(chartSkus.has(sku))chartSkus.delete(sku);else chartSkus.add(sku);chart();el('futureChart').scrollIntoView({behavior:'smooth',block:'center'})}});
el('futureFilters').addEventListener('submit',e=>e.preventDefault());el('futureFilters').addEventListener('reset',e=>{e.preventDefault();for(const id of ['futureSku','futureWarehouse','futureDay','futureOwner','futureChartSearch'])el(id).value='';chartSkus.clear();page=1;render()});el('futurePrev').addEventListener('click',()=>{page--;render()});el('futureNext').addEventListener('click',()=>{page++;render()});
el('futureExport').addEventListener('click',()=>{const cell=v=>'"'+String(v??'').replace(/^[=+\-@\t\r]/,"'$&").replaceAll('"','""')+'"',qty=v=>v==null?'':Math.round(v);const records=[['负责人','仓库','SKU','商品','预测日期','预估销量','区间下界','区间上界','云仓库存含在途','7日静态缺口','预测状态','有效历史天数','回测状态'],...filtered.map(r=>[ownerName(r),r.warehouse_name,r.sku_code||r.sku,r.product_name,r.target_date.slice(0,10),qty(r.ensemble_pred),qty(r.lower),qty(r.upper),qty(r.inventory?.cloud_stock),qty(r.inventory?.seven_day_stock_gap),r.forecast_reason||r.forecast_status,r.observed_days,r.backtest_status])];const url=URL.createObjectURL(new Blob(['\ufeff'+records.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`未来销量预估_${data.synthetic?'演示_':''}${data.training_cutoff.slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
window.addEventListener('hashchange',route);route();
})();


