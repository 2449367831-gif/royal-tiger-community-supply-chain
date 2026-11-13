import warnings
import numpy as np
from .features import features, global_samples

NAMES=['sarimax','holt_winters','lightgbm','tsb']

def tsb(y,h,alpha=.2,beta=.2):
    first=y[np.isfinite(y)][0]
    p=float(first>0); z=float(y[y>0][0]) if (y>0).any() else 0.
    for v in y[1:]:
        if not np.isfinite(v): continue  # Unknown day: no zero-demand update.
        p=beta*(v>0)+(1-beta)*p
        if v>0: z=alpha*v+(1-alpha)*z
    return np.repeat(p*z,h)

def fit_predict(series_by_sku, origin, h, config):
    results={}; status={}; lgb=None; lgb_error=None
    try:
        import lightgbm as lgbm
        X,y,labels=global_samples(series_by_sku,origin,h)
        if len(y)<20: raise ValueError('insufficient global training labels')
        lgb=lgbm.train({'num_leaves':15,'max_depth':5,'learning_rate':.03,'seed':42,'num_threads':2,'verbosity':-1},
                       lgbm.Dataset(X,label=y,categorical_feature=[0]),num_boost_round=config.get('lightgbm_estimators',100))
    except Exception as e: lgb_error=f'{type(e).__name__}: {e}'
    for i,(sku,series) in enumerate(sorted(series_by_sku.items())):
        s=series.loc[:origin]; y=s.to_numpy(dtype=float)
        results[sku]={}; status[sku]={}
        for name in NAMES:
            try:
                if np.isfinite(y).sum()<config.get('min_train_days',180): raise ValueError('insufficient history')
                # Do not compress missing calendar days into adjacent days.
                if not np.isfinite(y).all() and name=='holt_winters': raise ValueError('calendar gaps: model unavailable')
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    if name=='tsb': pred=tsb(y,h)
                    elif name=='sarimax':
                        from statsmodels.tsa.statespace.sarimax import SARIMAX
                        fitted=SARIMAX(y,order=(1,0,0),seasonal_order=(1,0,0,7),trend='c',
                                       enforce_stationarity=False,enforce_invertibility=False).fit(disp=False,maxiter=config.get('sarimax_maxiter',60))
                        if not fitted.mle_retvals.get('converged',True): raise ValueError('optimizer did not converge')
                        pred=fitted.forecast(h)
                    elif name=='holt_winters':
                        from statsmodels.tsa.holtwinters import ExponentialSmoothing
                        pred=ExponentialSmoothing(y,trend='add',damped_trend=True,seasonal='add',seasonal_periods=7).fit().forecast(h)
                    else:
                        if lgb is None: raise ValueError(lgb_error)
                        pred=lgb.predict(np.array([features(s,origin,k,i) for k in range(1,h+1)]),num_threads=2)
                pred=np.asarray(pred,dtype=float)
                if len(pred)!=h or not np.isfinite(pred).all(): raise ValueError('invalid model output')
                results[sku][name]=np.maximum(0,pred)
                status[sku][name]={'state':'ok','warnings':list(dict.fromkeys(str(w.message) for w in caught))[:3]}
            except Exception as e:
                results[sku][name]=np.full(h,np.nan)
                status[sku][name]={'state':'failed','reason':f'{type(e).__name__}: {e}'}
    return results,status
