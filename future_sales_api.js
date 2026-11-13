const fs = require('fs');
const path = require('path');
const ROOT = process.env.FUTURE_SALES_ROOT || path.join(__dirname, 'sales_forecasting');
function queryFutureSales(request, response, sendJson) {
  const search = new URL(request.url, 'http://localhost').searchParams;
  if ([...search.keys()].some(k => k !== 'mode') || search.getAll('mode').length > 1 || !['production', 'demo'].includes(search.get('mode') || 'production')) {
    sendJson(response, 400, {ok:false,error:'无效的预测查询参数。'}); return;
  }
  const mode = search.get('mode') || 'production';
  const file = path.join(ROOT, 'artifacts', mode, 'latest.json');
  fs.readFile(file, 'utf8', async (error, text) => {
    if (error?.code === 'ENOENT') { sendJson(response, 200, {ok:true,status:'awaiting_data',synthetic:mode==='demo',rows:[],evaluation:[],procurement:[],message:mode==='demo'?'演示回测正在准备，请稍后重新读取。':'待接入历史日销量并完成回测；初始需要90天训练数据和后续验证数据。'}); return; }
    if (error) {sendJson(response,503,{ok:false,error:'预测结果暂不可用。'});return;}
    try {
      const data=JSON.parse(text);
      if (!data.ok || !Array.isArray(data.rows) || Boolean(data.synthetic)!==(mode==='demo')) throw new Error('invalid');
      const {artifacts, ...payload}=data;
      if(payload.status==='ready') {
        const yesterday=new Date(Date.now()+8*3600000-86400000).toISOString().slice(0,10);
        payload.forecast_freshness=String(payload.training_cutoff).slice(0,10)===yesterday?'current':'stale';
      }
      const {attachInventory}=require('./future_sales_inventory');
      let stock=null;
      if(mode==='production') {
        try {stock=await require('./daily_sales_api').getSalesSnapshot({},false);} catch { /* Forecast remains available if stock read fails. */ }
      }
      sendJson(response,200,attachInventory(payload,stock));
    } catch {sendJson(response,503,{ok:false,error:'预测结果校验失败，请检查本批次输出。'});}
  });
}
module.exports={queryFutureSales};
