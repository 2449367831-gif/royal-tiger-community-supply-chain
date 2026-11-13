import numpy as np
import pandas as pd

def features(series, origin, horizon, sku_index):
    origin=pd.Timestamp(origin)
    s=series.loc[:origin]
    target=origin+pd.Timedelta(days=horizon)
    row=[sku_index,horizon,target.weekday(),target.month,target.day,int(target.weekday()>4)]
    for lag in [1,7,14,28]: row.append(s.get(origin-pd.Timedelta(days=lag-1),np.nan))
    for n in [7,14,28]: row.append(s.tail(n).mean())
    for n in [7,28]: row.append(s.tail(n).std())
    return row

def global_samples(series_by_sku, origin, horizon):
    # Vectorized equivalent of features(): the feature row is indexed by origin,
    # and labels are admitted only when their target date has been realized.
    origin=pd.Timestamp(origin); blocks=[]; targets=[]; label_dates=[]
    for i,(sku,series) in enumerate(sorted(series_by_sku.items())):
        s=series.loc[:origin].astype(float)
        if len(s)<=28: continue
        f=pd.DataFrame(index=s.index)
        f['lag1']=s; f['lag7']=s.shift(6); f['lag14']=s.shift(13); f['lag28']=s.shift(27)
        for n in [7,14,28]: f[f'mean{n}']=s.rolling(n,min_periods=1).mean()
        for n in [7,28]: f[f'std{n}']=s.rolling(n,min_periods=2).std()
        values=f.iloc[28:].to_numpy(); times=s.index[28:]
        for h in range(1,horizon+1):
            dates=times+pd.Timedelta(days=h); y=s.reindex(dates).to_numpy();mask=(dates<=origin)&np.isfinite(y)
            if not mask.any(): continue
            t=dates[mask]; count=int(mask.sum())
            head=np.column_stack([np.full(count,i),np.full(count,h),t.dayofweek,t.month,t.day,(t.dayofweek>4).astype(int)])
            blocks.append(np.column_stack([head,values[mask]]));targets.append(y[mask]);label_dates.extend(t)
    return (np.concatenate(blocks) if blocks else np.empty((0,15)),np.concatenate(targets) if targets else np.array([]),label_dates)
