import json
from pathlib import Path
import pandas as pd
import numpy as np
from sales_forecasting.ensemble import optimize,combine
from sales_forecasting.models import NAMES
root=Path(__file__).resolve().parents[1]/'sales_forecasting/artifacts/production'
latest=json.loads((root/'latest.json').read_text(encoding='utf-8'));folder=Path(latest['artifacts']);d=pd.read_csv(folder/'base_oof_predictions.csv',parse_dates=['origin','target_date']);d=d[d.target_date<=pd.Timestamp(latest['development_end'])]
cut=d.target_date.min()+(d.target_date.max()-d.target_date.min())*.7
rows=[]
for h,g in d.groupby('horizon'):
 train=g[g.target_date<=cut];test=g[(g.origin>=cut)&(g.target_date>cut)]
 for reg in [.001,.01,.05,.1,1,10]:
  w,source=optimize(train[[n+'_pred' for n in NAMES]],train.actual,[.25]*4,20,reg)
  pred=np.array([combine(r,w)[0] for r in test[[n+'_pred' for n in NAMES]].to_numpy()]);actual=test.actual.to_numpy();mask=np.isfinite(pred)&np.isfinite(actual)
  den=np.abs(actual[mask]).sum()
  rows.append({'horizon':h,'lambda':reg,'training_n':len(train),'validation_n':int(mask.sum()),'development_validation_wmape':float(np.abs(pred[mask]-actual[mask]).sum()/den*100) if den else None,'weights':json.dumps(w.tolist()),'source':source,'note':'Development-only pooled prior sensitivity; production lambda remains fixed. Final holdout not used.'})
(root/'reports').mkdir(exist_ok=True);pd.DataFrame(rows).to_csv(root/'reports/regularization_sensitivity.csv',index=False,encoding='utf-8-sig');print('Development-only sensitivity rows:',len(rows))
