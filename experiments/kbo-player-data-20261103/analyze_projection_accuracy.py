"""Descriptive KBO projection-vs-postgame-ID audit; never used for fitting."""
import gzip,hashlib,json,os
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
ROWS=HERE/'rows.jsonl'; LABELS=HERE/'labels.jsonl'; PROJECTIONS=HERE/'prior-projected-features.jsonl.gz'
INVENTORY=HERE/'inventory.json'; MANIFEST=HERE/'prior-projected-manifest.json'
PITCH=ROOT/'data/raw/detail/kbo_baseball_2023_2026.json';STARTERS=ROOT/'data/raw/kbo_starters.json'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(n,d):return n/d if d else None


def starter_status(row,side,pitchers,starters):
    gid=row['fixture_id']; actual=row['features'][side+'_actual_starter_assumed']['value']
    box=pitchers.get(gid); candidates=starters.get(gid,[])
    ps=((box or {}).get('data') or {}).get(side) or []
    if actual is not None:return 'available'
    if len(candidates)==1 and ps and ps[0].get('pcode') and candidates[0].get(side+'_starter') and candidates[0][side+'_starter']!=ps[0].get('name'):
        return 'source_name_conflict'
    return 'source_missing_or_unusable'


def compute(rows,projections,pitchers,starters):
    if len({r['fixture_id'] for r in rows})!=len(rows):raise ValueError('duplicate original fixture')
    if len({r['fixture_id'] for r in projections})!=len(projections):raise ValueError('duplicate projected fixture')
    byid={p['fixture_id']:p for p in projections}
    if set(byid)!={r['fixture_id'] for r in rows}:raise ValueError('fixture sets differ')
    yearly=defaultdict(Counter)
    for row in rows:
        s=yearly[str(row['season'])];projected=byid[row['fixture_id']]
        if projected['scenario']!='projected_from_prior_games':raise ValueError('wrong scenario')
        s['games']+=1;s['phase_unknown_games']+=1
        paired=[];correct=[]
        for side in ('home','away'):
            pred=projected['projection'][side+'_starter']['player_id']
            actual=row['features'][side+'_actual_starter_assumed']['value']
            status=starter_status(row,side,pitchers,starters)
            s['starter_side_opportunities']+=1;s['starter_actual_available']+=int(actual is not None)
            s['starter_actual_source_conflict']+=int(status=='source_name_conflict')
            s['starter_actual_source_missing_or_unusable']+=int(status=='source_missing_or_unusable')
            s['starter_projection_missing']+=int(pred is None)
            s['starter_both_missing']+=int(pred is None and actual is None)
            pair=pred is not None and actual is not None;hit=pair and pred==actual
            s['starter_compared_sides']+=int(pair);s['starter_matched_sides']+=int(hit)
            paired.append(pair);correct.append(hit)
            actual_lineup=row['features'][side+'_actual_lineup_assumed']['value']
            proposed=projected['projection'][side+'_lineup']['player_ids']
            for v in (actual_lineup,proposed):
                if v is not None and (len(v)!=9 or len(set(v))!=9):raise ValueError('lineup must have nine distinct IDs')
            s['lineup_side_opportunities']+=1;s['lineup_actual_9_missing']+=int(actual_lineup is None)
            s['lineup_projection_missing']+=int(proposed is None);s['lineup_both_missing']+=int(proposed is None and actual_lineup is None)
            if actual_lineup is not None and proposed is not None:
                s['lineup_compared_sides']+=1;s['lineup_actual_player_slots']+=9
                s['lineup_overlap_player_slots']+=len(set(actual_lineup)&set(proposed))
                s['lineup_exact_set_sides']+=int(set(actual_lineup)==set(proposed))
                s['lineup_exact_order_sides']+=int(actual_lineup==proposed)
            profiles=projected['features'][side+'_lineup_strength']['value']
            if proposed is None:
                if profiles is not None:raise ValueError('profiles without projected lineup')
            else:
                if not profiles or [x['player_id'] for x in profiles]!=proposed:raise ValueError('profile/lineup identity mismatch')
                s['projected_player_slots']+=len(profiles)
                for profile in profiles:
                    history=profile['history']
                    s['player_history_present']+=int(bool(history));s['player_history_missing']+=int(not history)
                    if history:
                        s['player_history_any_missing_ab_hit_bb_hr']+=int(any(x.get(k) is None for x in history for k in ('ab','hit','bb','hr')))
        s['starter_both_sides_compared_games']+=int(all(paired))
        s['starter_both_sides_match_games']+=int(all(correct))
    result={}
    for year,c in sorted(yearly.items()):
        d=dict(c);d.update(starter_side_match_rate=pct(c['starter_matched_sides'],c['starter_compared_sides']),
            starter_both_sides_match_rate=pct(c['starter_both_sides_match_games'],c['starter_both_sides_compared_games']),
            lineup_mean_set_overlap=pct(c['lineup_overlap_player_slots'],c['lineup_actual_player_slots']),
            lineup_exact_set_rate=pct(c['lineup_exact_set_sides'],c['lineup_compared_sides']),
            lineup_exact_order_rate=pct(c['lineup_exact_order_sides'],c['lineup_compared_sides']),
            player_history_coverage=pct(c['player_history_present'],c['projected_player_slots']))
        # Explicit zeros are material, not missing counters.
        for key in ('starter_actual_source_conflict','starter_actual_source_missing_or_unusable','starter_projection_missing','starter_both_missing','lineup_actual_9_missing','lineup_projection_missing','lineup_both_missing','player_history_missing','player_history_any_missing_ab_hit_bb_hr'):d.setdefault(key,0)
        result[year]=d
    return result


