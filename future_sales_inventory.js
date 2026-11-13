// Match the same approved cloud group and internal SKU; never infer absent inventory as zero.
const finite = v => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v)) ? Number(v) : null;
const chinaToday = () => new Date(Date.now()+8*3600000).toISOString().slice(0,10);
function attachInventory(payload, snapshot, today=chinaToday()) {
  if (payload.synthetic) return {...payload, inventory: {status:'demo',message:'演示数据未关联正式库存。'}};
  const alerts=snapshot?.alerts || {};
  const validSource=snapshot?.ok && Array.isArray(snapshot.rows) && alerts.stock_basis==='rolling-adjusted-with-transit-v1';
  const fresh=validSource && alerts.stock_date===today;
  const overall=!validSource?'unavailable':!fresh?'stale':snapshot.snapshot?.last_error?'retained':'current';
  const groups=new Map();
  if (validSource) for(const row of snapshot.rows) {
    const key=JSON.stringify([row.warehouse_name,row.sku_code]);
    if(groups.has(key)) groups.set(key,null); // Ambiguous groups must not silently take the first row.
    else groups.set(key,row);
  }
  const demand=new Map();
  for(const row of payload.rows || []) {
    const item=demand.get(row.sku) || {total:0,n:0,days:new Set(),valid:true};
    const value=finite(row.ensemble_pred);item.valid&&=value!==null;
    if(value!==null) item.total+=value;
    item.n++;item.days.add(String(row.target_date).slice(0,10));demand.set(row.sku,item);
  }
  const series=new Map();
  const rows=(payload.rows||[]).map(row=>{
    if(!series.has(row.sku)) {
      const prefix=String(row.warehouse_name)+'::';
      const code=String(row.sku).startsWith(prefix)?String(row.sku).slice(prefix.length):null;
      const stock=code?groups.get(JSON.stringify([row.warehouse_name,code])):null;
      const mapped=Boolean(stock?.cloud_warehouse_code);
      const rolling=mapped && alerts.stock_status==='available'?finite(stock.rolling_stock):null;
      const transit=mapped && alerts.transit_status==='available'?finite(stock.transit_stock):null;
      const total=rolling!==null && transit!==null?rolling+transit:null;
      const d=demand.get(row.sku);
      const fullForecast=d.valid && d.n===7 && d.days.size===7 && String(payload.training_cutoff).slice(0,10)===new Date(Date.parse(today+'T00:00:00Z')-86400000).toISOString().slice(0,10);
      const status=!validSource?'unavailable':!mapped?'unmatched':!fresh?'stale':rolling===null?'unavailable':snapshot.snapshot?.last_error?'retained':transit===null?'partial':'current';
      series.set(row.sku,{status,sku_code:code,cloud_warehouse_code:stock?.cloud_warehouse_code||null,
        cloud_warehouse_name:stock?.cloud_warehouse_name||null,rolling_stock:rolling,transit_stock:transit,cloud_stock:total,
        realtime_stock:mapped?finite(stock.realtime_stock):null,pending_out:mapped?finite(stock.pending_out):null,pending_in:mapped?finite(stock.pending_in):null,
        purchase_transit:mapped && alerts.transit_status==='available'?finite(stock.purchase_transit):null,
        transfer_transit:mapped && alerts.transit_status==='available'?finite(stock.transfer_transit):null,
        seven_day_stock_gap:fullForecast && status==='current'?Math.max(0,d.total-total):null,
        stock_date:alerts.stock_date||null,stock_queried_at:alerts.stock_queried_at||null,transit_queried_at:alerts.transit_queried_at||null});
    }
    return {...row,inventory:series.get(row.sku)};
  });
  const procurement=(payload.procurement||[]).map(order=>({...order,inventory_connected:['current','partial','retained'].includes(series.get(order.sku)?.status)}));
  return {...payload,rows,procurement,inventory:{status:overall,source:'每日预估销量 · 同一云仓库存快照',
    stock_date:alerts.stock_date||null,stock_queried_at:alerts.stock_queried_at||null,transit_queried_at:alerts.transit_queried_at||null,
    snapshot_id:snapshot?.snapshot?.id||null,total_series:series.size,matched_series:[...series.values()].filter(v=>v.rolling_stock!==null).length,
    basis:'滚动库存＝实物库存－待出库＋待入库；云仓库存＝滚动库存＋采购在途＋调拨在途。7日静态缺口＝max（0，完整7日预测合计－云仓库存），计入当前全部在途，不按到货日期拆分，不等同采购量。',
    warning:overall==='stale'?'库存快照不是今日数据，保留数量供核验，不计算7日缺口。':overall==='retained'?'库存最近更新失败，展示上次成功快照，不计算7日缺口。':overall==='unavailable'?'库存快照暂不可用，缺失保持为空。':null}};
}
module.exports={attachInventory};
