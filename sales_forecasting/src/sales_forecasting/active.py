"""Forecast every current warehouse/SKU; retain ineligible products with explicit status.
Existing audited OOF is reused, without claiming it evaluates newly added series.
"""
import hashlib,json,logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from .pipeline import load_sales,context,save_json
from .data import quality
from .models import NAMES,fit_predict
from .ensemble import WeightBook,combine,interval
from .metrics import evaluate

LOG=logging.getLogger(__name__)

def run_active(c):
    root=Path(c['_root']);out=root/c['output']['directory'];old=json.loads((out/'latest.json').read_text(encoding='utf-8'))
    old_folder=Path(old.get('oof_artifacts',old['artifacts']));d=load_sales(c);end,all_series=context(d,c)
    catalog=pd.read_csv(root/'data/raw/active_catalog.csv',dtype={'sku':str,'sku_code':str,'cloud_warehouse_code':str}).fillna({'product_name':'未命名商品'})
    if catalog.cloud_warehouse_code.isna().any() or catalog.cloud_warehouse_name.isna().any():
        raise ValueError('Cloud-only catalog requires approved cloud warehouse code and name')
    eligible=set(catalog.loc[catalog.forecast_status.eq('eligible'),'sku']);history={k:s for k,s in all_series.items() if k in eligible}
    LOG.info('Active catalog %s; eligible %s; generating all current forecasts',len(catalog),len(history))
    preds,status=fit_predict(history,end,7,{**c.get('models',{}),'min_train_days':c['forecast']['min_train_days']})
    base=pd.read_csv(old_folder/'base_oof_predictions.csv',dtype={'sku':str},parse_dates=['origin','target_date'])
    ens=pd.read_csv(old_folder/'ensemble_oof_predictions.csv',dtype={'sku':str},parse_dates=['origin','target_date'])
    dev_end=pd.Timestamp(old['development_end']);book=WeightBook(c['ensemble']);version=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%f')
    prior_cache={};outrows=[];evaluated=set(ens.loc[ens.origin.ge(dev_end),'sku']);own_oof=set(base.sku)
    for r in catalog.to_dict('records'):
        sku=r['sku']; state=r['forecast_status']; reason={'insufficient_history':'有效历史不足90天','missing_latest_day':'缺少昨日销量','mapping_required':'仓库或SKU映射待补充','invalid_sales':'销量异常待核验'}.get(state,'')
        has_prediction=sku in preds
        for h in range(1,8):
            values=[preds[sku][n][h-1] for n in NAMES] if has_prediction else [np.nan]*4
            if sku in own_oof: w,source,wversion=book.get(sku,h,end,base,dev_end)
            else:
                if h not in prior_cache: prior_cache[h]=book.get('__new_series_prior__',h,end,base,dev_end)
                w,_,wversion=prior_cache[h];source='prior_no_sku_oof'
            point,weights,n=combine(values,w)
            lo,hi,isource=interval(ens,sku,'unknown',h,end,point,c['interval']) if sku in own_oof else (None,None,'unavailable')
            available=np.isfinite(point)
            outrows.append({'sku':sku,'sku_code':r['sku_code'],'warehouse_name':r['warehouse_name'],'cloud_warehouse_code':r['cloud_warehouse_code'],'cloud_warehouse_name':r['cloud_warehouse_name'],'product_name':r['product_name'],
              'origin':end,'target_date':end+pd.Timedelta(days=h),'horizon':h,'ensemble_pred':point,'lower':lo,'upper':hi,'interval_source':isource,
              'available_models':n,'model_status':json.dumps(status.get(sku,{})),'weights_json':json.dumps(dict(zip(NAMES,weights.tolist()))),
              'weight_source':source,'weight_version':wversion,'model_version':version,'category':'unknown','observed_days':int(r['observed_days']),
              'forecast_status':'ready' if available else ('model_failed' if state=='eligible' else state),'forecast_reason':reason if not available else '',
              'backtest_status':'evaluated' if sku in evaluated else 'not_evaluated_for_this_series'})
    future=pd.DataFrame(outrows);valid=future[future.ensemble_pred.notna()];q=quality(d)
    folder=out/'runs'/version;folder.mkdir(parents=True)
    future.to_csv(folder/'forecasts.csv',index=False,encoding='utf-8-sig');catalog.to_csv(folder/'active_catalog.csv',index=False,encoding='utf-8-sig')
    report=evaluate(ens[(ens.target_date>dev_end)&ens.origin.ge(dev_end)])
    report.to_csv(folder/'evaluation.csv',index=False,encoding='utf-8-sig')
    procurement=[{'sku':sku,'status':'MISSING_INVENTORY_PARAMETERS' if sku in evaluated else 'BACKTEST_REQUIRED','order_quantity':None} for sku in catalog.sku]
    pd.DataFrame(procurement).to_csv(folder/'procurement.csv',index=False,encoding='utf-8-sig')
    # Link to immutable original audit records rather than copying/relabelling prior OOF.
    manifest={'run_id':version,'model_version':version,'training_cutoff':end,'training_min_days':c['forecast']['min_train_days'],
      'development_end':dev_end,'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'synthetic':False,'status':'ready',
      'scope':'All current captured APPROVED CLOUD warehouse/SKU pairs','cloud_only':True,'catalog_series':len(catalog),'forecast_series':valid.sku.nunique(),
      'unavailable_series':len(catalog)-valid.sku.nunique(),'quality':q,'source_import':json.loads((out/'import_report.json').read_text(encoding='utf-8')),
      'evaluation_scope':{'series':len(evaluated),'current_catalog_evaluated_series':len(evaluated&set(catalog.sku)),'catalog_series':len(catalog),'description':'仅原已回测样本，新增仓库SKU尚无独立回测；新SKU使用历史先验，区间暂不可用'},
      'oof_artifacts':str(old_folder),'artifacts':str(folder),'data_sha256':hashlib.sha256((root/c['data']['sales_path']).read_bytes()).hexdigest(),
      'limitations':old.get('limitations',[])+['All current captured listings shown, zero observed sales included','New series forecasts use OOF priors; no independent backtest or calibrated interval claimed for new series']}
    save_json(folder/'manifest.json',manifest);save_json(folder/'config.json',{k:v for k,v in c.items() if not k.startswith('_')})
    save_json(out/'latest.json',{**manifest,'ok':True,'rows':future.to_dict('records'),'procurement':procurement,'evaluation':report.to_dict('records'),'row_count':len(future)})
    LOG.info('Completed ALL: %s catalog pairs, %s forecastable, %s explicitly unavailable',len(catalog),valid.sku.nunique(),len(catalog)-valid.sku.nunique())
    return manifest