def main():
    before={x.name:sha(x) for x in (ROWS,LABELS,PROJECTIONS,INVENTORY,MANIFEST)}
    inventory=json.loads(INVENTORY.read_text(encoding='utf8')); manifest=json.loads(MANIFEST.read_text(encoding='utf8'))
    if before['rows.jsonl']!=inventory['output_sha256']['rows.jsonl'] or before['labels.jsonl']!=inventory['output_sha256']['labels.jsonl'] or before[PROJECTIONS.name]!=manifest['output_sha256']:raise ValueError('frozen input hash mismatch')
    for path,name in ((PITCH,'pitchers'),(STARTERS,'starters')):
        if sha(path)!=inventory['source_hashes'][name]:raise ValueError('source file hash mismatch')
    with ROWS.open(encoding='utf8') as f:rows=[json.loads(x) for x in f]
    with gzip.open(PROJECTIONS,'rt',encoding='utf8') as f:projected=[json.loads(x) for x in f]
    pitchers=json.loads(PITCH.read_text(encoding='utf8')); source=json.loads(STARTERS.read_text(encoding='utf8'));starters=defaultdict(list)
    for s in source:starters[s['gameId']].append(s)
    results=compute(rows,projected,pitchers,starters)
    after={x.name:sha(x) for x in (ROWS,LABELS,PROJECTIONS,INVENTORY,MANIFEST)}
    if before!=after:raise ValueError('frozen input changed')
    out={'schema_version':'kbo-projection-postgame-diagnostic-v1','status':'descriptive_diagnostic_only_no_training',
         'input_sha256':before,'source_sha256':{'pitchers':sha(PITCH),'starters':sha(STARTERS)},
         'seasons':results,'phase_counts':{'unknown':len(rows)},'point_in_time_verified':0,
         'definitions':{'starter_side_match_rate':'equal predicted and postgame first-pitcher IDs / both nonmissing team sides',
         'starter_both_sides_match_rate':'both home and away correct / games with both pairs available',
         'lineup_mean_set_overlap':'summed set intersections / (9 * comparable team lineups)',
         'lineup_exact_set_rate':'identical nine-ID sets / comparable team lineups',
         'lineup_exact_order_rate':'identical nine-ID sequence / comparable team lineups',
         'player_history_coverage':'projected lineup slots with at least one prior player history / all projected lineup slots'},
         'limitations':['Actual IDs are postgame reconstruction, not independent official confirmed starters/lineups.','Batting order takes first listed non-substitution entry in each slot; actual starting nine not independently established.','The history coverage denominator is projected slots, not unique individuals; generation from a previous lineup makes presence largely structural.','Prior-day completion is assumed; no actual contemporaneous receipt or independent price authentication.','No source field independently specifies season phase; all phase_unknown, despite yearly counts above 720.','No performance, model tuning, ROI, model input or operating change.']}
    path=HERE/'projection-accuracy.json';temp=HERE/'projection-accuracy.json.tmp';temp.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(temp,path)
    print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__':main()
