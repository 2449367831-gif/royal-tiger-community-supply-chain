"""Strict input validation. Calendar gaps remain NaN; negative net sales require upstream policy."""
import numpy as np
import pandas as pd

def validate(frame):
    d=frame.copy()
    missing=set(['date','sku','sales'])-set(d)
    if missing: raise ValueError(f'Missing columns: {sorted(missing)}')
    d['date']=pd.to_datetime(d.date, format='%Y-%m-%d', errors='raise')
    if d.date.isna().any() or (d.date != d.date.dt.normalize()).any(): raise ValueError('Invalid daily date')
    if d.sku.isna().any() or d.sku.astype(str).str.strip().eq('').any(): raise ValueError('Empty SKU')
    d['sku']=d.sku.astype(str)
    if d.duplicated(['sku','date']).any(): raise ValueError('Duplicate date+sku')
    d['sales']=pd.to_numeric(d.sales, errors='raise')
    if not np.isfinite(d.sales).all(): raise ValueError('Invalid sales: NaN/infinity')
    if (d.sales<0).any(): raise ValueError('Negative sales: define returns policy upstream; not clipped')
    for col in ['stockout','open_flag']:
        if col not in d: d[col]=np.nan
        if not d[col].dropna().isin([0,1]).all(): raise ValueError(f'Invalid {col}')
    if ((d.open_flag==0)&(d.sales!=0)).any(): raise ValueError('Closed day has sales')
    if 'category' not in d: d['category']='unknown'
    # Robust anomaly flag only; no observation removed or modified.
    median=d.groupby('sku').sales.transform('median')
    d['anomaly']=(d.sales>np.maximum(10*median,100))
    if len(d)==0: raise ValueError('Empty sales')
    return d.sort_values(['sku','date']).reset_index(drop=True)

def calendar(frame, end=None):
    out=[]
    for sku,g in frame.groupby('sku'):
        g=g.set_index('date').sort_index()
        g=g.reindex(pd.date_range(g.index.min(),end or g.index.max(),freq='D'))
        g.index.name='date'; g['sku']=sku
        g['missing_record']=g.sales.isna()
        out.append(g.reset_index())
    return pd.concat(out,ignore_index=True)

def quality(d):
    c=calendar(d,end=d.date.max())
    return {'rows':len(d),'skus':d.sku.nunique(),'start':str(d.date.min().date()),'end':str(d.date.max().date()),
            'missing_days':int(c.missing_record.sum()),'zero_sales':int(d.sales.eq(0).sum()),
            'stockout_days':int(d.stockout.eq(1).sum()),'closed_days':int(d.open_flag.eq(0).sum()),
            'unknown_stockout_days':int(d.stockout.isna().sum()),'anomaly_rows':int(d.anomaly.sum()),
            'limitation':'Forecasts realized sales, not unconstrained demand. Missing dates are not zero-filled.'}
