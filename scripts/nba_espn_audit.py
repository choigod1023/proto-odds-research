"""Independent arithmetic and historical-window audit of saved forecasts."""
import gzip,json,math
from collections import defaultdict
from datetime import date,timedelta
from nba_espn_research import OUT,PRICE,read_events,parse_game

def main():
 result=json.loads((OUT/'results.json').read_text(encoding='utf8'));rows=[json.loads(line) for line in gzip.open(OUT/'predictions.jsonl.gz','rt',encoding='utf8')]; events,_,_=read_events();games=[g for e in events.values() if (g:=parse_game(e))];games.sort(key=lambda g:(g['day'],g['utc'],g['id']));byid={g['id']:g for g in games};history=defaultdict(list)
 for g in games:
  for t in g['teams']:history[t].append(g)
 prices={r['id']:r for r in (json.loads(s) for s in gzip.open(PRICE,'rt',encoding='utf8')) if r['league']=='NBA'}
 checks=0
 for r in rows:
  g=byid[r['espn_id']];p=prices[r['id']];assert g['scores']==r['scores'] and g['teams']==r['teams'] and r['home_win']==int(p['y']==0)==int(g['scores'][0]>g['scores'][1]) and p['odds']==r['odds']
  cutoff=(date.fromisoformat(r['day'])-timedelta(days=4)).isoformat()
  for i,team in enumerate(g['teams']):
   recent=[x for x in history[team] if x['day']<=cutoff][-10:];assert [x['id'] for x in recent]==r['history_event_ids'][i] and len(recent)==10 and g['id'] not in r['history_event_ids'][i]
  s=result['states']['validation' if r['day']<'2026-01-01' else 'holdout'];z=math.log(r['market_home']/(1-r['market_home']))+s['beta']*(r['feature']-s['mean'])/s['sd'];assert abs(r['candidate']-1/(1+math.exp(-z)))<1e-12
  assert r['selected_side']==('home' if r['candidate']>=.5 else 'away');checks+=1
 for period in ('validation','holdout'):
  subset=[r for r in rows if (r['day']<'2026-01-01')==(period=='validation')]
  for field,key in [('candidate','candidate'),('market_home','market'),('existing_outcome_form','existing_outcome_form')]:
   n=len(subset);profit=0.;wins=0;ll=0.;brier=0.;odds=0.;equity=0.;peak=0.;mdd=0.
   for r in subset:
    prob=r[field];y=r['home_win'];pick=0 if prob>=.5 else 1;win=int(pick==(0 if y else 1));ret=win*r['odds'][pick]-1;profit+=ret;wins+=win;odds+=r['odds'][pick];ll-=y*math.log(prob)+(1-y)*math.log(1-prob);brier+=(prob-y)**2;equity+=ret;peak=max(peak,equity);mdd=max(mdd,peak-equity)
   expected=result['periods'][period][key]; actual={'n':n,'wins':wins,'hit_rate':wins/n,'roi':profit/n,'log_loss':ll/n,'brier':brier/n,'mean_odds':odds/n,'max_drawdown_units':mdd}
   for k,v in actual.items():assert abs(expected[k]-v)<1e-10,(period,key,k,expected[k],v)
 audit={'forecast_rows_checked':checks,'history_ids_per_row':20,'source_scores_and_price_labels_checked':True,'probabilities_recalculated':True,'summary_metrics_recalculated':True,'result':'pass'}
 (OUT/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf8');print(json.dumps(audit))
if __name__=='__main__':main()
