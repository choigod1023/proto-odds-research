"""Offline retrospective experiment; never reads or writes operational state."""
import gzip, hashlib, json, math, random
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
SOURCE=Path('C:/Users/user/Documents/Codex/kbo-reliever-workload-20261106/experiments/kbo-reliever-workload-20261106/official-workload.json.gz')
PRED=ROOT/'experiments/pregame-baseball-20261104/model-predictions.jsonl.gz'
MATCH=ROOT/'experiments/pregame-baseball-20261104/overlap.json'

def starter_features(days, target, game):
    """Target box supplies retrospective starter identity; history ends prior day."""
    day=date.fromisoformat(target); histories={s:[] for s in ('home','away')}
    if 'appearances' not in game: return None, 'missing_target_box_starter'
    for side in histories:
        team=game[side]; starters=[p for p in game['appearances'].get(team,[]) if not p['relief']]
        if len(starters)!=1 or not str(starters[0]['id']).isdigit(): return None, 'invalid_target_starter_identity'
        player=str(starters[0]['id'])
        for gap in range(1,11):
            prior=(day-timedelta(days=gap)).isoformat()
            if prior not in days: return None,'missing_prior_day_schedule'
            for g in days[prior]['games']:
                if g.get('postponed') or team not in (g['home'],g['away']):continue
                if 'appearances' not in g or team not in g['appearances']:return None,'missing_prior_box'
                for a in g['appearances'][team]:
                    if str(a['id'])==player:histories[side].append((gap,int(a['pitches'])))
        if not histories[side]:return None,'starter_no_appearance_in_prior_10_days'
    h,a=histories['home'],histories['away']
    return [(sum(p for _,p in h)-sum(p for _,p in a))/150.,(min(g for g,_ in h)-min(g for g,_ in a))/10.],None

def fit(rows, offset):
    chosen=[r for r in rows if r['winner']!='draw']; assert chosen
    x=np.array([r['features'] for r in chosen]); y=np.array([r['winner']=='home' for r in chosen],dtype=float); z=logit(np.array([r[offset] for r in chosen]))
    def fun(w):
        t=z+x@w;return float(np.mean(np.logaddexp(0,t)-y*t)+.1*(w@w)),x.T@(expit(t)-y)/len(y)+.2*w
    r=minimize(fun,np.zeros(2),jac=True,method='L-BFGS-B'); assert r.success; return r.x

def enrich(row, weights):
    x=np.array(row['features']); probabilities={'market':row['market'],'m0':row['m0']}
    probabilities.update({name:float(expit(logit(row[offset])+x@w)) for name,(offset,w) in weights.items()})
    arms={}
    for name,p in probabilities.items():
        side='home' if p>=.5 else 'away'; odds=float(row['prices'][side]); result=row['winner']; profit=0. if result=='draw' else odds-1 if side==result else -1.
        arms[name]={'prob_home':p,'side':side,'odds':odds,'profit':profit}
    return dict(row,arms=arms)

def score(rows,arm):
    if not rows:return None
    decided=[r for r in rows if r['winner']!='draw']; byday=defaultdict(float)
    for r in rows:byday[r['date']]+=r['arms'][arm]['profit']
    total=peak=drawdown=0.
    for d in sorted(byday):total+=byday[d];peak=max(total,peak);drawdown=max(drawdown,peak-total)
    def loss(r):
        p=r['arms'][arm]['prob_home'];return -math.log(p if r['winner']=='home' else 1-p)
    return {'n':len(rows),'decided':len(decided),'refunds':len(rows)-len(decided),'hits':sum(r['arms'][arm]['side']==r['winner'] for r in decided),'accuracy':sum(r['arms'][arm]['side']==r['winner'] for r in decided)/len(decided) if decided else None,'mean_odds':sum(r['arms'][arm]['odds'] for r in rows)/len(rows),'roi':sum(r['arms'][arm]['profit'] for r in rows)/len(rows),'logloss':sum(loss(r) for r in decided)/len(decided) if decided else None,'brier':sum((r['arms'][arm]['prob_home']-(r['winner']=='home'))**2 for r in decided)/len(decided) if decided else None,'mdd_daily_close':drawdown}

def paired_ci(rows,arm,baseline):
    blocks=defaultdict(list)
    for r in rows:blocks[r['date']].append(r)
    blocks=list(blocks.values());rng=random.Random(20261108);dist=defaultdict(list)
    for _ in range(2000):
        sample=[r for _ in blocks for r in rng.choice(blocks)]; a,b=score(sample,arm),score(sample,baseline)
        for metric in ('accuracy','roi','logloss','brier'):
            if a[metric] is not None:dist[metric].append(a[metric]-b[metric])
    return {k:[sorted(v)[int(.025*(len(v)-1))],sorted(v)[int(.975*(len(v)-1))]] for k,v in dist.items()}

