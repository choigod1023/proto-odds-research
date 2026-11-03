"""Join collected lineups to frozen research files by official ID, not live picks."""
import hashlib,json,math,subprocess
from collections import Counter
from pathlib import Path

B=Path(__file__).resolve().parent
REPO=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/nested-model-search-20261102')
SEARCH='experiments/nested-search-20261102/'
SOURCE_COMMIT='248de71574694e5a3c1395bd0ed2111115543a8b'
SOURCES={'features':('prepared/outer-features.jsonl','9a9ca5e69ba80fe5fccc93b27add93411ac883c64e53494c17f2016b12236ff7'),
         'research_predictions':('outer-predictions/predictions.jsonl','628f110e906714585d74561aafe8f010dbefa7f147c1d28b008dcc4ef1aeea91'),
         'later_price_candidates':('later-price-audit/fixture-candidates.jsonl','9bfc2f2e0f2aaaa29cef93578eef3120add81baad1e0d1bbbfaf8d3030aa670f')}

def read_source(name):
    p,h=SOURCES[name];raw=subprocess.check_output(['git','show',SOURCE_COMMIT+':'+SEARCH+p],cwd=REPO)
    if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('research source hash mismatch: '+name)
    rows=[json.loads(x) for x in raw.decode('utf8').splitlines() if x.strip()]
    id_key='official_fixture_id' if name=='later_price_candidates' else 'id'
    if len({r[id_key] for r in rows})!=len(rows):raise ValueError('duplicate source ID: '+name)
    return {r[id_key]:r for r in rows}

def calculate(plan,fixtures,features,predictions,later_candidates=None):
    later_candidates=later_candidates or {}
    target={r['fixture_id']:r for r in plan['targets']};successful=[];mismatches=[]
    for r in fixtures:
        if not r['fixture_id'].startswith('kleague:2026:1:'):continue
        x=target.get(r['fixture_id']); sides=r['lineups'];score=r['scores']
        if x is None or (r['day'],sides[0]['team_id'],sides[1]['team_id'],score['home'],score['away'])!=(x['day'],x['home_team_id'],x['away_team_id'],x['home_goal'],x['away_goal']):
            mismatches.append(r['fixture_id']);continue
        successful.append(r)
    if len({r['fixture_id'] for r in successful})!=len(successful):raise ValueError('duplicate lineup ID')
    detail=[]
    for r in successful:
        f=features.get(r['fixture_id']);pred=predictions.get(r['fixture_id']);match=False;price=False;research_frozen=False
        if f:
            match=(f['day'],f['home_id'],f['away_id'])==(r['day'],r['lineups'][0]['team_id'],r['lineups'][1]['team_id'])
            try:
                v=f['prices'];price=match and isinstance(v,list) and len(v)==3 and all(math.isfinite(float(x)) and float(x)>1 for x in v)
            except (ValueError,TypeError,KeyError):price=False
            research_frozen=bool(price and pred and pred.get('day')==r['day'] and pred.get('prices')==f['prices'])
        candidate=later_candidates.get(r['fixture_id']);schedule=target[r['fixture_id']];candidate_price=False
        if candidate and (candidate.get('home'),candidate.get('away'),str(candidate.get('kickoff_local',''))[:10])==(schedule['home'],schedule['away'],r['day']) and str(candidate.get('kickoff_local',''))[11:16]==schedule.get('kickoff_local_display'):
            try:
                vectors=candidate.get('price_vectors_dom_order',[])
                candidate_price=bool(vectors and all(len(v)==3 and all(math.isfinite(float(z)) and float(z)>1 for z in v) for v in vectors))
            except (TypeError,ValueError):candidate_price=False
        detail.append({'fixture_id':r['fixture_id'],'research_identity_match':match,'archived_3way_price':price,'research_frozen_prediction':research_frozen,'later_price_candidate_unverified':candidate_price})
    n=len(successful);finished=plan['seasons']['2026']['completed'];total=plan['seasons']['2026']['official_scheduled_fixtures']
    return {'scheduled':total,'completed_in_schedule':finished,'not_completed_in_schedule':total-finished,
       'official_2026_lineups_with_22_starters_and_matching_result':n,'finished_lineup_unavailable_or_not_yet_collected':finished-n,
       'schedule_identity_or_result_mismatches':mismatches,'archived_3way_price_on_same_id':sum(x['archived_3way_price'] for x in detail),
       'research_frozen_predictions_on_same_id':sum(x['research_frozen_prediction'] for x in detail),
       'later_archived_price_candidates_on_same_id_unverified':sum(x['later_price_candidate_unverified'] for x in detail),
       'union_archived_price_or_later_candidate_unverified':sum(x['archived_3way_price'] or x['later_price_candidate_unverified'] for x in detail),
       'operating_frozen_prediction_intersection':None,'operating_frozen_reason':'current policy exact-ID mapping and source snapshot unavailable; historical evt_ ledger IDs are not official IDs',
       'historical_pregame_lineup_receipt_verified':0,'source_commit':SOURCE_COMMIT,'source_hashes':{k:v[1] for k,v in SOURCES.items()},
       'other_seasons':plan['seasons'],'details':detail,
       'limitations':['Archived prices and research predictions are retrospective, not verified purchase-time evidence or current operating frozen picks.', 'Official lineups were received now after completion; historical pregame publication and receipt remain unknown.', 'Player match statistics are not emitted as predictive features.']}

def main():
    plan=json.loads((B/'cohort-plan.json').read_text(encoding='utf8'));fixtures=[json.loads(x) for x in (B/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    result=calculate(plan,fixtures,read_source('features'),read_source('research_predictions'),read_source('later_price_candidates'))
    result['fixtures_sha256']=hashlib.sha256((B/'fixtures.jsonl').read_bytes()).hexdigest();result['plan_sha256']=hashlib.sha256((B/'cohort-plan.json').read_bytes()).hexdigest()
    (B/'coverage-2026.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print(json.dumps({k:v for k,v in result.items() if k not in ('details','other_seasons')},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
