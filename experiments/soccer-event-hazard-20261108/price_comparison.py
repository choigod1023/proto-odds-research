"""Match a single third-party football-data derivative to fixed holdout forecasts."""
import collections, datetime, gzip, json, math, pathlib, random

HERE=pathlib.Path(__file__).parent
ALIASES={'AFC Bournemouth':'Bournemouth','Arsenal':'Arsenal','Aston Villa':'Aston Villa','Chelsea':'Chelsea','Crystal Palace':'Crystal Palace','Everton':'Everton','Leicester City':'Leicester','Liverpool':'Liverpool','Manchester City':'Man City','Manchester United':'Man United','Newcastle United':'Newcastle','Norwich City':'Norwich','Southampton':'Southampton','Stoke City':'Stoke','Sunderland':'Sunderland','Swansea City':'Swansea','Tottenham Hotspur':'Tottenham','Watford':'Watford','West Bromwich Albion':'West Brom','West Ham United':'West Ham'}
MODELS=('poisson','sequence','state_path','market')

def join(predictions, fixtures, prices):
    by_id={r['id']:r for r in fixtures}
    index=collections.defaultdict(list)
    for r in prices: index[(r['Date'],r['HomeTeam'],r['AwayTeam'])].append(r)
    result=[]; excluded=[]
    for pred in predictions:
        fixture=by_id[pred['id']]
        if fixture['home'] not in ALIASES or fixture['away'] not in ALIASES:
            excluded.append({'id':pred['id'],'reason':'unmapped_team'}); continue
        key=(fixture['date'],ALIASES[fixture['home']],ALIASES[fixture['away']])
        found=index[key]
        if len(found)!=1:
            excluded.append({'id':pred['id'],'reason':'no_match' if not found else 'ambiguous_duplicate'}); continue
        row=found[0]
        try:
            hs=float(row['FTHG']); aw=float(row['FTAG'])
            odds=[float(row[k]) for k in ('B365H','B365D','B365A')]
            if hs!=fixture['home_score'] or aw!=fixture['away_score'] or row['FTR']!=('H' if hs>aw else 'D' if hs==aw else 'A') or pred['result']!=(0 if hs>aw else 1 if hs==aw else 2):
                raise ValueError('score_mismatch')
            if any(not math.isfinite(o) or o<=1 for o in odds): raise ValueError('invalid_prices')
        except (ValueError,TypeError,KeyError) as e:
            excluded.append({'id':pred['id'],'reason':str(e) or 'invalid_price_or_score'}); continue
        inv=[1/o for o in odds]; market=[x/sum(inv) for x in inv]
        result.append({**pred,'kickoff':fixture['kickoff'],'home_score':fixture['home_score'],'away_score':fixture['away_score'],'odds':odds,'market':market})
    return sorted(result,key=lambda r:(r['date'],r['kickoff'],r['id'])),excluded

def pick(probs): return max(range(3),key=lambda k:probs[k])
def profit(row,model):
    k=pick(row[model]); return row['odds'][k]-1 if k==row['result'] else -1
def metrics(rows, model):
    n=len(rows); daily=collections.defaultdict(float)
    for r in rows: daily[r['date']]+=profit(r,model)
    equity=peak=mdd=0.
    for day in sorted(daily):
        equity+=daily[day]; peak=max(peak,equity); mdd=max(mdd,peak-equity)
    return {'n':n,'hits':sum(pick(r[model])==r['result'] for r in rows),'accuracy':sum(pick(r[model])==r['result'] for r in rows)/n,'roi':sum(profit(r,model) for r in rows)/n,'log_loss':sum(-math.log(max(1e-12,r[model][r['result']])) for r in rows)/n,'brier':sum(sum((p-(i==r['result']))**2 for i,p in enumerate(r[model])) for r in rows)/n,'daily_close_mdd_units':mdd}

def main():
    fixtures=json.loads((HERE/'inputs.json').read_text(encoding='utf8'))
    prices=json.loads((HERE/'price_rows.json').read_text(encoding='utf8'))
    with gzip.open(HERE/'state_predictions.jsonl.gz','rt',encoding='utf8') as f: predictions=[json.loads(line) for line in f]
    rows,excluded=join(predictions,fixtures,prices)
    if not rows: raise ValueError('no matched records')
    weeks=collections.defaultdict(list)
    for r in rows: weeks[str(datetime.date.fromisoformat(r['date']).isocalendar()[:2])].append(r)
    clusters=list(weeks.values()); rng=random.Random(20261108)
    deltas={m:{measure:[] for measure in ('accuracy','roi')} for m in MODELS if m!='market'}
    for _ in range(2000):
        sample=[r for _ in clusters for r in rng.choice(clusters)]
        market=metrics(sample,'market')
        for model in deltas:
            values=metrics(sample,model)
            for measure in deltas[model]: deltas[model][measure].append(values[measure]-market[measure])
    cis={m:{k:[sorted(v)[49],sorted(v)[1949]] for k,v in measures.items()} for m,measures in deltas.items()}
    months={month:{model:metrics([r for r in rows if r['date'][:7]==month],model) for model in MODELS} for month in sorted({r['date'][:7] for r in rows})}
    changed={model:sum(pick(r[model])!=pick(r['market']) for r in rows) for model in MODELS if model!='market'}
    report={'population':len(predictions),'matched':len(rows),'excluded':excluded,'aliases':ALIASES,'market':'B365 normalized inverse odds, no-vig; 1X2 full-time; flat one-unit stake per fixture','metrics':{m:metrics(rows,m) for m in MODELS},'monthly':months,'changed_picks_vs_market':changed,'paired_week_bootstrap_vs_market_95pct':cis,'weeks':len(clusters),'seed':20261108}
    (HERE/'price_summary.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    with gzip.open(HERE/'priced_predictions.jsonl.gz','wt',encoding='utf8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
