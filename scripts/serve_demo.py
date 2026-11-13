"""Standalone fictional preview. No database imports, queries or credentials."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timedelta,timezone
import argparse,json
ROOT=Path(__file__).resolve().parents[1]
DAY=datetime.now(timezone(timedelta(hours=8))).date()
STAMP=f'{DAY}T09:30:00+08:00'
products=['示例香烤鸡腿 220g','示例经典烤肠 500g','示例葱香手抓饼 1kg','示例芝士披萨 200g','示例酥皮蛋挞 360g','示例鲜蔬虾饼 160g']
stocks=[120,900,680,320,560,450];estimates=[180,350,110,90,140,75];refs=[80,95,70,48,65,38]
def fixtures():
    inventory=[];sales=[];future=[]
    for i,name in enumerate(products):
        warehouse=['示例华东云仓','示例华南云仓','示例华中云仓'][i//2];code=f'DEMO-WH-{i//2+1:02}';sku=f'DEMO-SKU-{i+1:03}'
        inventory.append({'仓库编码':code,'仓库名称':warehouse,'SKU编码':sku,'SKU名称':name,'当日库存':stocks[i],
            '期间出库数量':240,'期间退货数量':10,'净数量':230,'统计天数':7,'每日消耗':33,'预计天数':14,'实时库存':stocks[i]+20,'库存变动时间':f'{DAY} 09:00:00','待出库':20,'待入库':0})
        state='warning' if estimates[i]>stocks[i] else 'tight' if estimates[i]>refs[i]*3 else 'normal'
        sales.append(dict(warehouse_name=warehouse,warehouse_id=code,product_id=sku,sku_code=sku,product_name=name,
            estimated_sales=estimates[i],estimated_updated_at=STAMP,reference_sales=refs[i],valid_days=7,reference_kind='average',
            cloud_warehouse_code=code,cloud_warehouse_codes=[code],cloud_warehouse_name=warehouse,cloud_warehouse_names=[warehouse],
            source_warehouses=warehouse,platform_warehouse_count=1,rolling_stock=stocks[i]-30,transit_stock=30,cloud_stock=stocks[i],
            alert_state=state,alert_missing=[],alert_reasons=[]))
        total=sum(30+i*10+h*3 for h in range(7))
        for h in range(7):
            future.append(dict(sku=warehouse+'::'+sku,sku_code=sku,warehouse_name=warehouse,cloud_warehouse_code=code,cloud_warehouse_name=warehouse,product_name=name,
                origin=str(DAY-timedelta(days=1)),target_date=str(DAY+timedelta(days=h)),horizon=h+1,ensemble_pred=30+i*10+h*3,lower=None,upper=None,
                available_models=4,model_status=json.dumps({k:{'state':'ok','warnings':[]} for k in ['sarimax','holt_winters','lightgbm','tsb']}),
                weights_json=json.dumps({k:0.25 for k in ['sarimax','holt_winters','lightgbm','tsb']}),weight_source='demo',weight_version='DEMO',
                model_version='DEMO',observed_days=120,forecast_status='ready',forecast_reason='',backtest_status='demo_only',
                inventory=dict(status='current',rolling_stock=stocks[i]-30,transit_stock=30,cloud_stock=stocks[i],seven_day_stock_gap=max(0,total-stocks[i]))))
    common=dict(ok=True,queried_at=STAMP,snapshot=dict(id='0'*64,persisted=True,updating=False,last_error=None))
    return {
        'inventory':dict(common,rows=inventory,row_count=6,parameters=dict(sku_codes=[''],warehouse_codes=[''],start_date='',end_date=''),stock_date=str(DAY),stock_basis='dashboard_snapshot',stock_note='虚构示例数据 · 不连接业务数据库'),
        'daily-sales':dict(common,rows=sales,row_count=6,date=str(DAY),today=str(DAY),latest_date=str(DAY),capture_date=str(DAY),forecast_freshness='today',
            start_date=str(DAY-timedelta(days=7)),end_date=str(DAY-timedelta(days=1)),unmapped=[],sales_queried_at=STAMP,inventory_queried_at=STAMP,
            refresh_policy=dict(inventory_hours=[9,13,17,21],sales_daily_at='09:30'),alerts=dict(stock_basis='rolling-adjusted-with-transit-v1',unmapped_warehouses=[],stock_date=str(DAY),stock_status='available',transit_status='available'),note='虚构示例数据，不用于经营决策'),
        'future-sales':dict(ok=True,synthetic=True,status='ready',run_id='DEMO-FICTIONAL',training_cutoff=str(DAY-timedelta(days=1)),training_min_days=90,
            rows=future,row_count=len(future),catalog_series=6,forecast_series=6,unavailable_series=0,forecast_freshness='current',procurement=[],evaluation=[],
            evaluation_scope=dict(series=0),inventory=dict(status='current',source='虚构示例库存',stock_date=str(DAY),stock_queried_at=STAMP,transit_queried_at=STAMP,total_series=6,matched_series=6)),
        'movements':dict(common,rows=[dict(time=f'{DAY} 08:00:00',direction='out',source_kind='transfer',source_label='示例调拨出库',signed_quantity=-60,before_stock=180,after_stock=120,reference='DEMO-OUT-0002'),
            dict(time=f'{DAY-timedelta(days=1)} 16:00:00',direction='in',source_kind='inbound',source_label='示例仓库入库',signed_quantity=100,before_stock=80,after_stock=180,reference='DEMO-IN-0001')],
            current_stock=120,opening_stock=80,total_in=100,total_out=60,start_date=str(DAY-timedelta(days=7)),end_date=str(DAY),note='虚构示例流水 · 结存倒推展示，不代表真实业务')}
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_GET(self):
        path=urlparse(self.path).path
        if path.startswith('/api/'):
            key='movements' if path=='/api/inventory/movements' else path.rsplit('/',1)[-1]
            data=fixtures().get(key,{'ok':False,'error':'Demo endpoint unavailable'})
            raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
        if path in ['/','/index.html']:
            text=(ROOT/'index.html').read_text(encoding='utf-8').replace('</body>','<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#332a25;color:#ffdc73;text-align:center;padding:7px;font:14px sans-serif">公开演示 · 仓库、商品、库存、销量均为虚构示例 · 不连接真实数据库</div></body>')
            raw=text.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw);return
        if path.startswith(('/.','/config/','/scripts/','/sql/','/sales_forecasting/')):self.send_error(404);return
        super().do_GET()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8892);args=parser.parse_args()
    print(f'Fictional preview http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
