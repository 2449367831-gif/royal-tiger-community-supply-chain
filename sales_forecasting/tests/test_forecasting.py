import json
import numpy as np
import pandas as pd
import pytest
from sales_forecasting.data import validate,calendar
from sales_forecasting.features import features,global_samples
from sales_forecasting.ensemble import optimize,normalize,combine,realized,interval,WeightBook
from sales_forecasting.procurement import recommend
from sales_forecasting.metrics import score
from sales_forecasting.models import tsb,fit_predict

def sales(): return pd.DataFrame({'date':['2025-01-01','2025-01-03'],'sku':['001','001'],'sales':[0,3]})

def test_duplicate():
    with pytest.raises(ValueError,match='Duplicate'): validate(pd.concat([sales(),sales()]))
def test_calendar_preserves_missing_and_zero():
    d=calendar(validate(sales())); assert d.sales.iloc[0]==0 and pd.isna(d.sales.iloc[1]); assert d.missing_record.sum()==1
@pytest.mark.parametrize('value',[np.nan,np.inf,-1])
def test_invalid_sales(value):
    s=sales();s['sales']=s.sales.astype(float);s.loc[0,'sales']=value
    with pytest.raises(ValueError): validate(s)
def test_features_no_future():
    s=pd.Series(np.arange(90.),index=pd.date_range('2025-01-01',periods=90));T=s.index[50];a=features(s,T,7,0)
    s.loc[s.index>T]=100000
    assert features(s,T,7,0)==a
    X,y,labels=global_samples({'a':s},T,7);assert max(labels)<=T and max(y)<=50

def test_vectorized_global_features_match_calendar_reference():
    s=pd.Series(np.arange(105.)%17,index=pd.date_range('2025-01-01',periods=105))
    s.iloc[[30,41,60]]=np.nan
    origin=s.index[90];X,y,labels=global_samples({'b':s,'a':s*2},origin,7)
    expected=[];actual=[];dates=[]
    for i,(_,series) in enumerate(sorted({'b':s,'a':s*2}.items())):
        for h in range(1,8):
            for t in series.loc[:origin].index[28:]:
                target=t+pd.Timedelta(days=h)
                if target<=origin and pd.notna(series.get(target)):
                    expected.append(features(series,t,h,i));actual.append(series[target]);dates.append(target)
    np.testing.assert_allclose(X,expected,equal_nan=True,atol=1e-10)
    np.testing.assert_allclose(y,actual)
    assert labels==dates and max(labels)<=origin

def test_price_promotion_not_used():
    a=validate(sales().assign(price=[1,2],promotion=[0,0]));b=validate(sales().assign(price=[99999,99999],promotion=[1,1]))
    np.testing.assert_allclose(features(a.set_index('date').sales,a.date.iloc[0],1,0),features(b.set_index('date').sales,b.date.iloc[0],1,0),equal_nan=True)

def test_weight_constraints_and_missing():
    rng=np.random.default_rng(4);F=rng.uniform(0,50,(30,4));F[::2,1]=np.nan;y=F[:,0]*.8
    w,status=optimize(F,y,[.25]*4);assert (w>=0).all() and abs(w.sum()-1)<1e-6
    point,aw,n=combine([np.nan,10,np.nan,20],w);assert n==2 and aw[0]==aw[2]==0 and 10<=point<=20
    assert abs(aw.sum()-1)<1e-6

def test_weight_small_and_extreme():
    prior=np.array([.1,.2,.3,.4]);w,_=optimize(np.ones((2,4)),[1,2],prior);assert np.allclose(prior,w)
    w,_=optimize(np.full((30,4),1e100),np.full(30,1e100),prior);assert np.isfinite(w).all() and abs(w.sum()-1)<1e-6
    assert normalize([-2,np.nan,1,1]).sum()==1

def records():
    T=pd.Timestamp('2025-05-01'); return pd.DataFrame([{'sku':'a','category':'c','origin':T-pd.Timedelta(days=k+1),
        'target_date':T-pd.Timedelta(days=k),'horizon':1,'actual':10.,'residual':-20.,
        **{n+'_pred':float(k%3+2) for n in ['sarimax','holt_winters','lightgbm','tsb']}} for k in range(30)])

def test_realized_window_and_future_residuals():
    d=records();T=pd.Timestamp('2025-05-01');f=d.iloc[:1].copy();f['target_date']=T+pd.Timedelta(days=1);f['residual']=1e10
    old=d.iloc[:1].copy();old['origin']=T-pd.Timedelta(days=91)
    assert len(realized(pd.concat([d,f,old]),T))==30
    lo,hi,source=interval(pd.concat([d,f]),'a','c',1,T,5,{'min_residual_samples':20});assert lo==hi==0 and source=='sku'
    assert interval(d,'b','c',1,T,5,{'min_residual_samples':20})[2]=='category'
    assert interval(d,'b','d',1,T,5,{'min_residual_samples':20})[2]=='global'
    assert interval(d.iloc[:2],'a','c',1,T,5,{})[2]=='unavailable'

