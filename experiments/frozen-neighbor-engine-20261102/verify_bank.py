"""Independent source-only fixed-bank replay using synthetic target features."""
import hashlib,importlib.util,json,math,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.devig import shin
spec=importlib.util.spec_from_file_location('frozen_engine',Path(__file__).with_name('engine.py'))
engine=importlib.util.module_from_spec(spec);sys.modules[spec.name]=engine;spec.loader.exec_module(engine)
raw=Path(sys.argv[1]).read_bytes().replace(b'\r\n',b'\n')
assert hashlib.sha256(raw).hexdigest()==engine.BANK_SHA
source=[json.loads(x) for x in raw.splitlines() if b'"split": "meta_history_2024"' in x]
bank=engine.load_bank(sys.argv[1]);assert len(source)==len(bank.rows)==228
max_market=max(abs(a-b) for r in source for a,b in zip(shin([float(x) for x in r['prices']]),r['market_p']))
assert max_market<1e-9
features={'market_p':[0.42,0.29,0.29],'dc_p':[0.38,0.31,0.31]}
actual=engine.forecast(bank,features)
def corrected(rows,den):
    a=[max(1e-6,features['market_p'][j]+sum((int(r['y']==j)-r['market_p'][j]) for r in rows)/den) for j in range(3)]
    return [x/sum(a) for x in a]
x=np.array(features['market_p']+features['dc_p'])
ordered=sorted(source,key=lambda r:(float(np.linalg.norm(np.array(r['market_p']+r['dc_p'])-x)),r['id']))[:60]
expected={'market':features['market_p'],'global':corrected(source,228*90/60),'neighbor':corrected(ordered,90)}
error=max(abs(a-b) for name in expected for a,b in zip(expected[name],actual[name]));assert error<1e-12
print(json.dumps({'source_bank_rows':len(source),'max_market_error':max_market,'synthetic_feature_max_probability_error':error}))
