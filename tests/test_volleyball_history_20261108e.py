import importlib.util,json,hashlib
from pathlib import Path
import pandas as pd
D=Path(__file__).resolve().parents[1]/'experiments/volleyball-history-20261108e'
def module(name):
 spec=importlib.util.spec_from_file_location(name,D/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=module('run');c=module('collect')
def test_excel_date_and_separate_metric_pnl():
 assert m.date_value('46183').strftime('%Y-%m-%d')=='2026-06-10'
 x=m.metrics([{'y':1,'odds':[1.5,2.5]},{'y':0,'odds':[1.7,2.2]}],[.8,.8]);assert x['accuracy']==.5 and abs(x['roi']+.25)<1e-9

def test_four_day_lag_and_future_does_not_change_feature():
 def g(date,a,b,success):return {'day':pd.Timestamp(date),'league':'남배네이','teams':{a:{'success':success,'attempts':20,'win':3,'loss':0},b:{'success':10,'attempts':20,'win':0,'loss':3}},'match_id':date}
 games=[g(f'2025-06-0{i}', 'A','X',10) for i in range(1,6)]+[g(f'2025-06-0{i}','B','Y',8) for i in range(1,6)]+[g('2025-06-08','A','B',15)]
 price={'day':'2025-06-08','league':'남배네이','home':'A','away':'B','odds':[1.8,1.8],'y':0,'id':'x'}
 a,_=m.make_features(games,[price]);assert not a
 price['day']='2025-06-10';games[-1]['day']=pd.Timestamp('2025-06-10');a,_=m.make_features(games,[price]);assert len(a)==1
 games[-1]['teams']['A']['success']=0;b,_=m.make_features(games,[price]);assert a[0]['feature']==b[0]['feature']

def test_resume_skips_verified_cached_files(tmp_path,monkeypatch):
 monkeypatch.setattr(c.time,'sleep',lambda _:None);monkeypatch.setattr(c,'FILES',['2025/test.csv'])
 class R:
  status_code=200;content=b'RECEPTION_Attempts\n42\n'
  def raise_for_status(self):pass
 class S:
  def __init__(self):self.calls=0
  def get(self,*a,**k):self.calls+=1;return R()
 s=S();c.collect(tmp_path,s,0);c.collect(tmp_path,s,0);assert s.calls==1
 (tmp_path/'test.csv').write_bytes(b'corrupt');c.collect(tmp_path,s,0);assert s.calls==2

def test_notfound_and_403_do_not_silently_succeed(tmp_path,monkeypatch):
 monkeypatch.setattr(c.time,'sleep',lambda _:None);monkeypatch.setattr(c,'FILES',['2025/test.csv'])
 class R:status_code=403
 class S:
  def get(self,*a,**k):return R()
 assert c.collect(tmp_path,S(),0)['2025/test.csv']['status']=='blocked'
