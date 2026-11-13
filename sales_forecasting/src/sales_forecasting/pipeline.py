import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yaml
from .data import validate, calendar, quality
from .models import fit_predict, NAMES
from .ensemble import WeightBook, combine, interval
from .metrics import evaluate
from .procurement import recommend

LOG=logging.getLogger(__name__)

def json_safe(value):
    if isinstance(value,dict): return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_safe(v) for v in value]
    if isinstance(value,(pd.Timestamp,datetime)): return value.isoformat()
    if isinstance(value,(np.integer,)): return int(value)
    if isinstance(value,(float,np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NA: return None
    return value

def save_json(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(json_safe(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8'); os.replace(tmp,p)

def load_config(path):
    path=Path(path).resolve(); c=yaml.safe_load(path.read_text(encoding='utf-8')); c['_root']=str(path.parent.parent)
    return c

def load_sales(c):
    path=Path(c['_root'])/c['data']['sales_path']
    d=validate(pd.read_csv(path,dtype={'sku':str}))
    today=pd.Timestamp(datetime.now(ZoneInfo('Asia/Shanghai')).date())
    if (d.date>=today).any(): raise ValueError('Sales must end before today; current day is not finalized')
    return d

def context(d,c):
    end=d.date.max(); series={sku:g.set_index('date').sales for sku,g in calendar(d,end).groupby('sku')}
    return end,series

def generate(d,c,origins,run_id,development_end,base=None,ens=None,book=None):
    end,series=context(d,c); base=[] if base is None else list(base); ens=[] if ens is None else list(ens)
    book=book or WeightBook(c['ensemble']); horizon=c['forecast']['horizon_days']
    for T in origins:
        LOG.info('Predict origin %s',T.date())
        history={k:s.loc[:T] for k,s in series.items() if s.loc[:T].notna().sum()>=c['forecast']['min_train_days'] and pd.notna(s.get(T))}
        if not history: continue
        preds,status=fit_predict(history,T,horizon,{**c.get('models',{}),'min_train_days':c['forecast']['min_train_days']})
        bdf=pd.DataFrame(base); edf=pd.DataFrame(ens)
        for sku in history:
            known=d[(d.sku==sku)&(d.date<=T)].iloc[-1]; category=str(known.category)
            for h in range(1,horizon+1):
                target=T+pd.Timedelta(days=h)
                raw={'sku':sku,'origin':T,'target_date':target,'horizon':h,'actual':series[sku].get(target,np.nan),
                     'category':category,'model_status':json.dumps(status[sku]),'created_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                     'model_version':run_id,'feature_version':'origin-only-calendar-lags-v1','training_cutoff':T}
                values=[preds[sku][n][h-1] for n in NAMES]
                raw.update({n+'_pred':v for n,v in zip(NAMES,values)})
                w,source,version=book.get(sku,h,T,bdf,development_end)
                point,actual_weights,available=combine(values,w)
                lo,hi,isource=interval(edf,sku,category,h,T,point,c['interval'])
                baseline=history[sku].get(target-pd.Timedelta(days=7),np.nan)
                equal,_,_=combine(values,np.ones(4)/4)
                extra={'ensemble_pred':point,'residual':raw['actual']-point,'weights_json':json.dumps(dict(zip(NAMES,actual_weights.tolist()))),
                       'weight_source':source,'weight_version':version,'available_models':available,'lower':lo,'upper':hi,'interval_source':isource,
                       'baseline_pred':baseline,'equal_pred':equal,'product_name':str(known.get('product_name',sku)),
                       'warehouse_name':str(known.get('warehouse_name','全部仓库'))}
                base.append(raw); ens.append({**raw,**extra})
    return pd.DataFrame(base),pd.DataFrame(ens),book

def run(c,command='run-all'):
    root=Path(c['_root']); output=root/c['output']['directory']; output.mkdir(parents=True,exist_ok=True)
    try: d=load_sales(c)
    except Exception as e:
        save_json(output/'quality.json',{'status':'invalid','error':str(e)}); raise
    q=quality(d); save_json(output/'quality.json',q)
    if command=='validate': return q
    run_id=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%f')
    folder=output/'runs'/run_id; folder.mkdir(parents=True)
    h=c['forecast']['horizon_days']; min_days=c['forecast']['min_train_days']; end=d.date.max()
    if not 1<=h<=7: raise ValueError('V1 horizon must be 1..7')
    if c['forecast'].get('origin_step_days',7)<1: raise ValueError('Invalid origin step')
    dev_end=end-pd.Timedelta(days=c['forecast'].get('holdout_days',28))
    start=d.date.min()+pd.Timedelta(days=min_days-1)
    origins=pd.date_range(start,end-pd.Timedelta(days=h),freq=f"{c['forecast'].get('origin_step_days',7)}D")
    if not len(origins): raise ValueError('Insufficient history for backtest; at least min_train_days + horizon required')
    # Every origin is re-fitted using only then-realized labels. Config fixed before holdout.
    base,ens,book=generate(d,c,origins,run_id,dev_end)
    if ens.empty: raise ValueError('No eligible SKU origins')
    test=ens[(ens.target_date>dev_end)&(ens.origin>=dev_end)].copy()
    report=evaluate(test) if not test.empty else pd.DataFrame()
    development=evaluate(ens[ens.target_date<=dev_end])
    _,future,_=generate(d,c,[end],run_id,dev_end,base.to_dict('records'),ens.to_dict('records'),book)
    future=future[future.origin==end].copy()
    inv_path=root/c['data']['inventory_path']; inbound_path=root/c['data']['inbound_path']
    inv=pd.read_csv(inv_path,dtype={'sku':str}) if inv_path.exists() else pd.DataFrame(columns=['sku'])
    inbound=pd.read_csv(inbound_path,dtype={'sku':str}) if inbound_path.exists() else pd.DataFrame(columns=['sku'])
    assessed=not report.empty and bool(((report.model=='ensemble')&(report.n>0)).any())
    orders=recommend(future,inv,inbound,end,evaluated=assessed)
    for name,frame in [('base_oof_predictions',base),('ensemble_oof_predictions',ens),('forecasts',future),('evaluation',report),('development_evaluation',development),('procurement',orders)]:
        frame.to_csv(folder/(name+'.csv'),index=False,encoding='utf-8-sig')
    # Immutable run directory + atomic latest pointer prevents partial web reads.
    weights=ens[['sku','origin','horizon','weights_json','weight_source','weight_version']].drop_duplicates()
    weights.to_csv(folder/'weights.csv',index=False,encoding='utf-8-sig')
    save_json(folder/'config.json',{k:v for k,v in c.items() if not k.startswith('_')})
    manifest={'run_id':run_id,'model_version':run_id,'training_cutoff':end,'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'development_end':dev_end,'holdout_policy':'Fixed configuration; online weights may use already realized test observations. No holdout tuning.',
              'data_sha256':hashlib.sha256((root/c['data']['sales_path']).read_bytes()).hexdigest(),'quality':q,
              'training_min_days':min_days,'scope':'Selected warehouse-SKU series; not full catalog',
              'synthetic':bool('data_source' in d and d.data_source.eq('synthetic').all()),'artifacts':str(folder),
              'limitations':['Realized sales, not unconstrained demand','No future price/promotion used','Fixed lambda; no hyperparameter search',
                              'Sparse residuals may make intervals unavailable; 95% coverage is empirical, not guaranteed',
                              'Models refit per origin; parameters and input hash saved, fitted objects not serialized']}
    import_report=output/'import_report.json'
    if import_report.exists():
        manifest['source_import']=json.loads(import_report.read_text(encoding='utf-8'))
        manifest['limitations']+=manifest['source_import'].get('limitations',[])
    save_json(folder/'manifest.json',manifest)
    payload={**manifest,'ok':True,'rows':future.to_dict('records'),'procurement':orders.to_dict('records'),
             'evaluation':report.to_dict('records'),'status':'ready','row_count':len(future)}
    save_json(output/'latest.json',payload)
    LOG.info('Completed %s; %s forecasts',run_id,len(future))
    return manifest
