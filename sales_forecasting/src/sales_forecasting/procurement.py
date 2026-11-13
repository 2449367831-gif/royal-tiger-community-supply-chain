import math
import numpy as np
import pandas as pd

def recommend(forecasts, inventory, inbound, origin, evaluated=False):
    rows=[]; origin=pd.Timestamp(origin)
    for sku,g in forecasts.groupby('sku'):
        result={'sku':sku,'order_quantity':None}
        if not evaluated: rows.append({**result,'status':'BACKTEST_REQUIRED'}); continue
        inv=inventory[inventory.sku==sku]
        fields=['on_hand','allocated','safety_stock','lead_time_days','review_period_days','moq','pack_size']
        if len(inv)!=1 or not set(fields)<=set(inv): rows.append({**result,'status':'MISSING_INVENTORY_PARAMETERS'}); continue
        r=inv.iloc[0]
        try:
            vals={k:float(r[k]) for k in fields}
            if not all(np.isfinite(v) for v in vals.values()) or any(vals[k]<0 for k in fields if k!='on_hand') or vals['pack_size']<=0: raise ValueError()
            if any(vals[k]!=int(vals[k]) for k in ['lead_time_days','review_period_days']): raise ValueError()
        except (TypeError,ValueError): rows.append({**result,'status':'INVALID_INVENTORY_PARAMETERS'}); continue
        P=int(vals['lead_time_days']+vals['review_period_days'])
        expected=pd.date_range(origin+pd.Timedelta(days=1),periods=P)
        fg=g.set_index('target_date').reindex(expected)
        if P<1 or fg.ensemble_pred.isna().any() or len(expected)>len(g): rows.append({**result,'status':'INSUFFICIENT_FORECAST_HORIZON'}); continue
        if not {'sku','quantity','expected_arrival_date','status'}<=set(inbound): rows.append({**result,'status':'MISSING_INBOUND_DATA'}); continue
        ships=inbound[inbound.sku==sku].copy() if not inbound.empty else inbound.copy()
        eligible=0.
        if not ships.empty:
            if not {'quantity','expected_arrival_date','status'}<=set(ships): rows.append({**result,'status':'INVALID_INBOUND'}); continue
            ships=ships[ships.status.isin(['confirmed','in_transit'])].copy()
            dates=pd.to_datetime(ships.expected_arrival_date,errors='coerce'); q=pd.to_numeric(ships.quantity,errors='coerce')
            if dates.isna().any() or not np.isfinite(q).all() or (q<0).any(): rows.append({**result,'status':'INVALID_INBOUND'}); continue
            if (dates<=origin).any(): rows.append({**result,'status':'OVERDUE_INBOUND_REVIEW_REQUIRED'}); continue
            eligible=float(q[dates<=origin+pd.Timedelta(days=P)].sum())
        demand=float(fg.ensemble_pred.sum()); ip=vals['on_hand']+eligible-vals['allocated']; raw=max(0,demand+vals['safety_stock']-ip)
        qty=0 if raw==0 else math.ceil(max(raw,vals['moq'])/vals['pack_size'])*vals['pack_size']
        rows.append({**result,'status':'ADVISORY','coverage_days':P,'forecast_demand':demand,'inventory_position':ip,'eligible_inbound':eligible,'raw_order':raw,'order_quantity':qty})
    return pd.DataFrame(rows)