def main():
    blobs=[SOURCE,PRED,MATCH]; hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in blobs};cache=json.load(gzip.open(SOURCE,'rt',encoding='utf8')); overlap=json.loads(MATCH.read_text(encoding='utf8')); preds={}
    for l in gzip.open(PRED,'rt',encoding='utf8'):
        v=json.loads(l);x=v['payload']; name=x['model_id']
        if name in ('kbo-actual-training-2025-M0_actual','kbo-actual-training-2026-M0_actual'):
            key=(name[20:24],x['fixture_id']);assert key not in preds;preds[key]=x['probabilities']['home']
    rows=[];counts={}
    for year in ('2025','2026'):
        days=cache['years'][year]['days']; prices={r['fixture_id']:r for r in overlap[year]['rows']}; assert len(prices)==len(overlap[year]['rows']);c=Counter()
        for day,batch in sorted(days.items()):
            for game in batch['games']:
                if game.get('postponed'): c['postponed']+=1;continue
                c['schedule_opportunities']+=1
                fid=game['id'];features,error=starter_features(days,day,game)
                if error:c[error]+=1;continue
                c['features_available_retrospective']+=1
                if (year,fid) not in preds:c['no_exported_m0']+=1;continue
                if fid not in prices:c['no_exact_price_outcome']+=1;continue
                p=prices[fid]; winner=p['winner']; assert 'home_score' in game and 'away_score' in game
                official='home' if game['home_score']>game['away_score'] else 'away' if game['home_score']<game['away_score'] else 'draw';assert winner==official
                ph=float(p['prices']['home']);pa=float(p['prices']['away']);q=(1/ph)/(1/ph+1/pa)
                rows.append({'fixture_id':fid,'date':day,'year':year,'features':features,'winner':winner,'prices':p['prices'],'market_id':p['market_id'],'m0':preds[year,fid],'market':q});c['matched']+=1
        counts[year]=dict(c)
    train=[r for r in rows if r['year']=='2025' and r['date']<='2025-04-08']; val=[r for r in rows if r['year']=='2025' and r['date']>'2025-04-08'];test=[r for r in rows if r['year']=='2026'];assert train and val and test
    specs={'starter_m0': 'm0','starter_market':'market'}; initial={k:(v,fit(train,v)) for k,v in specs.items()}; fixed={k:(v,fit(train+val,v)) for k,v in specs.items()}; vr=[enrich(r,initial) for r in val];tr=[enrich(r,fixed) for r in test]; arms=('m0','market','starter_m0','starter_market')
    report={'source_sha256':hashes,'counts':counts,'split_n':{'train':len(train),'validation':len(val),'test':len(test)},'validation_coefficients':{k:w.tolist() for k,(_,w) in initial.items()},'frozen_coefficients':{k:w.tolist() for k,(_,w) in fixed.items()},'validation':{k:score(vr,k) for k in arms},'test':{k:score(tr,k) for k in arms},'paired_ci_test':{k:paired_ci(tr,k,'m0') for k in ('starter_m0','starter_market')},'paired_ci_vs_market':paired_ci(tr,'starter_market','market'),'switches_vs_market':sum(r['arms']['starter_market']['side']!=r['arms']['market']['side'] for r in tr),'date_bounds':{name:[min(r['date'] for r in group),max(r['date'] for r in group)] for name,group in [('train',train),('validation',val),('test',test)]},'switches_vs_m0':{k:sum(r['arms'][k]['side']!=r['arms']['m0']['side'] for r in tr) for k in ('starter_m0','starter_market')},'by_month':{d:{k:score([r for r in tr if r['date'][:7]==d],k) for k in arms} for d in sorted({r['date'][:7] for r in tr})},'limits':['retrospective actual target starter and lineup; source box receipts after target','prior-day features are retrospectively assembled not proven received before decision','2025 price matching excludes draws by outcome-dependent process','2026 previously inspected; not independent','not operational M0 or actual purchasing','pricing and timing unverified; windows only opening 25 days']}
    (OUT/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    with (OUT/'predictions.jsonl.gz').open('wb') as f:
        with gzip.GzipFile(filename='',fileobj=f,mode='wb',mtime=0) as g:
            for r in vr+tr:g.write((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode())
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
