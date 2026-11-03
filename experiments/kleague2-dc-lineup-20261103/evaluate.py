"""Score the frozen DC addendum against the exact original price/test cohort."""
import collections,datetime,hashlib,importlib.util,json,math,subprocess,sys
from pathlib import Path
import numpy as np
import dc_model as d
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1]
sys.path.insert(0,str(d.PRIOR));import evaluate as old
DC_COMMIT='bfb3c162a2c33936e4edb347ed41c2af0d393b3c';DC_SHA='b3e4783a3af9c16fdd04dfe6d37b9a565f765091c4b18b7805cf6ba62e13bf13'
PRIOR_COMMIT='7ba2ce8cfccd3e097014f95ba3b03560870d2f9a';PRIOR_SCORE_SHA='74f7a3db105dcf30d0aa9737fe0013bf23490ee23e041eaf85780f3b8dcbffe2'

def read_committed(path,commit,expected):
 body=path.read_bytes()
 if hashlib.sha256(body).hexdigest()!=expected:raise ValueError('hash mismatch '+str(path))
 gitpath=path.relative_to(REPO).as_posix();b=subprocess.check_output(['git','show',commit+':'+gitpath],cwd=REPO)
 if body!=b:raise ValueError('Git bytes mismatch '+gitpath)
 return body

def bootstrap(rows,metric,challenger,reference):
 groups=collections.defaultdict(list)
 for r in rows:
  y,w,_=datetime.date.fromisoformat(r['date']).isocalendar();groups[(y,w)].append(r)
 keys=sorted(groups);rng=np.random.default_rng(20261103);ds=[]
 for _ in range(2000):
  rs=[r for i in rng.integers(0,len(keys),size=len(keys)) for r in groups[keys[int(i)]]]
  if metric=='roi':a=old.priced_stats(rs,challenger)['roi'];b=old.priced_stats(rs,reference)['roi']
  else:a=old.stats(rs,challenger)[metric];b=old.stats(rs,reference)[metric]
  ds.append(a-b)
 return {'weeks':len(keys),'resamples':2000,'seed':20261103,'ci95':[float(x) for x in np.quantile(ds,[.025,.975])]}

