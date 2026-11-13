import numpy as np
import pandas as pd
from .models import NAMES

def score(y,p):
    mask=np.isfinite(y)&np.isfinite(p); y=np.asarray(y)[mask]; p=np.asarray(p)[mask]
    den=np.abs(y).sum()
    return {'n':len(y),'mae':float(np.abs(y-p).mean()) if len(y) else None,
            'wmape':float(np.abs(y-p).sum()/den*100) if den else None,'bias':float((p-y).sum()/den*100) if den else None}

def evaluate(d):
    output=[]
    groups=[('overall','all',d)]+[('sku',str(k),v) for k,v in d.groupby('sku')]+[('horizon',str(k),v) for k,v in d.groupby('horizon')]
    for level,key,g in groups:
        # Comparisons use the same realized targets for each candidate vs baseline.
        for model in ['baseline']+NAMES+['equal','ensemble']:
            col=model+'_pred'; s=score(g.actual.to_numpy(float),g[col].to_numpy(float))
            valid=np.isfinite(g.actual)&np.isfinite(g[col])&np.isfinite(g.baseline_pred)
            b=score(g.loc[valid,'actual'].to_numpy(float),g.loc[valid,'baseline_pred'].to_numpy(float))['wmape']
            own=score(g.loc[valid,'actual'].to_numpy(float),g.loc[valid,col].to_numpy(float))['wmape']
            coverage=width=None; interval_n=0
            if model=='ensemble':
                q=g.dropna(subset=['lower','upper','actual']); interval_n=len(q)
                if len(q): coverage=float(((q.actual>=q.lower)&(q.actual<=q.upper)).mean()); width=float((q.upper-q.lower).mean())
            output.append({'level':level,'key':key,'model':model,**s,'fva_percent':(b-own)/b*100 if b and own is not None else None,
                           'coverage':coverage,'mean_interval_width':width,'interval_n':interval_n})
    return pd.DataFrame(output)
