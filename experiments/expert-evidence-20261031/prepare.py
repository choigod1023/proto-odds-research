"""Build a retrospective, explicitly non-executable evidence inventory; no model fitting."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path

COHORT = 'retrospective_year_strict__claimed_time_only'

def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]

def stamp(value):
    if not isinstance(value, str): return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.utcoffset() is not None else None
    except ValueError: return None

def unique(rows, key):
    index = defaultdict(list)
    for row in rows: index[key(row)].append(row)
    return index

def valid_offer(target, case, frame):
    bridge = target.get('identity_bridge') or {}
    if not (target['league'] == case['league'] == frame['league'] and target['event_id'] == case['event_id'] == frame['event_id']): return False
    if bridge.get('official_fixture_id') != target['event_id'] or bridge.get('source_names') != [case['home'], case['away']]: return False
    if bridge.get('canonical_names') != [target['home'], target['away']] or [frame['home'],frame['away']] != [case['home'],case['away']]: return False
    if not (stamp(target['kickoff']) and stamp(target['kickoff']) == stamp(case['kickoff']) == stamp(frame['kickoff'])): return False
    if not (target['sides'] == case['sides'] and target['odds'] == case['odds'] and target['market_p'] == case['market_p'] and target['decision_at'] == case['decision_at']): return False
    if case.get('void'): return False
    choices = [x for x in frame.get('choices', []) if [x.get('issue_year'), x.get('round')] == case['issue'] and str(x.get('game_no')) == str(case['game_no'])]
    expected = target['sides']
    if len(choices) != len(expected) or [x.get('side') for x in choices] != expected: return False
    if [x.get('odds') for x in choices] != case['odds']: return False
    probabilities = [x.get('scores', {}).get('market') for x in choices]
    if any(not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not math.isclose(a, b, rel_tol=0, abs_tol=1e-12) for a, b in zip(probabilities, case['market_p'])): return False
    if any(x.get('line') is not None or not x.get('exact_offer_coordinates_saved') for x in choices): return False
    if len({x.get('offer_id') for x in choices}) != 1 or not choices[0].get('offer_id'): return False
    if any([x.get('issue_year'),x.get('round')] != case['issue'] or str(x.get('game_no')) != str(case['game_no']) for x in choices): return False
    if any(x.get('market') != choices[0].get('market') for x in choices): return False
    return True

def build(targets, frames, cases):
    by_f = unique(frames, lambda r:(r['league'],r['event_id']))
    by_c = unique(cases, lambda r:(r['cohort'],r['league'],r['event_id']))
    relevant = [x for x in targets if x.get('actual_votes') and x.get('cohort') == COHORT]
    by_t = unique(relevant, lambda r:(r['league'],r['event_id']))
    features, results, rejects = [], [], Counter()
    for target in relevant:
        key=(target['league'],target['event_id']); ff=by_f[key]; cc=by_c[(COHORT,*key)]
        if len(by_t[key])!=1 or len(ff)!=1 or len(cc)!=1:
            rejects['missing_or_duplicate_event'] += 1; continue
        f,c=ff[0],cc[0]
        if not valid_offer(target,c,f): rejects['identity_product_price_mismatch'] += 1; continue
        kickoff=stamp(target['kickoff']); decision=stamp(target['decision_at'])
        if not decision or decision > kickoff-timedelta(minutes=30): rejects['invalid_decision_or_t30'] += 1; continue
        choices=[x for x in f['choices'] if [x.get('issue_year'), x.get('round')] == c['issue'] and str(x.get('game_no')) == str(c['game_no'])]
        source_rows=f.get('raw_forecasts') or []
        counts=Counter((a.get('source'),a.get('author')) for a in source_rows)
        hash_counts=Counter(('document_sha256', a['document_sha256']) if a.get('document_sha256') else ('source_sha256', a.get('source_sha256')) for a in source_rows if a.get('document_sha256') or a.get('source_sha256'))
        authors=[]
        for a in source_rows:
            pub=stamp(a.get('source_reported_published_at')); observed=stamp(a.get('observed_at'))
            hash_value=a.get('document_sha256') or a.get('source_sha256')
            hash_type='document_sha256' if a.get('document_sha256') else ('source_sha256' if a.get('source_sha256') else None)
            valid_side=a.get('side') in target['sides']
            valid_identity=bool(a.get('source') and a.get('author'))
            authors.append({'source':a.get('source'),'author':a.get('author'),'side':a.get('side'),'source_url':a.get('source_url'),
                'published_claim':a.get('source_reported_published_at'),'saved_observed_at':a.get('observed_at'),
                'first_received_at':None,'document_hash_value':hash_value,'document_hash_kind':hash_type,
                'document_hash_verified':False,'external_receipt_verified':False,
                'same_hash_event':bool(hash_value and hash_counts[(hash_type, hash_value)] > 1),
                'duplicate_source_author_event':counts[(a.get('source'),a.get('author'))]>1,
                'claim_by_decision':bool(pub and pub<=decision),'observed_by_decision':bool(observed and observed<=decision),
                'research_claim_eligible':bool(valid_side and valid_identity and pub and pub<=decision and counts[(a.get('source'),a.get('author'))]==1),
                'availability_tier':'retrospective' if not observed or observed>decision else 'observed_by_decision_unverified'})
        excluded = sum(not row['research_claim_eligible'] for row in authors)
        if excluded: rejects['ineligible_author_rows'] += excluded
        authors = [row for row in authors if row['research_claim_eligible']]
        if not authors:
            rejects['no_eligible_author_event'] += 1
            continue
        quote_verified=all(x.get('quote_observed_at_verified') is True and stamp(x.get('quote_observed_at')) and stamp(x['quote_observed_at'])<=decision for x in choices)
        features.append({'event_id':target['event_id'],'league':target['league'],'source_names':[c['home'],c['away']],
            'canonical_names':[target['home'],target['away']],'kickoff':target['kickoff'],'decision_at':target['decision_at'],
            'issue':c['issue'],'game_no':c['game_no'],'case_product_id':c.get('product_id'),'offer_id':choices[0]['offer_id'],
            'offer_id_independently_verified':False,'market':choices[0]['market'],'line':None,'sides':target['sides'],
            'odds':target['odds'],'market_probability':target['market_p'],'quote_observed_at':f.get('quote_observed_at'),
            'quote_observed_by_decision_verified':bool(quote_verified),'price_evidence':target['price_evidence'],
            'overlaps_pr433_445':kickoff.year==2026,'authors':authors})
        results.append({'event_id':target['event_id'],'league':target['league'],'case_product_id':c.get('product_id'),
                        'outcome_index':target['outcome'],'case_outcome_index':c['outcome'],'outcome_reconciliation':'agree' if target['outcome']==c['outcome'] else 'disagree','sides':target['sides'],'result_evidence':f.get('result_evidence'),
                        'settlement_scope':f.get('settlement_scope'),'result_final_at':None})
    return features,results,dict(rejects)

def summary(features,rejects):
    groups=defaultdict(list)
    for event in features: groups[event['kickoff'][:4]].append(event)
    out={}
    for year, events in sorted(groups.items()):
        records=[a for e in events for a in e['authors']]; author_counts=Counter((a['source'],a['author']) for a in records if a['source'] and a['author'])
        docs=Counter((a['document_hash_kind'],a['document_hash_value']) for a in records if a['document_hash_value'])
        eligible=[a for a in records if a['research_claim_eligible']]
        out[year]={'events':len(events),'author_rows':len(records),'unique_source_authors':len(author_counts),
            'repeated_source_authors':sum(n>1 for n in author_counts.values()),'rows_by_repeated_source_authors':sum(n for n in author_counts.values() if n>1),
            'publisher_count':len({a['source'] for a in records if a['source']}),'publishers':dict(Counter(a['source'] for a in records)),
            'events_multiple_publishers':sum(len({a['source'] for a in e['authors'] if a['source']})>1 for e in events),
            'same_source_author_event_duplicate_rows':sum(a['duplicate_source_author_event'] for a in records),
            'same_hash_within_event_rows':sum(a['same_hash_event'] for a in records),
            'document_hash_present_rows':sum(bool(a['document_hash_value']) for a in records),'document_hash_verified_rows':sum(a['document_hash_verified'] for a in records),
            'hash_kind_rows':dict(Counter(a['document_hash_kind'] for a in records)),
            'distinct_hashes_by_kind':len(docs),'repeated_hash_rows':sum(n for n in docs.values() if n>1),
            'claim_by_decision_rows':sum(a['claim_by_decision'] for a in records),'claim_eligible_rows':len(eligible),
            'observed_by_decision_rows':sum(a['observed_by_decision'] for a in records),
            'externally_verified_receipts':sum(a['external_receipt_verified'] for a in records),
            'verified_quote_events':sum(e['quote_observed_by_decision_verified'] for e in events),
            'pr433_445_overlap_events':sum(e['overlaps_pr433_445'] for e in events)}
    return {'by_kickoff_year':out,'rejections':rejects,'event_count':len(features),
            'operational_pregame_eligible_events':sum(any(a['research_claim_eligible'] and a['observed_by_decision'] and a['external_receipt_verified'] for a in e['authors']) and e['quote_observed_by_decision_verified'] for e in features)}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--targets',type=Path,required=True); p.add_argument('--frames',type=Path,required=True); p.add_argument('--cases',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
    features,results,rejects=build(read(a.targets),read(a.frames),read(a.cases)); a.out.mkdir(parents=True,exist_ok=True)
    for name,rows in [('features.jsonl',features),('results.jsonl',results)]:
        (a.out/name).write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows),encoding='utf8',newline='\n')
    audit=summary(features,rejects); audit['input_sha256']={k:hashlib.sha256(getattr(a,k).read_bytes()).hexdigest() for k in ('targets','frames','cases')}
    (a.out/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n'); print(json.dumps(audit,ensure_ascii=True))
if __name__=='__main__': main()
