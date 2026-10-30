"""Offline full-revision export; no ranking, ticket construction or ROI search."""
import sys, json, gzip, hashlib, math, inspect
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone

HELPER_ROOT=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030')
OUT=Path(__file__).resolve().parent
INPUT=HELPER_ROOT/'experiments/proto-all-picks-20261030/inputs'
PROTOCOL=OUT.parents[1]/'docs/research/2026-10-30-single-parlay-protocol.json'
sys.path.insert(0,str(HELPER_ROOT))
import scripts.proto_saved_offer_profitability as h

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def option_shape(r):
    identity=r.get('identity_join') or {}
    fixture=identity.get('fixture') or r.get('fixture') or {}
    family=r['market'];n=int(identity.get('n_way') or r['saved_market_roster_size'])
    line=h.line_key(family,r.get('line'),r.get('label',''))
    slot=h.SEL_NAMES[(family,n)].index(r['selection'])
    issued=None
    try:issued=h.issued_key(fixture['year'],fixture['round'],identity['game_no'])
    except (KeyError,TypeError,ValueError):pass
    return family,n,line,slot,issued,fixture

def outcome(r,shape,index,conflicts):
    family,n,line,slot,issued,fixture=shape
    identity=r.get('identity_join') or {}
    if not r.get('identity_complete') or not identity.get('identity_recomputation_pass') or issued is None:
        return dict(hit=None,refund=False,result_status='pending',non_evaluable_reason='no_verified_issued_identity')
    key=(*issued,family,line)
    if key in conflicts:
        return dict(hit=None,refund=False,result_status='conflict',non_evaluable_reason='conflicting_issued_results')
    label=index.get(key)
    if label is None:
        return dict(hit=None,refund=False,result_status='pending',non_evaluable_reason='no_exact_product_result')
    reason=None
    if label['n_way']!=n or label['event_id'] is not None and label['event_id']!=r['event_id']:reason='result_identity_mismatch'
    elif h.aware(r['kickoff']).replace(tzinfo=None).isoformat()!=label['kickoff']:reason='result_kickoff_mismatch'
    elif label['home'] is not None and (fixture.get('home')!=label['home'] or fixture.get('away')!=label['away']):reason='result_team_mismatch'
    if reason:return dict(hit=None,refund=False,result_status='conflict',non_evaluable_reason=reason)
    refund=label['status']=='refund'
    return dict(hit=None if refund else slot==label['winner'],refund=refund,result_status='settled',
                non_evaluable_reason=None,result_source=label['source'],official_result_label=label['result'],
                official_result_record_hash=label.get('official_result_record_hash'))

