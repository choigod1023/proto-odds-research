"""Frozen out-of-sample shot-sequence research; no current-game event inputs."""
import collections, gzip, json, math, pathlib, random

HERE = pathlib.Path(__file__).parent
TRAIN_END = '2016-02-01'
TEST_START = '2016-03-01'
SEED = 20261108

def sequence(shots, team):
    prev = None
    for tid, minute, second, xg, outcome in shots:
        if tid != team:
            continue
        current = 60 * minute + second
        clustered = prev is not None and 0 <= current - prev <= 300
        yield xg, outcome == 'Goal', clustered
        prev = current

def calibration(rows):
    totals = [[5.0, 5.0], [5.0, 5.0]] # strong fixed shrinkage toward 1
    for r in rows:
        for team in (r['home_id'], r['away_id']):
            for xg, goal, clustered in sequence(r['shots'], team):
                totals[clustered][0] += goal
                totals[clustered][1] += xg
    return [a / b for a, b in totals]

def fit(rows, weights):
    totals = collections.defaultdict(lambda: [0., 0., 0])
    for r in rows:
        for team, opp in ((r['home_id'], r['away_id']), (r['away_id'], r['home_id'])):
            score = sum(xg * weights[c] for xg, _, c in sequence(r['shots'], team))
            totals[team][0] += score
            totals[opp][1] += score
            totals[team][2] += 1
    return dict(totals)

def outcome_probs(lh, la):
    def poisson(lam):
        p = [math.exp(-lam)]
        for k in range(1, 21): p.append(p[-1] * lam / k)
        return p
    h, a = poisson(lh), poisson(la)
    probs = [sum(h[i]*a[j] for i in range(21) for j in range(21) if (i > j if k == 0 else i == j if k == 1 else i < j)) for k in range(3)]
    return [x/sum(probs) for x in probs]

def forecast(fixture, state):
    # Only explicit prematch identity is accepted; no shots, goals or cards.
    h, a = (state[t] for t in (fixture['home_id'], fixture['away_id']))
    def rate(attack, defence, prior):
        return max(.1, min(4., ((attack[0]+10*prior)/(attack[2]+10) + (defence[1]+10*prior)/(defence[2]+10))/2))
    return outcome_probs(rate(h,a,1.5), rate(a,h,1.15))

def metrics(records, key):
    n = len(records)
    return {'n':n, 'accuracy':sum(r[key].index(max(r[key])) == r['result'] for r in records)/n, 'log_loss':sum(-math.log(max(1e-12,r[key][r['result']])) for r in records)/n, 'brier':sum(sum((p-(i==r['result']))**2 for i,p in enumerate(r[key])) for r in records)/n}

def main():
    rows = json.loads((HERE/'inputs.json').read_text(encoding='utf8'))
    if len(rows) != 380: raise ValueError('incomplete season: no evaluation')
    train = [r for r in rows if r['date'] < TRAIN_END]
    valid = [r for r in rows if TRAIN_END <= r['date'] < TEST_START]
    test = [r for r in rows if r['date'] >= TEST_START]
    weights = calibration(train)
    history = train + valid
    states = {'poisson':fit(history, [1.,1.]), 'sequence':fit(history, weights)}
    out = []
    for r in test:
        fixture = {k:r[k] for k in ('id','date','home_id','away_id')}
        result = 0 if r['home_score'] > r['away_score'] else 1 if r['home_score'] == r['away_score'] else 2
        out.append({**fixture, 'home':r['home'], 'away':r['away'], 'result':result, **{k:forecast(fixture,v) for k,v in states.items()}})
    rng = random.Random(SEED)
    weeks = collections.defaultdict(list)
    import datetime
    for r in out:
        d = datetime.date.fromisoformat(r['date']); weeks[str(d.isocalendar()[:2])].append(r)
    w = list(weeks.values()); delta=[]
    for _ in range(2000):
        sample = [r for _ in w for r in rng.choice(w)]
        delta.append(metrics(sample,'sequence')['accuracy'] - metrics(sample,'poisson')['accuracy'])
    delta.sort()
    changed = [r for r in out if r['poisson'].index(max(r['poisson'])) != r['sequence'].index(max(r['sequence']))]
    months = {month:{k:metrics([r for r in out if r['date'][:7] == month],k) for k in states} for month in sorted({r['date'][:7] for r in out})}
    summary = {'train':len(train),'validation':len(valid),'holdout':len(test),'train_end_exclusive':TRAIN_END,'holdout_start':TEST_START,'weights':weights,'metrics':{k:metrics(out,k) for k in states},'monthly':months,'changed_picks':len(changed),'changed_pick_metrics':{k:metrics(changed,k) for k in states} if changed else None,'paired_week_bootstrap_accuracy_delta_95pct':[delta[49],delta[1949]],'weeks':len(w),'priced_matches':0,'price_reason':'Official football-data source connection reset; no verified price joined','operating_m0_matches':0,'seed':SEED}
    (HERE/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    with gzip.open(HERE/'predictions.jsonl.gz','wt',encoding='utf8') as f:
        for r in out: f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print(json.dumps(summary,indent=2))

if __name__ == '__main__': main()
