"""Read-only MLB source replay and normalizer export; writes only beside this file."""
import collections, copy, gzip, hashlib, json, math, socket, sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030')
SOURCE_ROOT = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/baseball-information-stepchange-20261029')
SOURCE = SOURCE_ROOT/'experiments/baseball-information-stepchange-20261029/mlb'
BRIDGE = ROOT/'data/model_candidates/mlb'
OUT = ROOT/'.local-test/MLB'
assert Path(__file__).resolve().parent == OUT.resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT/'src'))
from mlb_frozen_candidate import infer_mlb_candidate, load_mlb_artifacts

def read(path):
    if path.name.endswith('.jsonl.gz'):
        with gzip.open(path, 'rt', encoding='utf8') as h: return [json.loads(l) for l in h if l.strip()]
    if path.name.endswith('.jsonl'):
        return [json.loads(l) for l in path.read_text(encoding='utf8').splitlines() if l.strip()]
    return json.loads(path.read_text(encoding='utf8'))

def write(name, value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')

def sha(path, canonical=False):
    raw=path.read_bytes()
    if canonical and path.suffix!='.gz': raw=raw.replace(b'\r\n',b'\n')
    return hashlib.sha256(raw).hexdigest()

# No network access and no calls to source run()/fit()/write functions.
def no_network(*args,**kwargs): raise AssertionError('network forbidden in offline replay')
socket.create_connection=no_network
socket.socket=no_network
paths=[BRIDGE/n for n in ['parity-fixtures.jsonl.gz','manifest.json','model.json','priors-2024.json','priors-2025.json']]
paths += [SOURCE/n for n in ['predictions.jsonl','inputs.jsonl.gz','frozen-selection.json','validation-predictions.jsonl','results.json','PROTOCOL.md','execution-notes.json','sources/mlb-evaluation.jsonl','sources/mlb-external-features.jsonl','sources/mlb-snapshots.jsonl.gz','external-snapshots.jsonl.gz','sources/manifest.json']]
paths += [ROOT/'src/mlb_frozen_candidate.py',SOURCE_ROOT/'scripts/mlb_lineup_nonlinear.py']
fingerprints={str(p):sha(p) for p in paths}
fixtures=read(BRIDGE/'parity-fixtures.jsonl.gz')
manifest=read(BRIDGE/'manifest.json')
assert sha(BRIDGE/'parity-fixtures.jsonl.gz')==manifest['fixture_sha256']
for name,digest in manifest['source_files'].items(): assert sha(SOURCE/name,True)==digest,name
assert sha(SOURCE/'frozen-selection.json',True)==manifest['lineage']['frozen_selection_sha256']
frozen=read(SOURCE/'frozen-selection.json')
predictions=read(SOURCE/'predictions.jsonl')
by_id={r['gamePk']:r for r in predictions}
inputs={r['gamePk']:r for r in read(SOURCE/'inputs.jsonl.gz')}
oldrefs={r['gamePk']:r for r in read(SOURCE/'sources/mlb-evaluation.jsonl')}
snapshots=read(SOURCE/'sources/mlb-snapshots.jsonl.gz')
external=read(SOURCE/'external-snapshots.jsonl.gz')
validations=read(SOURCE/'validation-predictions.jsonl')
bundles={y:load_mlb_artifacts(y) for y in (2025,2026)}

# Blind inference first: fixture labels and stored expected predictions not passed to inference.
choices=[]
for f in fixtures:
    model,priors=bundles[int(f['snapshot']['start_at'][:4])]
    r=infer_mlb_candidate(f['snapshot'],decision_at=f['decision_at'],matchup=f['matchup'],odds=f['odds'],model=model,prior_tables=priors)
    assert r['status']=='available',r['reasons']
    mutated=copy.deepcopy(f['snapshot']);mutated['home_score']=999;mutated['away_score']=0
    r2=infer_mlb_candidate(mutated,decision_at=f['decision_at'],matchup=f['matchup'],odds=f['odds'],model=model,prior_tables=priors)
    assert r==r2
    choices.append(r)

# Independent scalar prediction for ALL 12 saved coefficient sets; no fitting/reselection.
def scalar_predict(raw, model):
    z=[max(-6.,min(6.,(a-c)/s)) for a,c,s in zip(raw,model['center'],model['scale'])]
    if model['family']=='hinge': z += [max(v,0.)**2 for v in z[:23]]+[max(-v,0.)**2 for v in z[:23]]
    logit=model['beta'][0]+sum(a*b for a,b in zip(z,model['beta'][1:]))
    return 1/(1+math.exp(-max(-40.,min(40.,logit))))

def settle(p,hg,ag,prices):
    pick='home' if p>=.5 else 'away'
    hit=None if hg==ag else ((hg>ag)==(pick=='home'))
    odds=prices[0 if pick=='home' else 1]
    net=0 if hit is None else int((Decimal(10000)*(Decimal(str(odds))-1)).quantize(Decimal(1),rounding=ROUND_HALF_UP)) if hit else -10000
    return pick,hit,odds,net

max_error=max_feature_error=max_all_candidate_error=max_val_error=0.
records=[]
ledger_checks=0
limits=['retrospective_previously_inspected_evaluation_not_pristine_unseen','actual_quote_receipt_not_proven','actual_settlement_time_not_saved','not_operating_model_replay']
for f,replay in zip(fixtures,choices):
    gp=f['matchup']['gamePk'];row=by_id[gp];lab=f['labels']
    assert row['announced'] and row['price_order']==['home','away']
    assert row['prices']==[f['odds']['home'],f['odds']['away']]
    assert (lab['hg'],lab['ag'])==(row['hg'],row['ag'])
    max_error=max(max_error,abs(replay['probability_home']-row['probabilities']['primary']))
    max_feature_error=max(max_feature_error,max(abs(a-b) for a,b in zip(replay['features'],f['expected']['features'])))
    assert replay['model_stage']==row['model_stage']
    base='old116' if row['cohort']=='old116' else 'external194'
    split='2026-07-01' if base=='old116' else '2026-08-10'
    period='before_'+split if row['date']<split else 'from_'+split
    probs={('candidate/'+n):p for n,p in row['candidate_probabilities'].items()}
    probs['market_only']=row['probabilities']['market_only']
    baseline='research_market90_vote10' if base=='old116' else 'market_only'
    if base=='old116':
        probs[baseline]=row['probabilities']['baseline']
        probs['tipster_vote']=oldrefs[gp]['probabilities']['tipster_vote']
        assert abs(probs[baseline]-(.9*probs['market_only']+.1*probs['tipster_vote']))<1e-12
    blend_model='research_market81_vote9_learned10' if base=='old116' else 'market90_learned10'
    probs[blend_model]=row['probabilities']['primary_blend10']
    assert abs(probs[blend_model]-(.9*row['probabilities']['baseline']+.1*row['probabilities']['primary']))<1e-12
    # Exact source ledger checks cover every saved head/reference, even omitted aliases.
    for policy,p in row['probabilities'].items():
        pick,hit,odds,net=settle(p,row['hg'],row['ag'],row['prices'])
        got={'pick':pick,'correct':int(hit is True),'push':int(hit is None),'net_krw':net}
        assert got==row['ledger'][policy],(gp,policy,got,row['ledger'][policy])
        ledger_checks+=1
    for name,p in row['candidate_probabilities'].items():
        calc=scalar_predict(inputs[gp]['features'],frozen['models'][name][row['model_stage']])
        max_all_candidate_error=max(max_all_candidate_error,abs(calc-p))
    for name,p in probs.items():
        pick,hit,odds,net=settle(p,row['hg'],row['ag'],row['prices'])
        is_candidate=name.startswith('candidate/')
        selected=name=='candidate/linear:l2=10'
        info={
          'cohort':base+'/evaluation','population':base,'phase':'evaluation','period':period,
          'league':'MLB','model':name,'event_id':'MLB:'+str(gp),'gamePk':gp,
          'kickoff':row['start_at'],'source_calendar_date':row['date'],'calendar_date_timezone':'Asia/Seoul',
          'pick':pick,'odds':odds,'net_per_unit':net/10000,'hit':hit,'refund':hit is None,
          'probability':p if pick=='home' else 1-p,'probability_home':p,
          'probability_kind':'saved_vote_fraction_not_calibrated_win_probability' if name=='tipster_vote' else 'saved_model_or_reference_probability',
          'home':f['matchup']['home'],'away':f['matchup']['away'],'home_score':row['hg'],'away_score':row['ag'],
          'source_file':str(SOURCE/'sources/mlb-evaluation.jsonl') if name=='tipster_vote' else str(SOURCE/'predictions.jsonl'),
          'price_source_file':str(SOURCE/'sources/mlb-evaluation.jsonl') if base=='old116' else str(SOURCE/'sources/mlb-external-features.jsonl'),
          'fixture_source_file':str(BRIDGE/'parity-fixtures.jsonl.gz'),
          'provenance_limits':limits+(['no_saved_historical_tipster_in_this_cohort'] if base=='external194' else ['tipster_vote_publication_time_not_audited_in_this_export']),
          'saved_original_cohort':row['cohort'],'frozen_primary':selected,
          'candidate_status':'validation_selected_primary_and_hit' if selected else 'exploratory_unselected_do_not_promote' if is_candidate else 'fixed_reference',
          'model_stage':row['model_stage'] if is_candidate else None,
          'baseline_definition':baseline if name==blend_model else None,
          'unseen_status':'retrospective_not_pristine_unseen',
          'odds_time_verified':False,'settlement_time_verified':False,
        }
        if name=='tipster_vote':
            info['vote_share_home']=info.pop('probability_home')
            info['vote_share_selected']=info.pop('probability')
            info['voters']=oldrefs[gp]['target']['voters']
            info['vote_counts']=oldrefs[gp]['target']['vote_counts']
        if is_candidate: info['training_cutoff_at']=bundles[int(row['start_at'][:4])][0]['payload']['models'][row['model_stage']]['training_cutoff_at']
        records.append(info)
assert max_error<1e-12 and max_feature_error<1e-12 and max_all_candidate_error<1e-12
for r in validations:
    for name,p in r['candidate_probabilities'].items():
        max_val_error=max(max_val_error,abs(scalar_predict(inputs[r['gamePk']]['features'],frozen['models'][name]['before2025'])-p))
assert max_val_error<1e-12
selection_rows=[dict(cohort='selection_2025',phase='selection',league='MLB',event_id='MLB:'+str(r['gamePk']),kickoff=r['start_at'],y=r['y'],candidate_probabilities_home=r['candidate_probabilities'],source_file=str(SOURCE/'validation-predictions.jsonl'),profitability_unavailable_reason='no_saved_validation_prices') for r in validations]
write('selection-probabilities-no-prices.json',selection_rows)
keys=[(r['cohort'],r['model'],r['event_id']) for r in records]
assert len(keys)==len(set(keys))
assert all(isinstance(r['odds'],(int,float)) and r['odds']>1 and isinstance(r['net_per_unit'],(int,float)) and math.isfinite(r['net_per_unit']) and datetime.fromisoformat(r['kickoff']).tzinfo is not None for r in records)
assert not any('settled_at' in r or 'available_at' in r for r in records)
a={r['event_id'] for r in records if r['population']=='old116'}
b={r['event_id'] for r in records if r['population']=='external194'}
assert len(a)==116 and len(b)==194 and not a&b
assert not (a|b)&{'MLB:'+str(i) for i in frozen['train_ids']+frozen['validation_ids']}
records.sort(key=lambda r:(r['cohort'],r['model'],r['kickoff'],r['event_id']))
assert set(r['cohort'] for r in records)=={'old116/evaluation','external194/evaluation'}
write('records.json',records)
for pop in ['old116','external194']: write('records-'+pop+'.json',[r for r in records if r['population']==pop])

def metrics(rs):
    net=sum(Decimal(str(r['net_per_unit'])) for r in rs);n=len(rs)
    ys=[int(r['home_score']>r['away_score']) for r in rs];ps=[r.get('probability_home') for r in rs]; calibrated=all(p is not None for p in ps)
    return dict(n=n,correct=sum(r['hit'] is True for r in rs),refunds=sum(r['refund'] for r in rs),net_units=float(net),stake_krw=10000*n,net_krw=int(net*10000),roi=float(net/n),kickoff_min=min(r['kickoff'] for r in rs),kickoff_max=max(r['kickoff'] for r in rs),mean_selected_odds=sum(r['odds'] for r in rs)/n,mean_selected_probability=sum(r['probability'] for r in rs)/n if calibrated else None,model_implied_mean_EV=sum(r['probability']*r['odds']-1 for r in rs)/n if calibrated else None,logloss=sum(-y*math.log(max(p,1e-12))-(1-y)*math.log(max(1-p,1e-12)) for y,p in zip(ys,ps))/n if calibrated else None,brier=sum((p-y)**2 for y,p in zip(ys,ps))/n if calibrated else None)

groups={}
for pop in ['old116','external194']:
    for model in sorted({r['model'] for r in records if r['population']==pop}):
        rs=[r for r in records if r['population']==pop and r['model']==model]
        groups[pop+'/all/'+model]=metrics(rs)
        for period in sorted({r['period'] for r in rs}): groups[pop+'/'+period+'/'+model]=metrics([r for r in rs if r['period']==period])
        for month in sorted({r['source_calendar_date'][:7] for r in rs}): groups[pop+'/month_'+month+'/'+model]=metrics([r for r in rs if r['source_calendar_date'].startswith(month)])
# Independently agree with saved verified summaries; no fallback236/230 blending.
source_results=read(SOURCE/'results.json')['groups']
for pop,old in [('old116','old116'),('external194','external230')]:
    for ours,theirs in [('candidate/linear:l2=10','primary'),('market_only','market_only'),('research_market81_vote9_learned10' if pop=='old116' else 'market90_learned10','primary_blend10')]+([('research_market90_vote10','baseline')] if pop=='old116' else []):
        a=groups[pop+'/all/'+ours];b=source_results[old+'/verified/all'][theirs]
        assert all(a[k]==b[k] for k in ['n','correct','net_krw']) and abs(a['roi']-b['roi'])<1e-14

splits={}
for role in ['train','validation','evaluation']:
    rr=[r for r in snapshots if r.get('accepted') and r['role']==role]
    splits[role]=dict(n=len(rr),kickoff_min=min(r['start_at'] for r in rr),kickoff_max=max(r['start_at'] for r in rr),ids=[r['gamePk'] for r in rr],profitability_prices_present=sum(bool(r.get('target',{}).get('prices')) for r in rr))

diagnostics={}
for pop in ['old116','external194']:
    rs=[r for r in records if r['population']==pop and r['model']=='candidate/linear:l2=10']
    base_model='research_market90_vote10' if pop=='old116' else 'market_only'
    bb={r['event_id']:r for r in records if r['population']==pop and r['model']==base_model}
    changed=[r for r in rs if r['pick']!=bb[r['event_id']]['pick']]
    overround=[1/by_id[r['gamePk']]['prices'][0]+1/by_id[r['gamePk']]['prices'][1]-1 for r in rs]
    diagnostics[pop]=dict(changed_picks=len(changed),changed_pick_delta_krw=int(sum(Decimal(str(r['net_per_unit']))-Decimal(str(bb[r['event_id']]['net_per_unit'])) for r in changed)*10000),mean_overround=sum(overround)/len(rs),primary_negative_model_EV_count=sum(r['probability']*r['odds']-1<=0 for r in rs),primary_model_EV_above_zero_count=sum(r['probability']*r['odds']-1>0 for r in rs),primary_home_picks=sum(r['pick']=='home' for r in rs),selected_probability_min=min(r['probability'] for r in rs),selected_probability_max=max(r['probability'] for r in rs))

check=dict(status='passed',fixture_games=len(fixtures),exact_source_ledger_checks=ledger_checks,primary_reinferred_from_raw_games=len(choices),all_saved_candidate_coefficient_checks=12*310,selection_coefficient_checks=12*300,outcome_mutation_invariant_games=310,max_primary_probability_error=max_error,max_primary_feature_error=max_feature_error,max_all_candidate_probability_error=max_all_candidate_error,max_selection_probability_error=max_val_error,duplicate_model_event_rows=0,cohort_overlap_games=0,selection_training_overlap_with_evaluation=0,actual_settlement_timestamps_exported=0,quote_receipt_timestamps_exported=0,no_network=True,no_training=True,no_cap_replay=True)
write('validation.json',check)
write('uncapped-summary.json',dict(stake_krw=10000,no_monthly_cap=True,settlement='single_game_Decimal_ROUND_HALF_UP_integer_KRW_tie_refund',groups=groups,diagnostics=diagnostics))
write('sources-and-models.json',dict(source_sha256=fingerprints,canonical_source_hashes_verified_against_bridge_manifest=True,primary='candidate/linear:l2=10',selected_heads=frozen['heads'],finite_candidate_count=12,selection=frozen['selection'],selection_validation_scores=frozen['validation'],source_splits=splits,accepted_external194=dict(n=194,requested=230,rejected=len(manifest['excluded']),rejections=manifest['excluded']),strict_unseen_game_count=0,retrospective_protocol=str(SOURCE/'PROTOCOL.md'),evaluation_seen_before_implementation_correction=read(SOURCE/'execution-notes.json')['evaluation_seen_before_implementation_correction'],cohort_record_counts=dict(collections.Counter(r['cohort'] for r in records)),model_definitions={'candidate/*':'12 frozen logistic models: linear/matchup/hinge x ridge0.01,0.1,1,10; choose home iff saved p_home >=0.5; only linear:l2=10 selected by 2025 validation; others diagnostic','market_only':'saved original market probability, NOT recomputed from current/re-exported prices','research_market90_vote10':'old116 only: 0.9*saved market p_home +0.1*saved tipster vote fraction','tipster_vote':'old116 only: probability omitted; original vote fractions retained as vote_share_home/vote_share_selected; not calibrated; no publication-time audit','research_market81_vote9_learned10':'old116: 0.9*research_market90_vote10 +0.1*primary = 0.81*market +0.09*vote +0.10*primary','market90_learned10':'external194: 0.9*market +0.1*primary; no tipster input'},aliases_not_duplicated={'primary':'candidate/linear:l2=10','hit':'candidate/linear:l2=10','hit_blend10':'research_market81_vote9_learned10 on old116; market90_learned10 on external194','external_baseline':'market_only'},provenance_limits=limits,selection_ROI_unavailable='2024 training and 2025 selection validation have no saved price pairs in these inputs; no fabricated profitability records'))
assert fingerprints=={str(p):sha(p) for p in paths},'read-only sources modified'
print(json.dumps({'output':str(OUT/'records.json'),'record_count':len(records),'cohorts':dict(collections.Counter(r['cohort'] for r in records)),'checks':check,'headline':{k:{z:v[z] for z in ['n','correct','net_krw','roi']} for k,v in groups.items() if '/all/' in k and not '/candidate/' in k or k in ['old116/all/candidate/linear:l2=10','external194/all/candidate/linear:l2=10']}},ensure_ascii=False,indent=2))