def extract(s,opts,index,conflicts,fixture):
    kickoff,captured,asof=h.aware(s['kickoff']),h.aware(s['captured_at']),h.aware(s['as_of'])
    observed=h.aware(s['market_observed_at']) if s.get('market_observed_at') else asof
    assert s['pregame_timing_pass'] and max(captured,asof,observed)<=kickoff
    assert observed<=captured and asof<=captured
    frame=dict(cohort='saved_pregame',snapshot_id=s['record_hash'],event_id=s['event_id'],kickoff=kickoff.isoformat(),
        available_at=max(captured,asof,observed).isoformat(),league=fixture.get('league'),home=fixture.get('home'),away=fixture.get('away'),
        availability_proven=True,availability_scope='internal_saved_time_only',choices=[],probability_models=[],
        captured_at=captured.isoformat(),as_of=asof.isoformat(),market_observed_at=observed.isoformat(),quote_observed_at=observed.isoformat(),
        saved_options_count=s['saved_options_count'],ledger_sequence=s['ledger_sequence'],
        source_file=s['source_file'],source_row_1based=s['source_row_1based'],
        model_status='uncalibrated_research_shadow_not_operating_policy')
    assert len(opts)==s['saved_options_count']
    groups=defaultdict(list);choices=[];shapes={}
    for r in opts:
        assert r['record_hash']==s['record_hash'] and r['event_id']==s['event_id']
        assert h.aware(r['kickoff'])==kickoff
        c=dict(offer_id=r['offer_id'],market=r['market'],label=r['label'],selection=r['selection'],line=r['line'],
            issue_year=None,round=None,game_no=None,odds=r['odds'],scores={},probability_models=[],
            hit=None,refund=False,result_status='pending',non_evaluable_reason='invalid_option_contract',
            source_option_index_0based=r['option_index_0based'],source_pointer=r['source_pointer'])
        try:
            shape=option_shape(r);family,n,line,slot,issued,fix=shape
            price=float(r['odds'])
            if not math.isfinite(price) or price<=1:raise ValueError('invalid_saved_price')
            if issued:c.update(issue_year=issued[0],round=issued[1],game_no=issued[2])
            else:c.update(issue_year=fix.get('year'),round=fix.get('round'))
            c.update(line=line,**outcome(r,shape,index,conflicts))
            # No outcomes enter this grouping or the probability-vector contract.
            q=dict(r,family=family,n_way=n,sport=fix.get('sport') or fixture.get('sport'),
                   score_contract=(s.get('score_forecast') or {}).get('contract'),slot=slot)
            groups[(family,line,n,issued)].append((q,c))
            shapes[r['offer_id']]=shape
        except (KeyError,TypeError,ValueError,OverflowError) as exc:
            c['non_evaluable_reason']='invalid_option_contract:'+str(exc)
        choices.append(c)
    for group in groups.values():
        group.sort(key=lambda pair:pair[0]['slot']);rows=[q for q,c in group]
        n=rows[0]['n_way']
        if len(rows)!=n or {r['slot'] for r in rows}!=set(range(n)):continue
        vectors={}
        for name,field,score in [('market','market_probability',False),('saved_final_reference','final_probability',False),('saved_score_shadow','score_model_probability',True)]:
            p=h.vector(rows,field,score)
            if p is not None:vectors[name]=p
        if 'market' in vectors and 'saved_score_shadow' in vectors:
            vectors['market90_score10_hypothesis']=[.9*m+.1*p for m,p in zip(vectors['market'],vectors['saved_score_shadow'])]
        for i,(_,c) in enumerate(group):
            c['scores']={name:probs[i] for name,probs in vectors.items()}
            c['probability_models']=list(c['scores'])
    frame['probability_models']=sorted({name for c in choices for name in c['scores']})
    frame['choices']=choices if frame['probability_models'] else []
    if not frame['choices']:frame['empty_reason']='saved_empty_roster' if not opts else 'no_complete_consistent_probability_vector'
    return frame

