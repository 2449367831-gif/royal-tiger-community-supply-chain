import argparse
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np
import pandas as pd
from .pipeline import run, load_config, json_safe

def make_demo(root):
    path=Path(root)/'data/demo';path.mkdir(parents=True,exist_ok=True);rng=np.random.default_rng(42)
    dates=pd.date_range(end=pd.Timestamp(datetime.now(ZoneInfo('Asia/Shanghai')).date())-pd.Timedelta(days=1),periods=365);rows=[]
    for i in range(10):
        for n,day in enumerate(dates):
            mean=(15+i*3)*(1+.3*np.sin(2*np.pi*n/7))
            v=rng.poisson(mean) if i%3 else (rng.poisson(mean) if rng.random()<.25 else 0)
            rows.append({'date':str(day.date()),'sku':f'DEMO-{i+1:03}','sales':v,'stockout':0,'open_flag':1,
                         'category':f'demo-{i%2}','product_name':f'演示商品 {i+1:02}','warehouse_name':'演示云仓','data_source':'synthetic'})
    pd.DataFrame(rows).to_csv(path/'sales_daily.csv',index=False)
    pd.DataFrame([{'sku':f'DEMO-{i+1:03}','on_hand':100,'allocated':20,'safety_stock':30,'lead_time_days':3,
                   'review_period_days':2,'moq':24,'pack_size':12} for i in range(10)]).to_csv(path/'inventory.csv',index=False)
    pd.DataFrame([{'sku':'DEMO-001','quantity':24,'expected_arrival_date':str((dates[-1]+pd.Timedelta(days=2)).date()),'status':'confirmed'}]).to_csv(path/'inbound_shipments.csv',index=False)
    (path/'SYNTHETIC_ONLY.txt').write_text('Synthetic pipeline validation only. Not real business sales.',encoding='utf-8')

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['validate','make-demo-data','backtest','evaluate','forecast','procurement','run-all','forecast-active']);p.add_argument('--config',default='configs/default.yaml')
    args=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    c=load_config(args.config)
    if args.command=='make-demo-data': make_demo(c['_root']);return
    if args.command=='forecast-active':
        from .active import run_active
        print(json.dumps(json_safe(run_active(c)),ensure_ascii=False));return
    # Atomic audited full run ensures standalone commands never consume stale OOF data.
    result=run(c,args.command);print(json.dumps(json_safe(result),ensure_ascii=False))
if __name__=='__main__': main()