def main():
 orig=[json.loads(l) for l in read_committed(d.PRIOR/'results/scored.jsonl',PRIOR_COMMIT,PRIOR_SCORE_SHA).splitlines()]
 frozen=[json.loads(l) for l in read_committed(ROOT/'frozen/test-predictions.jsonl',DC_COMMIT,DC_SHA).splitlines()]
 games,excluded=d.base.load_games();features=d.base.history_features(games);_,_,test=d.base.partition(games)
 original_pred=[json.loads(l) for l in (d.PRIOR/'frozen/test-predictions.jsonl').read_bytes().splitlines()];d.require_cohort(test,original_pred,features)
 if len(orig)!=128 or len(frozen)!=128 or [r['id'] for r in orig]!=[g['id'] for g in test] or [r['fixture_id'] for r in frozen]!=[g['id'] for g in test]:raise ValueError('external or incomplete test cohort')
 pars=json.loads((ROOT/'frozen/parameters.json').read_bytes());recalc={}
 for mode in ('team_only','lineup'):
  p=pars[mode];theta=np.array(p['theta']);rho,_,_=d.rho_transform(theta,len(p['teams']),p['candidate'])
  if abs(rho-p['rho'])>1e-14:raise ValueError('rho mismatch')
  recalc[mode]=d.predict({'teams':p['teams'],'theta':theta,'candidate':p['candidate'],'rho':rho},test,features)
 rows=[]
 for i,(r,f,g) in enumerate(zip(orig,frozen,test)):
  if f['kickoff_kst']!=g['kickoff'] or f['team_ids']!=g['teams'] or f['features_sha256']!=original_pred[i]['features_sha256']:raise ValueError('fixture or features mismatch')
  p={'poisson_team':r['p']['team_only'],'poisson_lineup':r['p']['lineup']}
  if r['odds'] is not None:p['market']=r['p']['market']
  for src,dest in [('team_only','dc_team'),('lineup','dc_lineup')]:
   rates,probs,unknown,_=recalc[src];model=f['models'][src]
   if not np.allclose(probs[i],model['p'],atol=1e-12,rtol=0) or not np.allclose(rates[i],model['rates'],atol=1e-12,rtol=0):raise ValueError('frozen prediction mismatch')
   if int(np.argmax(model['p']))!=model['pick'] or model['min_tau']<=0:raise ValueError('invalid frozen probability or tau')
   p[dest]=model['p']
  rows.append({'id':r['id'],'date':r['date'],'outcome':r['outcome'],'odds':r['odds'],'p':p})
 priced=[r for r in rows if r['odds'] is not None]
 if len(priced)!=127:raise ValueError('price cohort changed')
 methods=('poisson_team','poisson_lineup','dc_team','dc_lineup')
 metrics={'status':'retrospective_followup_after_prior_test_seen_not_blind','prior_protocol_commit':'ead1596c636233d6042c26e65d3e50d68309d35f','dc_protocol_commit':'d576040ecae5010380abf2df1c433dbf09add13e','dc_freeze_commit':DC_COMMIT,'dc_predictions_sha256':DC_SHA,'prior_scored_sha256':PRIOR_SCORE_SHA,'test_n':len(rows),'price_n':len(priced),'excluded':excluded,'test_metrics':{k:old.stats(rows,k) for k in methods},'price_metrics':{k:old.stats(priced,k) for k in methods+('market',)},'price_returns':{k:old.priced_stats(priced,k) for k in methods+('market',)},'test_scoreline_logloss':{k:d.score_loss(test,recalc[k][0],pars[k]['rho']) for k in ('team_only','lineup')},'changed_picks':{'dc_lineup_vs_dc_team':int(sum(np.argmax(r['p']['dc_lineup'])!=np.argmax(r['p']['dc_team']) for r in rows)),'dc_lineup_vs_dc_team_priced':int(sum(np.argmax(r['p']['dc_lineup'])!=np.argmax(r['p']['dc_team']) for r in priced))},'paired_ci95':{k:bootstrap(rows,k,'dc_lineup','dc_team') for k in ('accuracy','logloss','brier')},'price_paired_ci95':{k:bootstrap(priced,k,'dc_lineup','dc_team') for k in ('accuracy','logloss','brier','roi')},'price_lineup_vs_market_ci95':{k:bootstrap(priced,k,'dc_lineup','market') for k in ('accuracy','logloss','brier','roi')},'min_test_tau':{k:min(f['models'][k]['min_tau'] for f in frozen) for k in ('team_only','lineup')},'refit_rho':{k:pars[k]['rho'] for k in ('team_only','lineup')},'missing_features':{'no_previous_team_sides':int(sum(side[2] for f in original_pred for side in f['features'])),'fewer_than_three_team_sides':int(sum(side[3] for f in original_pred for side in f['features']))},'unknown_team_sides':{k:sum(sum(f['models'][k]['unknown_teams']) for f in frozen) for k in ('team_only','lineup')},'limitations':['DC addendum registered after the Poisson test was already observed; not blind.','Official XI and price data are retrospective with no verified pregame receipt or executable price.','No frozen operating prediction comparison; no production adoption.','No K1, cup or national-team player history.']}
 out=ROOT/'results';out.mkdir(exist_ok=True);(out/'metrics.json').write_bytes((json.dumps(metrics,sort_keys=True,indent=2)+'\n').encode());(out/'scored.jsonl').write_bytes(b''.join((json.dumps(r,sort_keys=True,separators=(',',':'))+'\n').encode() for r in rows));print(json.dumps(metrics,indent=2))
if __name__=='__main__':main()