def test_weekly_smoothing():
    book=WeightBook({'update_days':7,'smoothing_rho':.7});T=pd.Timestamp('2025-05-01');d=records()
    w,_,v=book.get('a',1,T,d,T);w2,_,v2=book.get('a',1,T+pd.Timedelta(days=1),d,T)
    assert v==v2 and np.allclose(w,w2)
    w3,_,_=book.get('a',1,T+pd.Timedelta(days=7),d,T);assert abs(w3.sum()-1)<1e-6 and (w3>=0).all()

def inputs(on_hand=0,lead=3,review=2):
    T=pd.Timestamp('2025-05-01');f=pd.DataFrame({'sku':['a']*7,'target_date':pd.date_range(T+pd.Timedelta(days=1),periods=7),'ensemble_pred':[10.]*7})
    inv=pd.DataFrame([{'sku':'a','on_hand':on_hand,'allocated':10,'safety_stock':0,'lead_time_days':lead,'review_period_days':review,'moq':25,'pack_size':12}])
    ships=pd.DataFrame([{'sku':'a','quantity':20,'expected_arrival_date':'2025-05-03','status':'confirmed'},
                         {'sku':'a','quantity':1000,'expected_arrival_date':'2025-05-09','status':'confirmed'},
                         {'sku':'a','quantity':1000,'expected_arrival_date':'2025-05-03','status':'cancelled'}]);return f,inv,ships,T

def test_procurement_arrival_moq_and_negative_ip():
    f,inv,ships,T=inputs(-20);r=recommend(f,inv,ships,T,True).iloc[0]
    assert r.eligible_inbound==20 and r.inventory_position==-10 and r.order_quantity==60
    inv['on_hand']=45;r=recommend(f,inv,ships,T,True).iloc[0];assert r.order_quantity==0
    inv['on_hand']=30;r=recommend(f,inv,ships,T,True).iloc[0];assert r.order_quantity==36

def test_procurement_horizon_and_gate():
    f,inv,ships,T=inputs(0,8,1);assert recommend(f,inv,ships,T,True).iloc[0].status=='INSUFFICIENT_FORECAST_HORIZON'
    assert recommend(f,inv,ships,T,False).iloc[0].status=='BACKTEST_REQUIRED'

def test_zero_denominator(): assert score(np.zeros(2),np.ones(2))['wmape'] is None

def test_tsb_zero(): assert (tsb(np.zeros(200),7)==0).all()

def test_single_model_failure_isolated():
    a=pd.Series(np.ones(40),index=pd.date_range('2025-01-01',periods=40));b=a.copy();b.iloc[5]=np.nan
    pred,status=fit_predict({'a':a,'b':b},a.index[-1],7,{'min_train_days':30,'lightgbm_estimators':2})
    assert np.isfinite(pred['a']['tsb']).all() and status['b']['holt_winters']['state']=='failed'
    assert np.isfinite(pred['b']['lightgbm']).all()


def test_end_to_end_and_holdout(tmp_path):
    from pathlib import Path
    from sales_forecasting.pipeline import run
    dates=pd.date_range('2025-01-01',periods=60)
    d=pd.DataFrame([{'date':str(day.date()),'sku':sku,'sales':float(10+day.weekday()),'data_source':'synthetic'} for sku in ['a','b'] for day in dates])
    d.to_csv(tmp_path/'sales.csv',index=False)
    c={'_root':str(tmp_path),'data':{'sales_path':'sales.csv','inventory_path':'inv.csv','inbound_path':'inbound.csv'},
       'forecast':{'horizon_days':7,'min_train_days':30,'origin_step_days':7,'holdout_days':14},'ensemble':{},'interval':{},
       'models':{'lightgbm_estimators':2,'sarimax_maxiter':10},'output':{'directory':'artifacts'}}
    manifest=run(c);folder=Path(manifest['artifacts'])
    assert manifest['synthetic'] and (folder/'base_oof_predictions.csv').exists()
    latest=json.loads((tmp_path/'artifacts/latest.json').read_text(encoding='utf-8'))
    assert len(latest['rows'])==14 and len(latest['evaluation'])>0
    base=pd.read_csv(folder/'base_oof_predictions.csv');assert (pd.to_datetime(base.training_cutoff)==pd.to_datetime(base.origin)).all()
    assert all(r['status']=='MISSING_INVENTORY_PARAMETERS' for r in latest['procurement'])


def test_missing_inbound_does_not_imply_zero():
    f,inv,ships,T=inputs()
    assert recommend(f,inv,pd.DataFrame(columns=['sku']),T,True).iloc[0].status=='MISSING_INBOUND_DATA'


def test_missing_tsb_skips_update_not_zero():
    assert np.allclose(tsb(np.array([10.,np.nan,10.]),7),tsb(np.array([10.,10.]),7))
    assert not np.allclose(tsb(np.array([10.,0.,10.]),7),tsb(np.array([10.,10.]),7))
