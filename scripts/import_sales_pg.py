"""Read-only import of all warehouse/SKU pairs in today's captured product catalog."""
import sys,json,argparse
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.query_daily_sales_web import required,mapping_sql
from scripts.daily_sales_cities import policy
import psycopg2
p=argparse.ArgumentParser();p.add_argument('--max-series',type=int,default=None,help='Optional diagnostic cap; default ALL current catalog pairs');args=p.parse_args()
if args.max_series is not None and args.max_series<1:raise ValueError('max-series must be positive')
today=datetime.now(timezone(timedelta(hours=8))).date();rules=policy();groups={e['platform_warehouse']:e['group_name'] for e in rules['entries']}
query='''WITH warehouse_groups AS (SELECT key AS original_warehouse,value AS warehouse_name FROM jsonb_each_text(%(groups)s::jsonb)),
'''+mapping_sql()+''', active_latest AS MATERIALIZED (
 SELECT DISTINCT ON(account_name,warehouse_name,product_id) account_name,warehouse_name,product_id,product_name
 FROM public.pdd_order_today WHERE sale_date=%(end)s AND captured_date=%(end)s
 ORDER BY account_name,warehouse_name,product_id,estimated_updated_at DESC NULLS LAST,id DESC
), catalog AS MATERIALIZED (
 SELECT g.warehouse_name AS warehouse_name,
 COALESCE(m.sku_code,'未映射:'||a.product_id) AS sku_code,MAX(COALESCE(m.sku_name,a.product_name)) AS product_name,
 BOOL_AND(m.sku_code IS NOT NULL AND g.warehouse_name IS NOT NULL) AS mapping_valid,
 STRING_AGG(DISTINCT a.product_id,',') AS platform_product_ids
 FROM active_latest a JOIN warehouse_groups g ON g.original_warehouse=a.warehouse_name LEFT JOIN mapping m ON m.product_id=a.product_id
 GROUP BY 1,2
), source_latest AS MATERIALIZED (
 SELECT DISTINCT ON (s.account_name,s.warehouse_name,s.product_id,s.sale_date)
 s.account_name,s.warehouse_name,s.product_id,s.sale_date,s.sell_unit_total,s.error_message
 FROM public.pdd_sales_data s WHERE s.sale_date>=%(start)s AND s.sale_date<%(end)s
 ORDER BY s.account_name,s.warehouse_name,s.product_id,s.sale_date,s.updated_at DESC NULLS LAST,s.id DESC
), daily AS MATERIALIZED (
 SELECT g.warehouse_name,m.sku_code,s.sale_date AS date,SUM(s.sell_unit_total*m.ratio) AS sales
 FROM source_latest s JOIN mapping m ON m.product_id=s.product_id JOIN warehouse_groups g ON g.original_warehouse=s.warehouse_name
 JOIN catalog c ON c.warehouse_name=g.warehouse_name AND c.sku_code=m.sku_code
 WHERE COALESCE(s.error_message,'')='' AND s.sell_unit_total IS NOT NULL
 GROUP BY g.warehouse_name,m.sku_code,s.sale_date
)
SELECT c.*,d.date,d.sales FROM catalog c LEFT JOIN daily d USING(warehouse_name,sku_code)
ORDER BY c.warehouse_name,c.sku_code,d.date LIMIT 500001'''
c=psycopg2.connect(host=required('PGHOST'),port=required('PGPORT'),dbname=required('PGDATABASE'),user=required('PGUSER'),password=required('PGPASSWORD'),connect_timeout=10,options='-c timezone=Asia/Shanghai -c statement_timeout=60000');c.set_session(readonly=True,isolation_level='REPEATABLE READ')
try:
 with c.cursor() as q:
  q.execute('SET LOCAL enable_nestloop=off')
  q.execute(query,{'groups':json.dumps(groups,ensure_ascii=False),'start':today-timedelta(days=180),'end':today})
  rows=q.fetchall();cols=[x[0] for x in q.description]
finally:c.close()
if len(rows)>=500001:raise ValueError('Safety row cap exceeded; no partial import published')
d=pd.DataFrame(rows,columns=cols)
if d.empty:raise ValueError('Today product capture missing; cannot declare all on-sale SKUs')
clouds={e['group_name']:(e['cloud_code'],e['cloud_name']) for e in rules['entries']}
d['cloud_warehouse_code']=d.warehouse_name.map(lambda v:clouds[v][0])
d['cloud_warehouse_name']=d.warehouse_name.map(lambda v:clouds[v][1])
d['sku']=d.warehouse_name+'::'+d.sku_code
if args.max_series:d=d[d.sku.isin(sorted(d.sku.unique())[:args.max_series])].copy()
catalog=d[['sku','warehouse_name','cloud_warehouse_code','cloud_warehouse_name','sku_code','product_name','mapping_valid','platform_product_ids']].drop_duplicates('sku').copy()
hist=d[d.date.notna()].copy();hist['sales']=hist.sales.astype(float)
stats=hist.groupby('sku').agg(observed_days=('date','count'),last_sales_date=('date','max'),min_sales=('sales','min'))
catalog=catalog.merge(stats,on='sku',how='left');catalog['observed_days']=catalog.observed_days.fillna(0).astype(int)
catalog['forecast_status']=np.select([~catalog.mapping_valid,catalog.min_sales.lt(0),catalog.observed_days.lt(90),catalog.last_sales_date.ne(today-timedelta(days=1))],['mapping_required','invalid_sales','insufficient_history','missing_latest_day'],default='eligible')
root=(Path(__file__).resolve().parents[1]/'sales_forecasting');dest=root/'data/raw';dest.mkdir(parents=True,exist_ok=True)
hist['data_source']='pdd_sales_data_current_mapping';hist['category']='unknown';hist.to_csv(dest/'sales_daily_import.csv',index=False,encoding='utf-8-sig')
hist=hist[~hist.sku.isin(catalog.loc[catalog.forecast_status.eq('invalid_sales'),'sku'])];hist.to_csv(dest/'sales_daily.csv',index=False,encoding='utf-8-sig');catalog.to_csv(dest/'active_catalog.csv',index=False,encoding='utf-8-sig')
report={'ok':True,'imported_rows':len(hist),'series':len(catalog),'eligible_series':int(catalog.forecast_status.eq('eligible').sum()),'statuses':catalog.forecast_status.value_counts().to_dict(),'start':str(hist.date.min()),'end':str(hist.date.max()),'catalog_date':str(today),'window_days':180,'minimum_observed_days':90,'max_series':args.max_series,
 'selection':'ALL today captured APPROVED CLOUD warehouse/internal-SKU pairs; unmapped warehouses excluded; zero sales and unmapped SKUs retained',
 'mapping':'Existing approved cloud mapping and independent community_plan_sku_mapping; read only',
 'limitations':['On-sale catalog means products captured today; sales_status is statistics state, not a verified master on-sale flag','Reconstructed using current mappings; historical mapping versions unavailable','Latest source corrections used, not point-in-time archived captures','Missing records not zero-filled; stockout/open flags unknown','Partial platform warehouse capture within one cloud-day cannot be proven complete','Procurement blocked until confirmed arrival/lead time/MOQ/pack parameters supplied']}
(root/'artifacts/production').mkdir(parents=True,exist_ok=True);(root/'artifacts/production/import_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
