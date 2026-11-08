import importlib.util,json,sys
from pathlib import Path
from datetime import datetime
P=Path(__file__).resolve().parents[1]/'experiments/volleyball-history-20261108e';sys.path.insert(0,str(P))
def load(name):
 s=importlib.util.spec_from_file_location(name,P/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
m=load('kovo_model');c=load('kovo_history')
def test_no_same_day_or_future_receive_history():
 g=[]
 for i in range(5):
  for h in ('A','B'):
   g.append({'league':'KOVO남','date':f'2025-10-{i+1:02d}','home':h,'away':'X','number':i,'sets':[3,0],'receive':[30.,20.]})
 match={'league':'KOVO남','date':'2025-10-10','home':'A','away':'B','number':11,'sets':[3,0],'receive':[95.,5.]};g.append(match)
 p={'league':'KOVO남','day':'2025-10-10','home':'A','away':'B','odds':[1.8,1.9],'y':0,'id':'x'}
 a,_=m.build_features(g,[p]);assert len(a)==1;match['receive']=[-95.,99.];b,_=m.build_features(g,[p]);assert a[0]['feature']==b[0]['feature']

def test_collector_stops_on_forbidden_and_resumes(tmp_path,monkeypatch):
 monkeypatch.setattr(c.time,'sleep',lambda _:None)
 row={'season':{'value':'022'},'league':{'value':'201'},'number':1,'endTime':'20:00','gameDate':'2025-10-20','gender':'MALE','homeTeam':{'score':3},'awayTeam':{'score':0}}
 sf=tmp_path/'s.json';sf.write_text(json.dumps({'payload':[row]}),encoding='utf8')
 class Resp:status_code=403;content=b''
 class Session:
  def get(self,*a,**k):return Resp()
 monkeypatch.setattr(c.requests,'Session',Session);out=tmp_path/'out';c.collect(sf,out);v=json.loads((out/'manifest.json').read_text(encoding='utf8'));assert list(v.values())[0]['status']=='stopped_http'

def test_fitted_scale_uses_training_values_only():
 rows=[{'feature':i/100,'market':.6,'y':1 if i%2 else 0} for i in range(1,21)]
 beta,sigma=m.fit(rows);assert 0<sigma<1 and -2<=beta<=2
 import pytest
 with pytest.raises(ValueError):m.fit([{'feature':.1,'market':.6,'y':1}]*20)

def test_duplicate_price_fixture_is_excluded():
 g={'league':'KOVO남','date':'2025-10-10','home':'A','away':'B','number':11,'sets':[3,0],'receive':[30.,20.]}
 p={'league':'KOVO남','day':'2025-10-10','home':'A','away':'B','odds':[1.8,1.9],'y':0,'id':'x'}
 rows,audit=m.build_features([g],[p,dict(p,id='y')]);assert not rows and audit['KOVO남/2025/duplicate_price_fixture']==2
