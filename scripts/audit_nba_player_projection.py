"""Independent scalar audit of the player-history proxy; no target lineup inference."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path


def rows(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def verified(path):
    body = path.read_bytes()
    receipt = json.loads(path.with_suffix('.receipt.json').read_text(encoding='utf8'))
    if receipt['status'] != 200 or hashlib.sha256(body).hexdigest() != receipt['sha256']:
        raise ValueError('source_hash')
    return json.loads(body)


def day_of(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat()


def shooting(rotation, states, league_a, league_m, minutes, da, dm, spatial, ref):
    families = ((0,1,2,3,4),(5,6,7,8))
    def pool(v):
        return list(v) if spatial else [sum(v[:5]),sum(v[5:])]
    la,lm,da,dm = map(pool,(league_a,league_m,da,dm))
    if not spatial:
        families = ((0,),(1,))
    accuracy = []
    for family in families:
        numerator = denominator = 0.
        for pid, projected in rotation.items():
            state = states.get(pid)
            eligible = state and state['games'] >= 5
            a,m = (pool(state['a']),pool(state['m'])) if eligible else (None,None)
            for i in family:
                if la[i] == 0:
                    continue
                base = ref.clip(lm[i]/la[i]); strength = 200*la[i]/sum(la[j] for j in family)
                rate = la[i]/minutes
                if eligible:
                    rate = (a[i]+240*rate)/(state['minutes']+240)
                    off = ref.clip((m[i]+strength*base)/(a[i]+strength))
                else:
                    off = base
                defense = ref.clip((dm[i]+strength*base)/(da[i]+strength))
                p = ref.clip(ref.sigmoid(ref.logit(off)+ref.logit(defense)-ref.logit(base)))
                weight = projected*rate
                numerator += weight*p; denominator += weight
        accuracy.append(numerator/denominator)
    return accuracy


def audit(args):
    spec = importlib.util.spec_from_file_location('scalar_spatial_reference',args.reference)
    ref = importlib.util.module_from_spec(spec); spec.loader.exec_module(ref)
    pool = sorted(rows(args.hard),key=lambda r:(r['day'],r['date_utc'],r['game_id']))
    by_id = {r['game_id']:r for r in pool}
    people = rows(args.players/'player_games.jsonl.gz')
    coverage=json.loads((args.players/'pregame-lineup-coverage.json').read_text(encoding='utf8'))
    if coverage['strict_verified_pregame_lineup_games'] != 0:
        raise ValueError('new_confirmed_lineups_require_a_separate_audit')
    grouped = defaultdict(list)
    for person in people:
        grouped[person['game_id'],person['team']].append(person)
    team_audits = {(r['game_id'],r['team']):r for r in rows(args.players/'team_audit.jsonl.gz')}
    clean = []
    for game in pool:
        valid = True
        for i,team in enumerate(game['teams']):
            audit = team_audits[game['game_id'],team]
            played = [p for p in grouped[game['game_id'],team] if (p['minutes_seconds_displayed'] or 0)>0]
            if audit['minutes_status'] not in ('exact_match','display_precision_consistent') or any(audit['unattributed'].values()) or any(not p['accounting_ok'] for p in played):
                valid = False
            for name, field in [('hard_attempts','attempts'),('hard_makes','makes')]:
                if [sum(p[name][k] for p in played) for k in range(9)] != game['sides'][i][field]:
                    valid = False
        if valid:
            clean.append(game)
    events = {}
    teams = {t for g in pool for t in g['teams']}
    for path in sorted((args.cache/'scoreboard').glob('*.json')):
        if path.name.endswith('.receipt.json'):
            continue
        for event in verified(path).get('events',[]):
            identifier = str(event['id'])
            if identifier in events and events[identifier] != event:
                raise ValueError('conflicting_event')
            events[identifier] = event
    histories = defaultdict(list)
    for event in events.values():
        if not event.get('status',{}).get('type',{}).get('completed'):
            continue
        cs = event['competitions'][0]['competitors']
        if len(cs)!=2 or any(c.get('team',{}).get('abbreviation') not in teams for c in cs) or cs[0]['score']==cs[1]['score']:
            continue
        for c in cs:
            histories[c['team']['abbreviation']].append((day_of(event['date']),event['date'],str(event['id'])))
    for history in histories.values():
        history.sort()
    cache = {}
    forecasts = rows(args.model/'predictions.jsonl.gz')
    excluded = rows(args.model/'exclusions.jsonl.gz')
    frozen = {r['id']:r for r in rows(args.old)}
    if len(forecasts)+len(excluded)!=len(frozen) or len({r['id'] for r in forecasts+excluded}) != len(frozen) or {r['id'] for r in forecasts+excluded} != set(frozen):
        raise ValueError('missing_or_duplicate_target')
    max_prob = max_rotation = 0.
    for row in forecasts:
        previous = frozen[row['id']]
        if any(row[k] != previous[k] for k in ('id','espn_id','day','teams','scores','odds','home_win','period')):
            raise ValueError('frozen_identity_changed')
        d = date.fromisoformat(row['day']); end = (d-timedelta(days=4)).isoformat()
        for i, team in enumerate(row['teams']):
            expected = [v[2] for v in histories[team] if v[0] <= end][-10:]
            if expected != row['rotation'][i]['history_game_ids'] or len(expected)!=10:
                raise ValueError('not_latest_ten')
            totals = defaultdict(float)
            for identifier in expected:
                if identifier not in cache:
                    cache[identifier] = verified(args.cache/'summary'/(identifier+'.json'))
                data = cache[identifier]
                for block in data['boxscore']['players']:
                    if block['team']['abbreviation'] != team:
                        continue
                    for group in block['statistics']:
                        for person in group['athletes']:
                            fields = dict(zip(group['keys'],person.get('stats',[])))
                            raw = fields.get('minutes',0 if person.get('didNotPlay') else None)
                            if raw is None:
                                raise ValueError('missing_minutes')
                            if isinstance(raw,str) and ':' in raw:
                                mm,ss=map(int,raw.split(':')); minute=mm+ss/60
                            else:
                                minute=float(raw)
                            totals[str(person['athlete']['id'])] += minute
            calculated = {k:240*v/sum(totals.values()) for k,v in totals.items() if v>0}
            if set(calculated) != set(row['rotation'][i]['minutes']):
                raise ValueError('target_roster_filter')
            error = max(abs(v-row['rotation'][i]['minutes'][k]) for k,v in calculated.items())
            max_rotation=max(max_rotation,error)
            if error > 1e-9:
                raise ValueError('projected_minutes_not_reproduced')
        active = [g for g in clean if d-timedelta(days=365) <= date.fromisoformat(g['day']) <= d-timedelta(days=4)]
        states = {}; la=[0.]*9; lm=[0.]*9; exposure=0.
        for game in active:
            for team in game['teams']:
                for p in grouped[game['game_id'],team]:
                    minute=(p['minutes_seconds_displayed'] or 0)/60
                    if minute<=0:
                        continue
                    s=states.setdefault(str(p['athlete_id']),dict(games=0,minutes=0.,a=[0.]*9,m=[0.]*9))
                    s['games']+=1; s['minutes']+=minute; exposure+=minute
                    for k in range(9):
                        s['a'][k]+=p['hard_attempts'][k]; s['m'][k]+=p['hard_makes'][k]
                        la[k]+=p['hard_attempts'][k]; lm[k]+=p['hard_makes'][k]
        own,defense,league,shared,_,_=ref.context(pool,row)
        for kind,spatial in [('player_nonspatial',False),('player_spatial',True)]:
            means=[]
            for i,team in enumerate(row['teams']):
                opponent=row['teams'][1-i]
                allowed=[g for g in active if opponent in g['teams']]
                ids=[g['game_id'] for g in active if team in g['teams']]
                if len(ids)!=len(row['player_source_game_ids'][i]) or set(ids)!=set(row['player_source_game_ids'][i]):
                    raise ValueError('player_source_window')
                da=[sum(g['sides'][1-g['teams'].index(opponent)]['attempts'][k] for g in allowed) for k in range(9)]
                dm=[sum(g['sides'][1-g['teams'].index(opponent)]['makes'][k] for g in allowed) for k in range(9)]
                accuracy=shooting(row['rotation'][i]['minutes'],states,la,lm,exposure,da,dm,spatial,ref)
                families,ft=ref.family_components(own[i],defense[1-i],league)
                means.append([shared*families[k]['q_rate']*accuracy[k] for k in range(2)]+[shared*ft])
            p=ref.score_probability(means)
            error=abs(p-row['probabilities'][kind]); max_prob=max(max_prob,error)
            if error>1e-10:
                raise ValueError('player_probability_not_reproduced')
        for new,old in [('q','nonspatial'),('market','market'),('prior_outcome_form','prior_outcome_form')]:
            if row['probabilities'][new] != previous['probabilities'][old]:
                raise ValueError('control_changed')
    results = json.loads((args.model/'results.json').read_text(encoding='utf8'))
    metrics = {}
    for phase in ('A','B'):
        subset=[r for r in forecasts if r['period']==phase]
        metrics[phase]={key:ref.metrics(subset,key) for key in subset[0]['probabilities']}
        for key,values in metrics[phase].items():
            for stat,value in values.items():
                if abs(value-results[phase]['models'][key][stat])>1e-9:
                    raise ValueError('payout_metric_mismatch')
    return dict(strict_confirmed_lineup_games=0,strict_status='blocked_missing_pregame_evidence',
                proxy_games=len(forecasts),excluded=len(excluded),clean_history_games=len(clean),
                max_probability_error=max_prob,max_projected_minute_error=max_rotation,metrics=metrics)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for flag in ('hard','players','cache','model','old','reference','out'):
        parser.add_argument('--'+flag,type=Path,required=True)
    args=parser.parse_args(); result=audit(args)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='metrics'}))
