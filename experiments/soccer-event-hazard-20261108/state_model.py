"""Discrete-minute score/red-card conditional intensity, simulated from kickoff."""
import collections, gzip, json, math, pathlib, random
import experiment as base

HERE = pathlib.Path(__file__).parent
PATHS = 3000

def train(rows, paths):
    exposure = [0,0,0]; goals = [0,0,0]
    red_exposure = [0,0]; red_goals = [0,0]
    reds = 0; used = 0; excluded = []
    for r in rows:
        path = paths[str(r['id'])]
        counts = collections.Counter(team for _,team,kind in path if kind=='goal')
        if counts[r['home_id']] != r['home_score'] or counts[r['away_id']] != r['away_score']:
            excluded.append(r['id']); continue
        used += 1; score=[0,0]; red=[0,0]
        for minute in range(90):
            for side in (0,1):
                diff=max(-1,min(1,score[side]-score[1-side]))+1
                exposure[diff]+=1; red_exposure[red[side]]+=1
            for m,team,kind in path:
                if m != minute: continue
                side = 0 if team==r['home_id'] else 1
                if kind=='goal':
                    diff=max(-1,min(1,score[side]-score[1-side]))+1
                    goals[diff]+=1; red_goals[red[side]]+=1; score[side]+=1
                else: reds+=1; red[side]=1
    rate=sum(goals)/sum(exposure)
    factors=[(g+rate*900)/(e+900)/rate for g,e in zip(goals,exposure)]
    red_factor=(red_goals[1]+rate*900)/(red_exposure[1]+900)/rate
    return {'score_factors':factors,'red_factor':red_factor,'red_hazard':reds/(used*180),'used':used,'excluded_score_mismatch':excluded,'training_goal_count':sum(goals),'training_red_count':reds}

def simulate(fixture, state, params, seed, paths=PATHS):
    h,a = state[fixture['home_id']], state[fixture['away_id']]
    def rate(attack, defence, prior):
        return max(.1,min(4.,((attack[0]+10*prior)/(attack[2]+10)+(defence[1]+10*prior)/(defence[2]+10))/2))
    rates=[rate(h,a,1.5)/90,rate(a,h,1.15)/90]
    rng=random.Random(seed); outcomes=[0,0,0]
    for _ in range(paths):
        score=[0,0]; red=[0,0]
        for minute in range(90):
            new_score=[0,0]; new_red=[0,0]
            for side in (0,1):
                diff=max(-1,min(1,score[side]-score[1-side]))+1
                intensity=rates[side]*params['score_factors'][diff]*(params['red_factor'] if red[side] else 1)
                new_score[side] = rng.random() < 1-math.exp(-intensity)
                new_red[side] = not red[side] and rng.random() < params['red_hazard']
            for side in (0,1): score[side]+=new_score[side]; red[side]|=new_red[side]
        outcomes[0 if score[0]>score[1] else 1 if score[0]==score[1] else 2]+=1
    return [(n+.5)/(paths+1.5) for n in outcomes]

def main():
    rows=json.loads((HERE/'inputs.json').read_text(encoding='utf8'))
    paths=json.loads((HERE/'training_paths.json').read_text(encoding='utf8'))
    trainrows=[r for r in rows if r['date']<base.TRAIN_END]
    history=[r for r in rows if r['date']<base.TEST_START]
    params=train(trainrows,paths); state=base.fit(history,[1.,1.])
    out=[]
    with gzip.open(HERE/'predictions.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            r=json.loads(line); fixture={k:r[k] for k in ('id','date','home_id','away_id')}
            r['state_path']=simulate(fixture,state,params,base.SEED+r['id']); out.append(r)
    weeks=collections.defaultdict(list)
    import datetime
    for r in out: weeks[str(datetime.date.fromisoformat(r['date']).isocalendar()[:2])].append(r)
    rng=random.Random(base.SEED); w=list(weeks.values()); deltas=[]
    for _ in range(2000):
        sample=[r for _ in w for r in rng.choice(w)]
        deltas.append(base.metrics(sample,'state_path')['accuracy']-base.metrics(sample,'poisson')['accuracy'])
    deltas.sort()
    changed=[r for r in out if r['state_path'].index(max(r['state_path'])) != r['poisson'].index(max(r['poisson']))]
    summary={'params':params,'paths_per_game':PATHS,'seed':'20261108 + match ID','metrics':base.metrics(out,'state_path'),'changed_picks':len(changed),'changed_metrics':{k:base.metrics(changed,k) for k in ('poisson','state_path')} if changed else None,'paired_week_accuracy_delta_ci':[deltas[49],deltas[1949]],'monthly':{month:base.metrics([r for r in out if r['date'][:7]==month],'state_path') for month in sorted({r['date'][:7] for r in out})}}
    (HERE/'state_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    with gzip.open(HERE/'state_predictions.jsonl.gz','wt',encoding='utf8') as f:
        for r in out: f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