def main():
    sources=[INPUT/'combined-games.jsonl.gz',*(INPUT/'saved'/name for name in ['prediction-snapshots.jsonl.gz','all-offer-outcomes.jsonl.gz','issued-product-result-labels-post-20260909.jsonl.gz'])]
    hashes={str(p):sha(p) for p in sources+[PROTOCOL]}
    snapshots=h.load_jsonl(sources[1]);options=h.load_jsonl(sources[2]);extra=h.load_jsonl(sources[3])
    archives=[(str(sources[0]),r) for r in h.read_source(sources[0])]
    index,result_audit=h.result_index(archives,extra)
    # result_index omits conflicts; recover only the omitted keys of accepted labels.
    recognized=set()
    for source,raw in archives:
        try:
            r=h.parse_row(raw,source)
            if r['status']=='settled' or h.refund_label(r['family'],r['n_way'],r['sport'],r['result']):
                recognized.add((*h.issued_key(r['season'],r['round'],r['game_no']),r['family'],r['line']))
        except (KeyError,TypeError,ValueError):pass
    for r in extra:
        # Single-record helper applies exactly the canonical postledger acceptance rules.
        recognized.update(h.result_index([], [r])[0])
    conflicts=recognized-set(index)
    assert len(conflicts)==result_audit.get('conflicting_issued_results',0)
    by_hash=defaultdict(list);identity_candidates=defaultdict(dict)
    def add_fixture(event,kickoff,f):
        if all(f.get(k) for k in ('league','home','away','sport')):
            identity_candidates[(event,h.aware(kickoff).isoformat())][tuple(f[k] for k in ('league','home','away','sport'))]=f
    for s in snapshots:add_fixture(s['event_id'],s['kickoff'],s.get('fixture') or {})
    for r in options:
        by_hash[r['record_hash']].append(r)
        add_fixture(r['event_id'],r['kickoff'],(r.get('identity_join') or {}).get('fixture') or r.get('fixture') or {})
    assert len({s['record_hash'] for s in snapshots})==len(snapshots)
    assert set(by_hash)<=set(s['record_hash'] for s in snapshots)
    frames=[];audit=Counter();status=Counter();models=Counter();reasons=Counter()
    for s in snapshots:
        candidates=identity_candidates[(s['event_id'],h.aware(s['kickoff']).isoformat())]
        fixture=next(iter(candidates.values())) if len(candidates)==1 else (s.get('fixture') or {})
        f=extract(s,by_hash[s['record_hash']],index,conflicts,fixture);frames.append(f)
        audit['frames']+=1;audit['choices']+=len(f['choices']);audit['empty_frames']+=not bool(f['choices'])
        audit['source_empty_frames']+=not bool(by_hash[s['record_hash']])
        audit['frames_without_score_shadow']+='saved_score_shadow' not in f['probability_models']
        audit['missing_frame_team_identity']+=not all(f.get(k) for k in ('league','home','away'))
        for c in f['choices']:
            status[c['result_status']]+=1;models.update(c['scores'].keys());reasons.update([c['non_evaluable_reason']] if c.get('non_evaluable_reason') else [])
    frames.sort(key=lambda f:(f['available_at'],f['event_id'],f['snapshot_id']))
    data=b''.join((json.dumps(f,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode() for f in frames)
    (OUT/'events.jsonl.gz').write_bytes(gzip.compress(data,mtime=0))
    # Outcome blindness: erase the entire result index; selection inputs must be identical.
    for s in snapshots[::37]:
        original=next(f for f in frames if f['snapshot_id']==s['record_hash'])
        fixture={k:original[k] for k in ('league','home','away')}
        fixture['sport']=(s.get('fixture') or {}).get('sport')
        candidates=identity_candidates[(s['event_id'],h.aware(s['kickoff']).isoformat())]
        if len(candidates)==1:fixture=next(iter(candidates.values()))
        erased=extract(s,by_hash[s['record_hash']],{},set(),fixture)
        signature=lambda f:[(c['offer_id'],c['odds'],c['scores'],c['issue_year'],c['round'],c['game_no']) for c in f['choices']]
        assert signature(original)==signature(erased)
    assert len(frames)==len(snapshots)
    assert all(f['snapshot_id']==s['record_hash'] for f,s in zip(sorted(frames,key=lambda f:f['snapshot_id']),sorted(snapshots,key=lambda s:s['record_hash'])))
    assert all(c['hit'] is None and not c['refund'] for f in frames for c in f['choices'] if c['result_status']!='settled')
    assert hashes=={str(p):sha(p) for p in sources+[PROTOCOL]},'input mutated during export'
    local_modules={str(Path(m.__file__).resolve()) for m in list(sys.modules.values()) if getattr(m,'__file__',None) and str(Path(m.__file__).resolve()).startswith(str(HELPER_ROOT)) and str(m.__file__).endswith('.py')}
    local_modules.add(str(Path(__file__).resolve()))
    provenance=dict(created_at=datetime.now(timezone.utc).isoformat(),protocol='single-vs-low-parlay-v1',
        input_sha256=hashes,helper_code_sha256={p:sha(p) for p in sorted(local_modules)},
        output_sha256=sha(OUT/'events.jsonl.gz'),counts=dict(audit),choice_status=dict(status),model_choice_counts=dict(models),
        non_evaluable_reasons=dict(reasons),result_index_audit=result_audit,conflicting_product_keys=sorted(map(list,conflicts),key=str),
        source_snapshot_labels=dict(Counter(s['source_file'] for s in snapshots)),
        tests=dict(all_revisions_preserved=True,source_option_counts_match=True,pending_conflict_null_hit=True,
            source_hashes_stable=True,outcome_blindness_sample_frames=len(snapshots[::37])),
        limitations=['Internal saved-time evidence only; no independent availability certification.',
            'Entire saved features.options roster, not proof every operator-issued product was saved.',
            'No T-30/latest filter applied: consumer must apply frozen checkpoint protocol.',
            'All score shadows are uncalibrated; market90_score10 is not market90_vote10.',
            'No verified issued identity leaves settlement non-evaluable but does not suppress valid saved probability vectors.',
            'No score or vote fabricated; no ROI search, production calls, tracked changes or PR.',
            'Frame team metadata may use unique consistent static identity from other revisions of same event/kickoff; never results.',
            'Invalid/incomplete market vectors receive no model score; frames with no valid vectors retain empty choices.'])
    (OUT/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:provenance[k] for k in ['counts','choice_status','model_choice_counts','non_evaluable_reasons','tests','output_sha256']},ensure_ascii=False))

if __name__=='__main__':main()
