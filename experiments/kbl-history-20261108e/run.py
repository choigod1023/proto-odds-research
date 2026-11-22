"""Retrospective KBL efficiency market-offset study; frozen protocol only."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit, ndtr

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = Path('C:/Users/user/AppData/Local/Temp/kbl-history-20261108e')
FIX = ROOT/'experiments/pregame-basketball-20261104/season-records.jsonl'
PRICE = ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
ALIASES = ROOT/'data/model_candidates/handicap-v1.json'


def sha(b): return hashlib.sha256(b).hexdigest()


def box(row, result, body):
    data = json.loads(body)
    teams = [str(row['payload']['home_team_id']), str(row['payload']['away_team_id'])]
    if len(set(teams)) != 2 or not isinstance(data, list) or not data:
        raise ValueError('invalid_teams_or_schema')
    sums = defaultdict(Counter); ids = set()
    names = ('playMin','playSec','score','fgtA','fgA','threepA','offr','to','ftA')
    for p in data:
        person, stats = p['player'], p['records']
        pid, team = str(person.get('pcode') or ''), str(person.get('tcode') or '')
        if not pid or pid in ids or team not in teams:
            raise ValueError('player_or_team_identity')
        ids.add(pid)
        if any(type(stats.get(k)) is not int or stats[k] < 0 for k in names) or stats['playSec'] >= 60:
            raise ValueError('invalid_player_field')
        if stats['fgtA'] != stats['fgA'] + stats['threepA']:
            raise ValueError('attempt_definition_mismatch')
        s = sums[team]; s['seconds'] += 60*stats['playMin']+stats['playSec']
        for k in ('score','fgtA','offr','to','ftA'): s[k] += stats[k]
    if set(sums) != set(teams): raise ValueError('missing_team')
    expected = [result['payload']['home_score'], result['payload']['away_score']]
    if [sums[x]['score'] for x in teams] != expected: raise ValueError('score_mismatch')
    duration = [sums[x]['seconds'] for x in teams]
    if duration[0] != duration[1] or duration[0] < 12000 or (duration[0]-12000)%1500:
        raise ValueError('duration_mismatch')
    poss = [sums[x]['fgtA']-sums[x]['offr']+sums[x]['to']+.44*sums[x]['ftA'] for x in teams]
    if min(poss) <= 0: raise ValueError('invalid_possessions')
    return {'home':teams[0], 'away':teams[1], 'day':row['kickoff'][:10], 'score':expected,
            'net':[100*expected[0]/poss[0]-100*expected[1]/poss[1],
                   100*expected[1]/poss[1]-100*expected[0]/poss[0]],
            'ot_periods': (duration[0]-12000)//1500}


def load(cache=CACHE):
    records = [json.loads(s) for s in FIX.read_text(encoding='utf8').splitlines()]
    fs = defaultdict(list); rs = defaultdict(list)
    for r in records:
        if r.get('league') != 'KBL' or r.get('kind') not in ('fixture','result'): continue
        (fs if r['kind']=='fixture' else rs)[r['payload']['source_fixture_id']].append(r)
    games, exclusions, raw_games, hashes = [], Counter(), [], []
    for ident in sorted(fs):
        if len(fs[ident]) != 1 or len(rs[ident]) != 1:
            exclusions['missing_or_duplicate_fixture_result'] += 1; continue
        f, r = fs[ident][0], rs[ident][0]
        if any(f.get(k) != r.get(k) for k in ('kickoff','home','away','league')):
            exclusions['fixture_result_mismatch'] += 1; continue
        payload = r['payload']; home, away = str(f['payload']['home_team_id']), str(f['payload']['away_team_id'])
        raw_games.append({'id':ident,'day':f['kickoff'][:10], 'home':home,'away':away,
                          'score':[payload['home_score'],payload['away_score']]})
        meta_path, body_path = cache/(ident+'.json'), cache/(ident+'.body.gz')
        if not meta_path.exists() or not body_path.exists(): exclusions['box_missing'] += 1; continue
        try:
            meta = json.loads(meta_path.read_text(encoding='utf8'))
            body = gzip.decompress(body_path.read_bytes())
            if meta['game_id'] != ident or meta['status'] != 200 or meta['sha256'] != sha(body):
                raise ValueError('receipt_mismatch')
            hashes.append({'game_id':ident,'sha256':meta['sha256'],'received_at':meta['received_at']})
            g = box(f,r,body); g['id']=ident; games.append(g)
        except (ValueError, KeyError, TypeError, OSError) as e:
            exclusions['box_invalid:'+str(e)] += 1
    return games, raw_games, dict(exclusions), hashes


def features(day, home, away, games, raw_games):
    cutoff = date.fromisoformat(day)-timedelta(days=4)
    verified = {g['id']:g for g in games}
    histories = []
    for team in (home,away):
        schedule = sorted((g for g in raw_games if date.fromisoformat(g['day']) <= cutoff and team in (g['home'],g['away'])),
                          key=lambda g:(g['day'],g['id']))[-10:]
        if len(schedule) < 10 or any(g['id'] not in verified for g in schedule): return None
        prior = [verified[g['id']] for g in schedule]
        histories.append(sum(g['net'][0 if g['home']==team else 1] for g in prior)/10)
    return (histories[0]-histories[1])/10


def make_targets(games, raw_games):
    alias = json.loads(ALIASES.read_text(encoding='utf8'))['KBL']['aliases']
    with gzip.open(PRICE,'rt',encoding='utf8') as f: quotes = [json.loads(s) for s in f]
    raw = defaultdict(list); q = defaultdict(list); counts = Counter()
    for g in raw_games: raw[(g['day'],g['home'],g['away'])].append(g)
    for p in quotes:
        if p.get('league') != 'KBL' or p['day'][:4] not in ('2025','2026'): continue
        counts['price_rows_'+p['day'][:4]] += 1
        home, away = (alias.get(''.join(p.get(s,'').split())) for s in ('home','away'))
        if not home or not away: counts['missing_team_alias'] += 1; continue
        q[(p['day'],home,away)].append(p)
    targets = []; opportunities = []
    for key, rows in sorted(q.items()):
        gs = raw.get(key,[])
        if len(rows) != 1 or len(gs) != 1: counts['duplicate_or_unmatched'] += 1; continue
        r,g = rows[0],gs[0]; odds = r.get('odds') or []
        if len(odds)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=1 for v in odds):
            counts['bad_price'] += 1; continue
        if g['score'][0] == g['score'][1]: counts['draw_refund_unverified'] += 1; continue
        y = int(g['score'][0] > g['score'][1])
        if r.get('y') != (0 if y else 1): counts['winner_conflict'] += 1; continue
        counts['matched_'+g['day'][:4]] += 1
        opportunities.append(g['day'])
        x = features(g['day'],g['home'],g['away'],games,raw_games)
        if x is None: counts['insufficient_history_'+g['day'][:4]] += 1; continue
        p = (1/odds[0])/(1/odds[0]+1/odds[1])
        targets.append(dict(g, y=y, odds=odds, x=x, market=p))
    return targets, dict(counts), opportunities


def fit_beta(rows):
    if not rows: raise ValueError('no train rows')
    def loss(beta):
        z=np.array([logit(r['market'])+beta*r['x'] for r in rows]); y=np.array([r['y'] for r in rows])
        return float(np.sum(np.logaddexp(0,z)-y*z)+5*beta**2)
    fit=minimize_scalar(loss,bounds=(-5,5),method='bounded')
    if not fit.success: raise ValueError('fit failed')
    return float(fit.x)


def fit_margin(games):
    n=len(games); mean=sum(g['score'][0]-g['score'][1] for g in games)/n
    totals=defaultdict(lambda:[0.,0])
    for g in games:
        diff=g['score'][0]-g['score'][1]-mean
        for team,sign in ((g['home'],1),(g['away'],-1)):
            totals[team][0]+=sign*diff; totals[team][1]+=1
    teams={k:v[0]/(v[1]+10) for k,v in totals.items()}
    errors=[g['score'][0]-g['score'][1]-mean-(teams[g['home']]-teams[g['away']])/2 for g in games]
    return mean, math.sqrt(sum(x*x for x in errors)/n), teams


def metrics(rows, name):
    if not rows: return None
    n=len(rows); wins=[]; odds=[]; net=[]; ll=[]; br=[]; daily=defaultdict(float)
    for r in rows:
        p=min(1-1e-12,max(1e-12,r['prob'][name])); pick=int(p>=.5); hit=int(pick==r['y'])
        price=r['odds'][0 if pick else 1]; profit=price-1 if hit else -1
        wins.append(hit); odds.append(price); net.append(profit); daily[r['day']]+=profit
        ll.append(-math.log(p if r['y'] else 1-p)); br.append((p-r['y'])**2)
    equity=peak=mdd=0.
    for day in sorted(daily):
        equity+=daily[day]; peak=max(peak,equity); mdd=max(mdd,peak-equity)
    return {'n':n,'hits':sum(wins),'accuracy':sum(wins)/n,'mean_odds':sum(odds)/n,
            'conditional_roi':sum(net)/n,'logloss':sum(ll)/n,'brier':sum(br)/n,'daily_mdd_units':mdd}


def bootstrap(rows, name, ref):
    dates=sorted({r['day'] for r in rows}); grouped={d:[r for r in rows if r['day']==d] for d in dates}
    rng=np.random.default_rng(20261108); values=defaultdict(list)
    for _ in range(2000):
        sample=[r for d in rng.choice(dates,size=len(dates),replace=True) for r in grouped[d]]
        a,b=metrics(sample,name),metrics(sample,ref)
        for k in ('accuracy','conditional_roi','logloss','brier'): values[k].append(a[k]-b[k])
    return {k:[float(x) for x in np.percentile(v,[2.5,97.5])] for k,v in values.items()}


def run():
    games, raw, errors, hashes=load(); targets, counts, opportunities=make_targets(games,raw)
    cohorts=[('validation','2025-09-26','2025-10-01','2025-12-27'),
             ('evaluation','2025-12-27','2026-01-01','2026-12-31')]
    output={'inputs':{'fixture_sha256':sha(FIX.read_bytes()),'price_sha256':sha(PRICE.read_bytes()),
            'alias_sha256':sha(ALIASES.read_bytes()),'box_manifest_sha256':sha(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode())},
            'box_count':len(games),'official_results':len(raw),'box_exclusions':errors,'target_counts':counts,'periods':{}}
    predictions=[]
    for name, cutoff, start, end in cohorts:
        train=[r for r in targets if '2025-01-01' <= r['day'] <= cutoff]
        evalrows=[r for r in targets if start <= r['day'] <= end]
        base=[g for g in raw if '2025-01-01' <= g['day'] <= cutoff]
        beta=fit_beta(train); mean,sd,team=fit_margin(base); rows=[]
        for r in evalrows:
            if r['home'] not in team or r['away'] not in team: continue
            margin=mean+(team[r['home']]-team[r['away']])/2
            prob={'market':r['market'],'team_margin':float(ndtr(margin/sd)),
                  'efficiency_offset':float(expit(logit(r['market'])+beta*r['x']))}
            rows.append({'id':sha(r['id'].encode())[:20],'day':r['day'],'period':name,'y':r['y'],
                         'odds':r['odds'],'x':r['x'],'prob':prob})
        names=('market','team_margin','efficiency_offset')
        months={m:{n:metrics([r for r in rows if r['day'][:7]==m],n) for n in names} for m in sorted({r['day'][:7] for r in rows})}
        opportunity_n=sum(start<=d<=end for d in opportunities)
        output['periods'][name]={'train_n':len(train),'beta':beta,'margin_train_n':len(base),'opportunities':opportunity_n,
            'eligible':len(evalrows), 'covered':len(rows),'coverage':len(rows)/opportunity_n if opportunity_n else None,
            'metrics':{n:metrics(rows,n) for n in names},'months':months,
            'paired_day_ci':{n+'_minus_'+ref:bootstrap(rows,n,ref) for n,ref in
                             (('team_margin','market'),('efficiency_offset','market'),
                              ('efficiency_offset','team_margin'))} if rows else {}}
        predictions.extend(rows)
    with (HERE/'predictions.jsonl.gz').open('wb') as rawfile:
        with gzip.GzipFile(fileobj=rawfile,mode='wb',filename='',mtime=0) as z:
            for r in predictions: z.write((json.dumps(r,sort_keys=True,separators=(',',':'))+'\n').encode())
    output['prediction_sha256']=sha((HERE/'predictions.jsonl.gz').read_bytes())
    (HERE/'summary.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (HERE/'box-receipts.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


if __name__=='__main__': run()
