"""Reaggregate preserved KBO replay; all inputs are read only."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

MODELS = ('m0_actual', 'm0_projected', 'm1_projected')
SIDES = ('home', 'away')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def index_gzip(path):
    result = {}
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            fid = row['fixture_id']
            if fid in result:
                raise ValueError('duplicate fixture: ' + fid)
            result[fid] = row
    return result

def metric(rows):
    result = {'n': len(rows), 'decided': sum(r['winner'] != 'draw' for r in rows),
              'refunds': sum(r['winner'] == 'draw' for r in rows)}
    for model in MODELS:
        decided = [r for r in rows if r['winner'] != 'draw']
        hits = sum(r['models'][model]['pick'] == r['winner'] for r in decided)
        net = sum((Decimal(r['models'][model]['net']) for r in rows), Decimal(0))
        result[model] = {'hits': hits, 'errors': len(decided)-hits, 'net_units': str(net),
                         'roi': float(net/len(rows)) if rows else None,
                         'log_loss': sum(-math.log(max(1e-15, r['models'][model]['home_probability'] if r['winner']=='home' else 1-r['models'][model]['home_probability'])) for r in decided)/len(decided) if decided else None}
    for control in ('m0_actual', 'm0_projected'):
        cross = Counter(('correct' if r['models'][control]['pick']==r['winner'] else 'error') + '/' + ('correct' if r['models']['m1_projected']['pick']==r['winner'] else 'error') for r in rows if r['winner'] != 'draw')
        result[control+'_vs_m1'] = {k: cross[k] for k in ('correct/correct','correct/error','error/correct','error/error')}
    return result

def unknown(row, spec):
    result = []
    for side in SIDES:
        roster = row['roster'][side]
        hitters = roster['hitters']
        result.append(sum(p not in spec['hitters'] for p in hitters) if hitters is not None else None)
        p = roster['starter']
        result.append(None if p is None else p not in spec['starters'])
    return result

def group_unknown(u):
    if any(v is None for v in u): return 'roster_missing'
    return 'unknown_present' if any(u) else 'all_known'

def missing_group(row, suffix):
    n = sum(row['features'][s+'_'+suffix]['value'] is None for s in SIDES)
    return ('none', 'one_side', 'both_sides')[n]

def analyze(replay, features, labels, model):
    rows = replay['rows']
    if len(rows) != 667 or len({r['fixture_id'] for r in rows}) != 667: raise ValueError('expected 667 unique replay fixtures')
    groups = defaultdict(lambda: defaultdict(list))
    miss = Counter(); unknown_totals = Counter(); history = Counter(); history_by_selection = defaultdict(Counter); samples = []
    for r in rows:
        fid = r['fixture_id']
        if fid not in features or fid not in labels: raise ValueError('missing join: '+fid)
        f, label = features[fid], labels[fid]
        miss['label_settlement_rule'] += label['settlement_rule'] is None
        if f['event_date'] != r['date']: raise ValueError('date mismatch: '+fid)
        winner = 'home' if label['home_score'] > label['away_score'] else 'away' if label['home_score'] < label['away_score'] else 'draw'
        if winner != r['winner'] or label['status'] != 'final' or label['settlement_rule'] not in (None, 'draw_void_refund'): raise ValueError('label mismatch: '+fid)
        for m in MODELS:
            pick = r['models'][m]['pick']
            if pick not in SIDES: raise ValueError('invalid pick: '+fid)
            expected = Decimal(0) if winner == 'draw' else Decimal(str(r['prices'][pick]))-1 if pick == winner else Decimal(-1)
            if Decimal(r['models'][m]['net']) != expected: raise ValueError('settlement mismatch: '+fid)
        u = unknown(f, model['spec'])
        unknown_totals['unknown_hitters'] += sum(v for v in u[::2] if v is not None)
        unknown_totals['unknown_starters'] += sum(v for v in u[1::2] if v is not None)
        unknown_totals['missing_lineups'] += sum(v is None for v in u[::2])
        unknown_totals['missing_starters'] += sum(v is None for v in u[1::2])
        for k,v in f['features'].items(): miss[k] += v['value'] is None
        hist_group = history_by_selection['selected103' if r['in_selected_103'] else 'remaining']
        hist_group['fixtures'] += 1
        for side in SIDES:
            team = f['raw_features'][side+'_team_strength']['value'] or []
            history['team_side_count'] += 1
            history['team_records'] += len(team)
            hist_group['team_records'] += len(team)
            hist_group['team_records_previous_season'] += sum(str(z['date'])[:4] != r['date'][:4] for z in team)
            history['team_records_previous_season'] += sum(str(z['date'])[:4] != r['date'][:4] for z in team)
            history['team_length_'+str(len(team))] += 1
            lineup = f['raw_features'][side+'_lineup_strength']['value'] or []
            for player in lineup:
                h = player['history'] or []
                history['hitter_slots'] += 1
                hist_group['hitter_slots'] += 1
                hist_group['hitter_no_history'] += not h
                hist_group['hitter_history_records'] += len(h)
                hist_group['hitter_records_previous_season'] += sum(str(z['date'])[:4] != r['date'][:4] for z in h)
                history['hitter_no_history'] += not h
                history['hitter_history_records'] += len(h)
                history['hitter_history_length_'+str(len(h))] += 1
                history['hitter_records_previous_season'] += sum(str(z['date'])[:4] != r['date'][:4] for z in h)
        rest = [f['features'][s+'_rest_days']['value'] for s in SIDES]
        if any(v is not None and v>=100 for v in rest): samples.append({'fixture_id': fid, 'date':r['date'], 'home_rest':rest[0], 'away_rest':rest[1]})
        groups['month'][r['date'][:7]].append(r)
        groups['selection']['selected103' if r['in_selected_103'] else 'remaining'].append(r)
        groups['unknown_m1_roster'][group_unknown(u)].append(r)
        groups['rest']['missing' if any(v is None for v in rest) else '100_plus_any' if max(rest)>=100 else '2_plus_any' if max(rest)>=2 else '0_or_1'].append(r)
        for name in ('lineup_strength','starter_strength','bullpen_load'):
            groups[name+'_missing'][missing_group(f,name)].append(r)
        for m in MODELS:
            pick = r['models'][m]['pick']; a,b=r['prices'][pick],r['prices']['away' if pick=='home' else 'home']
            groups[m+'_price_role']['equal' if a==b else 'favorite' if a<b else 'underdog'].append(r)
    total = metric(rows)
    for m in MODELS:
        expected = replay['metrics']['all667'][m]
        if (total[m]['hits'] != expected['hits'] or total[m]['errors'] != expected['decided']-expected['hits'] or Decimal(total[m]['net_units']) != Decimal(expected['net_units']) or abs(total[m]['log_loss']-expected['decisive_log_loss'])>1e-12): raise ValueError('replay total mismatch: '+m)
    for key, replay_key in (('selected103','selected103'), ('remaining','remaining564')):
        subset = metric(groups['selection'].get(key, []))
        for m in MODELS:
            expected = replay['metrics'][replay_key][m]
            if subset['n'] != expected['bets'] or subset[m]['hits'] != expected['hits'] or Decimal(subset[m]['net_units']) != Decimal(expected['net_units']):
                raise ValueError('subset mismatch: '+key+':'+m)
    for dimension, cells in groups.items():
        if sum(len(v) for v in cells.values()) != len(rows): raise ValueError('partition mismatch: '+dimension)
    return {'total': total, 'slices': {k:{v:metric(rs) for v,rs in sorted(c.items())} for k,c in sorted(groups.items())},
            'missing_features': {k:{'missing':v,'denominator':len(rows),'rate':v/len(rows)} for k,v in sorted(miss.items())},
            'roster_unknown_counts':dict(unknown_totals), 'preserved_history_by_selection':{k:dict(v) for k,v in history_by_selection.items()}, 'preserved_history':dict(history), 'rest_100_plus_fixtures':samples}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('replay','features','labels','models','output'): p.add_argument('--'+name, type=Path, required=True)
    a=p.parse_args(); replay=json.loads(a.replay.read_text(encoding='utf8')); models=json.loads(a.models.read_text(encoding='utf8'))
    out=analyze(replay,index_gzip(a.features),index_gzip(a.labels),models['models']['m1_projected'])
    out['input_sha256']={k:digest(getattr(a,k)) for k in ('replay','features','labels','models')}
    out['limitations']='Repeatedly inspected retrospective sample; no historical pregame receipt proof, independent blind evaluation, causality, or operational vote performance.'
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf8')
if __name__=='__main__': main()
