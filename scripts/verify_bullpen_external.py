"""Independent coefficient, payout and past-workload verification, no fitter calls."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
import argparse,hashlib,json,math,sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities
from build_mlb_bullpen_external import read,stamp,source_index


def market(r):
    t=r.get('target')
    return market_probabilities(t['prices'])[t['order'].index('home')] if t else market_probabilities(r['historical_prices'])[0]


def reference(r):
    t=r.get('target');m=market(r)
    return .9*m+.1*t['vote_counts'].get('home',0)/t['voters'] if t else m


def probability(config,row,base):
    if config is None:return base
    eta=math.log(base/(1-base))+sum(max(-6,min(6,row['features'][n]/s))*b for n,s,b in zip(config['names'],config['scale'],config['beta']))
    return 1/(1+math.exp(-eta)) if eta>=0 else math.exp(eta)/(1+math.exp(eta))


def check_metric(metric,rows,ps):
    wins=push=0;profit=Decimal(0);probs=[];ys=[]
    for r,p in zip(rows,ps):
        if r['hg']==r['ag']:push+=1;continue
        t=r.get('target');prices=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away']
        home=p>=.5;correct=home==(r['hg']>r['ag']);wins+=int(correct)
        profit+=Decimal(str(prices[order.index('home' if home else 'away')]))-1 if correct else Decimal(-1)
        probs.append(p);ys.append(int(r['hg']>r['ag']))
    assert metric['n']==len(rows)
    if not rows:return
    assert metric['wins']==wins and metric['pushes']==push and metric['net_krw']==int(profit*10000)
    assert abs(metric['roi']-float(profit/len(rows)))<1e-12
    assert abs(metric['accuracy']-wins/(len(rows)-push))<1e-12
    assert abs(metric['brier']-sum((p-y)**2 for p,y in zip(probs,ys))/len(probs))<1e-12


def common_replay(row,by_team):
    d=date.fromisoformat(row['date']);sides=[]
    for team in [row['home'],row['away']]:
        past=[p for p in by_team[row['league'],team] if 0<(d-date.fromisoformat(p['date'])).days<=28]
        y=[p for p in past if (d-date.fromisoformat(p['date'])).days==1];three=[p for p in past if (d-date.fromisoformat(p['date'])).days<=3]
        previous={p['player'] for p in past if (d-date.fromisoformat(p['date'])).days==2};yi={p['player'] for p in y};rank=defaultdict(float)
        for p in past:rank[p['player']]+=p['outs']
        top=sorted(rank,key=lambda p:(-rank[p],p))[:3]
        sides.append(dict(relief_outs1=sum(p['outs'] for p in y),relief_outs3=sum(p['outs'] for p in three),
            relief_consecutive=len(previous&yi),relief_top3_outs_used1=sum(p in yi for p in top),
            relief_kbb_per_out28=(sum(p['k']-p['bb'] for p in past)+9)/(sum(p['outs'] for p in past)+45)))
    return {n:sides[0][n]-sides[1][n] for n in sides[0]}


def verify(folder):
    report=json.loads((folder/'results.json').read_text(encoding='utf8'));ledger=read(folder/'predictions.jsonl')
    for name,digest in report['input_sha256'].items():assert hashlib.sha256((folder/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest
    asian=read(folder/'asian-features.jsonl');history=read(folder/'sources'/'asian-normalized-history.jsonl.gz');by_team=defaultdict(list)
    for r in history:
        assert sum(p['runs'] for p in r['pitchers'][0])==r['ag'] and sum(p['runs'] for p in r['pitchers'][1])==r['hg']
        for i,team in enumerate([r['home'],r['away']]):
            for p in r['pitchers'][i][1:]:by_team[r['league'],team].append(dict(p,date=r['date']))
    common_values=0
    for r in asian:
        expected=common_replay(r,by_team)
        for n,v in expected.items():assert abs(v-r['features'][n])<1e-12;common_values+=1
        for side in ['home','away']:
            latest=r['proof'][side]['latest_source_date'];assert latest is None or latest<r['date']
    mlb=read(folder/'mlb-external-features.jsonl');frozen=json.loads((folder/'frozen-mlb-model.json').read_text(encoding='utf8'))['config']
    excluded=set(json.loads((folder/'excluded-mlb-fixtures.json').read_text(encoding='utf8')))
    assert not excluded&{r['gamePk'] for r in mlb}
    games,raw=source_index(folder);mlb_values=0
    for r in mlb:
        cutoff=stamp(r['decision_at']);g=games[r['gamePk']]
        assert cutoff<stamp(g['gameDate'])-timedelta(minutes=10)
        assert cutoff<stamp(g['gameInfo']['firstPitch'])-timedelta(minutes=10)
        d=date.fromisoformat(g['officialDate']);side_values=[]
        for side in ['home','away']:
            team=g['teams'][side]['team']['id']
            past=[p for p in raw[team] if p['date']<g['officialDate'] and p['completed_bound']<cutoff and cutoff-timedelta(days=28)<=p['played_at']]
            ys=(d-timedelta(days=1)).isoformat();d2=(d-timedelta(days=2)).isoformat();ds={(d-timedelta(days=i)).isoformat() for i in [1,2,3]}
            y=[p for p in past if p['date']==ys];three=[p for p in past if p['date'] in ds];yi={p['player'] for p in y};pi={p['player'] for p in past if p['date']==d2};ranks=defaultdict(lambda:[0.,0.])
            for p in past:ranks[p['player']][0]+=p['stat'].get('saves',0)+p['stat'].get('holds',0);ranks[p['player']][1]+=p['stat'].get('outs',0)
            top=sorted(ranks,key=lambda p:(-ranks[p][0],-ranks[p][1],p))[:3]
            side_values.append(dict(relief_pitches1=sum(p['stat'].get('numberOfPitches',0) for p in y),relief_pitches3=sum(p['stat'].get('numberOfPitches',0) for p in three),
                relief_consecutive=len(yi&pi),relief_top3_used1=sum(p in yi for p in top),
                relief_kbb28=(sum(p['stat'].get('strikeOuts',0)-p['stat'].get('baseOnBalls',0) for p in past)+14)/(sum(p['stat'].get('battersFaced',0) for p in past)+100)))
            assert sorted({p['gamePk'] for p in past})==r['proof'][side]['source_game_ids']
        for n in frozen['names']:assert abs(side_values[0][n]-side_values[1][n]-r['features'][n])<1e-12;mlb_values+=1
    probabilities=settlements=0
    for league,selection in report['asian_selection'].items():
        val=[r for r in asian if r['league']==league and r['role']=='validation'];evaluation=[r for r in asian if r['league']==league and r['role']=='evaluation']
        for name,m in selection['models'].items():
            vp=[probability(m['selection_fit'],r,market(r)) for r in val];ep=[probability(m['final_fit'],r,reference(r)) for r in evaluation]
            check_metric(m['validation'],val,vp);check_metric(m['evaluation'],evaluation,ep);settlements+=2;probabilities+=len(vp)+len(ep)
        models=selection['models'];chosen=min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))
        assert chosen==selection['selected']
    for r in ledger:
        config=frozen if r['league']=='MLB' else report['asian_selection'][r['league']]['models'][r['model']]['final_fit']
        base=market(r) if r['league']=='MLB' else reference(r)
        assert abs(base-r['baseline_probability'])<1e-12
        assert abs(probability(config,r,base)-r['candidate_probability'])<1e-12;probabilities+=1
    for label,periods in report['periods'].items():
        league='MLB' if label=='MLB_frozen_market_only' else label
        for period,metrics in periods.items():
            rows=[r for r in ledger if r['league']==league and (period=='all' or
                period=='before_aug10' and r['date']<'2026-08-10' or period=='aug10_onward' and r['date']>='2026-08-10' or
                period=='before_july' and r['date']<'2026-07-01' or period=='july_onward' and r['date']>='2026-07-01')]
            check_metric(metrics['baseline'],rows,[r['baseline_probability'] for r in rows]);check_metric(metrics['candidate'],rows,[r['candidate_probability'] for r in rows]);settlements+=2
    result=dict(ok=True,evaluation_rows=len(ledger),new_mlb_games=len(mlb),common_feature_values=common_values,mlb_feature_values=mlb_values,
        coefficient_probabilities=probabilities,independent_settlements=settlements,past_pitching_games_reconciled=len(history))
    (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8',newline='\n')
    manifest={str(p.relative_to(folder)).replace('\\','/'):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n') if p.suffix in {'.json','.jsonl','.md'} else p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file() and p.name!='verified-sha256.json'}
    (folder/'verified-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);verify(p.parse_args().folder)
