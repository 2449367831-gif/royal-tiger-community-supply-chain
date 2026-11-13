import numpy as np
import pandas as pd
from scipy.optimize import minimize
from .models import NAMES

def realized(records, T, days=90, horizon=None):
    if records is None or records.empty: return pd.DataFrame()
    T=pd.Timestamp(T)
    d=records[(records.origin<T)&(records.origin>=T-pd.Timedelta(days=days))&(records.target_date<=T)].copy()
    if horizon is not None: d=d[d.horizon==horizon]
    return d[np.isfinite(d.actual)]

def normalize(w, mask=None):
    w=np.maximum(0,np.asarray(w,float)); w[~np.isfinite(w)]=0
    if mask is not None: w=np.where(mask,w,0)
    if w.sum()==0:
        w=np.ones(len(w)) if mask is None else np.asarray(mask,dtype=float)
    return w/w.sum() if w.sum() else w

def optimize(F,y,prior,min_samples=20,reg=.05):
    F=np.asarray(F,float); y=np.asarray(y,float); prior=normalize(prior)
    if len(y)<min_samples: return prior,'prior_insufficient_samples'
    usable=np.isfinite(y)&np.isfinite(F).any(axis=1); F=F[usable]; y=y[usable]
    if len(y)<min_samples: return prior,'prior_insufficient_samples'
    mask=np.isfinite(F); values=np.nan_to_num(F,nan=0); scale=max(float(np.mean(np.abs(y))),1)
    def loss(w):
        den=mask@w
        if (den<=1e-12).any(): return 1e12
        pred=(values@w)/den
        return np.mean(np.abs(y-pred))/scale+reg*np.sum((w-prior)**2)
    result=minimize(loss,prior,method='SLSQP',bounds=[(1e-8,1)]*len(prior),
                    constraints=[{'type':'eq','fun':lambda w:w.sum()-1}],options={'maxiter':150,'ftol':1e-9})
    if not result.success or not np.isfinite(result.x).all(): return prior,'prior_optimizer_failed'
    return normalize(result.x),'optimized'

class WeightBook:
    def __init__(self, config): self.config=config; self.versions={}
    def get(self,sku,h,T,records,development_end):
        c=self.config; key=(sku,h); T=pd.Timestamp(T); old=self.versions.get(key)
        if old and (T-old[0]).days<c.get('update_days',7): return old[1],old[2],old[0]
        pool=realized(records,T,c.get('weight_window_days',90),h)
        prior=np.ones(4)/4
        if not pool.empty:
            dev=pool[pool.target_date<=development_end]
            if len(dev): prior,_=optimize(dev[[n+'_pred' for n in NAMES]],dev.actual,prior,c.get('min_weight_samples',20),c.get('lambda_reg',.05))
            own=pool[pool.sku==sku]
        else: own=pd.DataFrame()
        w,source=(prior,'prior_insufficient_samples') if own.empty else optimize(own[[n+'_pred' for n in NAMES]],own.actual,prior,c.get('min_weight_samples',20),c.get('lambda_reg',.05))
        if old: w=normalize(c.get('smoothing_rho',.7)*old[1]+(1-c.get('smoothing_rho',.7))*w)
        self.versions[key]=(T,w,source)
        return w,source,T

def combine(pred,w):
    pred=np.asarray(pred,float); mask=np.isfinite(pred)
    weights=normalize(w,mask)
    return (float(np.nan_to_num(pred,nan=0)@weights) if mask.any() else np.nan),weights,int(mask.sum())

def interval(records,sku,category,h,T,pred,config):
    if not np.isfinite(pred): return None,None,'unavailable'
    p=realized(records,T,config.get('residual_window_days',90),h)
    if p.empty: return None,None,'unavailable'
    p=p[np.isfinite(p.residual)]
    for name,pool in [('sku',p[p.sku==sku]),('category',p[p.category==category]),('global',p)]:
        if len(pool)>=config.get('min_residual_samples',20):
            a=config.get('alpha',.05); lo,hi=np.quantile(pool.residual,[a/2,1-a/2])
            return float(max(0,pred+lo)),float(max(0,pred+hi)),name
    return None,None,'unavailable'
