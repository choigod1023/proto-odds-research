"""Offline additional-source linking, chronology audit and fixed comparisons.

Imports research helpers only. Never changes a served pick, wallet, DB or cron.
Publication claims and current retrieval never become historical first receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit,parse_qsl,urlencode

import pregame_rd_compare as m
from rd_asof_evidence import is_hash, timestamp


def calendar_adapter(row):
    """Preserve original calendar name; prove offset equivalence on its dates.

    The old gate accepts UTC/KST. Japan date-only records have the same bound
    on these modern dates. We do not invent a completed-at timestamp.
    """
    row = json.loads(json.dumps(row))
    for meta in row.get('feature_evidence', {}).values():
        if meta.get('fact_date_timezone') != 'Asia/Tokyo':
            continue
        if meta.get('max_fact_time') is not None or not meta.get('max_fact_date'):
            continue  # A real aware completion time needs no calendar substitution.
        day = date.fromisoformat(meta['max_fact_date'])
        offsets = [datetime.combine(day + timedelta(days=i), time(), ZoneInfo('Asia/Tokyo')).utcoffset()
                   for i in (0, 1)]
        if offsets != [timedelta(hours=9)] * 2:
            raise ValueError('unsupported source calendar offset')
        meta.update(source_calendar_timezone='Asia/Tokyo', fact_date_timezone='Asia/Seoul',
                    calendar_adapter='verified_equal_UTC+09_bounds_only_not_observed_timestamp')
    return row


def feature_index(paths):
    index = defaultdict(list)
    sources = []
    for name, path in paths:
        sources.append(dict(feature_set=name, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for raw in m.load(path):
            row = calendar_adapter(raw)
            index[(name, row['cohort'], row['event_id'], row['kickoff'])].append(row)
    return index, sources


def coverage(cases, index, names):
    result = []
    for name in sorted(names):
        for view in ('captured_pregame', 'retrospective_lagged', 'retrospective_date_lagged'):
            accepted, audit = m.attach(cases, index, name, view, {})
            eligible = Counter((r['cohort'], r['league']) for r in accepted)
            both = Counter((r['cohort'], r['league']) for r in accepted
                           if any(n.startswith('home_') or '_home_' in n for n in r['features'])
                           and any(n.startswith('away_') or '_away_' in n for n in r['features']))
            result.append(dict(feature_set=name, evidence_view=view, audit=audit,
                               groups=[dict(cohort=c, league=l, accepted=n, both_sides=both[c, l])
                                       for (c, l), n in sorted(eligible.items())]))
    return result


def vote_direction(vote):
    return {'홈': 'home', '승': 'home', '원정': 'away', '패': 'away', '무': 'draw',
            'home': 'home', 'away': 'away', 'draw': 'draw'}.get(vote.get('selection'))


def document_identities(vote):
    identities=[]
    if is_hash(vote.get('document_sha256')):identities.append(('hash',vote['document_sha256']))
    try:
        u=urlsplit(vote.get('source_url') or '')
        if u.hostname:
            query=urlencode(sorted((k,val)for k,val in parse_qsl(u.query,keep_blank_values=True)
                                   if k.lower() not in ('page','utm_source','utm_medium','utm_campaign','utm_content','utm_term')))
            identities.append(('article',(u.hostname.casefold().removeprefix('www.'),u.path.rstrip('/'),query)))
    except (ValueError,TypeError):pass
    return identities


def admissible_vote(vote, case):
    if vote.get('event_id') != case['event_id'] or vote.get('league') != case['league']:
        return False, 'fixture_identity_mismatch'
    try:
        if timestamp(vote['kickoff']) != timestamp(case['kickoff']):
            return False, 'kickoff_mismatch'
        published = timestamp(vote['published_at_claim'])
        observed = timestamp(vote['observed_at'])
        if published > timestamp(case['decision_at']):
            return False, 'publication_claim_after_decision'
        if published > observed:
            return False, 'publication_claim_after_observation'
    except (KeyError, ValueError, TypeError):
        return False, 'missing_or_naive_source_time'
    if vote_direction(vote) not in case['sides']:
        return False, 'unsupported_market_direction'
    if vote.get('market') not in ('full_game_moneyline', '승패', '승무패'):
        return False, 'unsupported_market'
    if not str(vote.get('author') or '').strip() or not str(vote.get('source_site') or '').strip() or not vote.get('source_url'):
        return False, 'missing_author_or_source'
    try:
        parsed=urlsplit(vote['source_url'])
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            return False,'invalid_public_source_url'
    except (ValueError,TypeError):return False,'invalid_public_source_url'
    if not is_hash(vote.get('document_sha256')):
        return False, 'missing_document_hash'
    if not vote.get('join_evidence'):
        return False, 'missing_fixture_join_evidence'
    return True, 'publication_claim_only_receipt_not_historically_certified'


def document_components(votes):
    # Same article with a changed page/tracking parameter or response wrapper
    # is still one document. Hash copies across different URLs also count once.
    parents=list(range(len(votes)))
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    identities={}
    for i,v in enumerate(votes):
        keys=document_identities(v)
        if v.get('_raw_document_component') is not None:keys.append(('raw_component',v['_raw_document_component']))
        for identity in keys:
            if identity in identities:parents[find(i)]=find(identities[identity])
            else:identities[identity]=i
    return [find(i)for i in range(len(votes))]


def author_key(vote):
    try:
        host=urlsplit(vote.get('source_url')or '').hostname
        author=str(vote.get('author')or '').strip().casefold()
        return (host.casefold().removeprefix('www.'),author)if host and author else None
    except (ValueError,TypeError):return None


def author_components(votes):
    """Preserve every document's author membership, including later revisions."""
    authors={author_key(v)for v in votes if author_key(v)is not None}
    parents={a:a for a in authors}
    def find(a):
        while parents[a]!=a:parents[a]=parents[parents[a]];a=parents[a]
        return a
    shared={}
    for component,v in zip(document_components(votes),votes):
        a=author_key(v)
        if a is None:continue
        keys=[('document',component)]
        if v.get('_raw_author_component')is not None:keys.append(('raw_author',v['_raw_author_component']))
        for key in keys:
            if key in shared:parents[find(a)]=find(shared[key])
            else:shared[key]=a
    return [find(author_key(v))if author_key(v)is not None else None for v in votes]


def independent_votes(votes):
    """Per-fixture input only. One copied document, then one source/author.

    No inference that different aliases are different real people. Conflicting
    document directions or simultaneous first author directions fail closed.
    """
    audit = Counter();documents=defaultdict(list)
    doc_components=document_components(votes);actors=author_components(votes)
    for component,v in zip(doc_components,votes):documents[component].append(v)
    conflicting=set()
    for component,group in documents.items():
        if len({vote_direction(v) for v in group}) != 1:
            audit['conflicting_document_rows_excluded'] += len(group)
            conflicting.add(component)
        else:audit['copied_document_rows_omitted'] += len(group) - 1
    authors = defaultdict(list)
    actor_by_author={}
    for component,actor,v in zip(doc_components,actors,votes):
        if component not in conflicting:
            authors[author_key(v)].append(v);actor_by_author[author_key(v)]=actor
    eligible=defaultdict(list)
    for group in authors.values():
        first_time = min(timestamp(v['observed_at']) for v in group)
        first = [v for v in group if timestamp(v['observed_at']) == first_time]
        if len({vote_direction(v) for v in first}) != 1:
            audit['conflicting_first_author_rows_excluded'] += len(group)
            continue
        chosen=sorted(first,key=lambda v:(v['document_sha256'],v['source_url']))[0]
        eligible[actor_by_author[author_key(chosen)]].append(chosen)
        audit['later_or_duplicate_author_rows_omitted'] += len(group) - 1
    result=[]
    for group in eligible.values():
        # Copies of one author's later posts cannot acquire fresh voting slots
        # by changing which alias is selected as the document representative.
        if len({vote_direction(v)for v in group})!=1:
            audit['dependent_author_cluster_conflict']+=len(group);continue
        result.append(min(group,key=lambda v:(timestamp(v['observed_at']),v['source_url'])))
        audit['dependent_author_alias_votes_omitted']+=len(group)-1
    return result, dict(audit)


def share(votes, sides):
    counts = Counter(vote_direction(v) for v in votes)
    return [counts[s] / len(votes) for s in sides] if votes else None


def compare_votes(cases, old_votes, new_votes):
    old = defaultdict(list)
    new = defaultdict(list)
    for v in old_votes:
        old[v.get('event_id')].append(v)
    for v in new_votes:
        new[v.get('event_id')].append(v)
    rows = [];policy_rows=[]
    audit = Counter()
    all_old_identities={identity for v in old_votes for identity in document_identities(v)}
    audit['new_rows_matching_preexisting_article_or_hash']=sum(any(i in all_old_identities for i in document_identities(v))for v in new_votes)
    for case in cases:
        if case['auxiliary'] or not case['cohort'].endswith('__claimed_time_only'):
            continue
        accepted = []
        raw=old[case['event_id']]+new[case['event_id']]
        raw=[{k:val for k,val in v.items()if k not in ('_raw_document_component','_raw_author_component')}for v in raw]
        components=document_components(raw);actors=author_components(raw)
        component_position=0
        for group in (old[case['event_id']], new[case['event_id']]):
            valid = []
            for vote in group:
                component=components[component_position];actor=actors[component_position];component_position+=1
                ok, reason = admissible_vote(vote, case)
                audit[reason] += 1
                if ok:
                    valid.append(dict(vote,_raw_document_component=component,_raw_author_component=actor))
            accepted.append(valid)
        original, notes = independent_votes(accepted[0]); audit.update(notes)
        original_share = share(original, case['sides'])
        baseline = case.get('external_p')
        if original_share is None or baseline is None:
            audit['missing_old_electorate'] += 1; continue
        if max(abs(a-b) for a, b in zip(original_share, baseline)) > 1e-6:
            audit['old_electorate_disagrees_with_frozen_baseline'] += 1; continue
        union, notes = independent_votes(accepted[0] + accepted[1]); audit.update(notes)
        union_share = share(union, case['sides'])
        old_hashes = {v['document_sha256'] for v in original}
        added = [v for v in union if v['document_sha256'] not in old_hashes]
        novel=[v for v in added if not any(i in all_old_identities for i in document_identities(v))]
        entry=dict(case=case, old_vote=original_share, new_vote=union_share,
                         old_documents=len(original), new_documents=len(union),
                         added_documents=len(added),previously_unseen_documents=len(novel), receipt_certified=False)
        policy_rows.append(entry)
        if union_share is None:
            audit['extended_no_valid_documents']+=1;continue
        if union_share.count(max(union_share)) != 1:
            audit['extended_vote_tie_no_selection'] += 1; continue
        rows.append(entry)
    results = []
    for league in sorted({r['case']['league'] for r in rows}):
        selected = [r for r in rows if r['case']['league'] == league]
        cases_ = [r['case'] for r in selected]
        arms = {
            'market_reference': [r['market_p'] for r in cases_],
            'old_fixed91': [[.9*p+.1*v for p, v in zip(r['case']['market_p'], r['old_vote'])] for r in selected],
            'expanded_fixed91': [[.9*p+.1*v for p, v in zip(r['case']['market_p'], r['new_vote'])] for r in selected],
        }
        for label in ('old', 'new'):
            arms[label+'_vote_direction_only'] = [[float(i == max(range(len(r[label+'_vote'])), key=lambda j: r[label+'_vote'][j]))
                                                 for i in range(len(r[label+'_vote']))] for r in selected]
        for arm, probabilities in arms.items():
            measured = m.metrics(cases_, probabilities)
            if arm.endswith('direction_only'):
                measured['brier'] = measured['logloss'] = None
            results.append(dict(league=league, model=arm, metrics=measured,
                                added_document_cases=sum(r['added_documents'] > 0 for r in selected),
                                previously_unseen_document_cases=sum(r['previously_unseen_documents']>0 for r in selected),
                                changed_vote_directions=sum(max(range(len(r['old_vote'])), key=lambda i:r['old_vote'][i]) != max(range(len(r['new_vote'])), key=lambda i:r['new_vote'][i]) for r in selected),
                                status='retrospective_claimed_times_no_new_model_selection',
                                delta_vs_old91=m.paired_delta(cases_, probabilities, arms['old_fixed91'])))
    policy_results=[]
    for league in sorted({r['case']['league']for r in policy_rows}):
        opportunities=[r for r in policy_rows if r['case']['league']==league]
        for arm in ('old_vote_direction_only','new_vote_direction_only','old_fixed91','expanded_fixed91'):
            source='old_vote'if arm.startswith('old')else 'new_vote'
            selected=[r for r in opportunities if source=='old_vote'or (r[source] and r[source].count(max(r[source]))==1)]
            probabilities=[]
            for r in selected:
                if arm.endswith('direction_only'):
                    pick=max(range(len(r[source])),key=lambda i:r[source][i])
                    probabilities.append([float(i==pick)for i in range(len(r[source]))])
                else:probabilities.append([.9*p+.1*v for p,v in zip(r['case']['market_p'],r[source])])
            measured=m.metrics([r['case']for r in selected],probabilities)
            if arm.endswith('direction_only'):measured['brier']=measured['logloss']=None
            policy_results.append(dict(league=league,model=arm,opportunities=len(opportunities),placed=len(selected),
                abstained=len(opportunities)-len(selected),metrics=measured,
                opportunity_budget_roi_pct=100*measured.get('net_krw',0)/(len(opportunities)*m.BUDGET),
                semantics='Same opportunity universe; tie abstention is zero exposure, not a fabricated win/refund'))
    return dict(audit=dict(audit), matched_cases=len(rows), results=results,policy_results=policy_results,
                cases=[dict(event_id=r['case']['event_id'], league=r['case']['league'], kickoff=r['case']['kickoff'],
                            old_vote=r['old_vote'], new_vote=r['new_vote'], added_documents=r['added_documents'],
                            previously_unseen_documents=r['previously_unseen_documents']) for r in rows])


def paper_audit(path):
    rows = json.loads(path.read_text(encoding='utf-8'))
    bets = [r for r in rows if r.get('type') == 'bet']
    settled = {r['ticket']: r for r in rows if r.get('type') == 'settlement'}
    current = [r for r in bets if r.get('account') == 'tipster-vote-single-v1']
    return dict(records=len(rows), bets=len(bets), settled_tickets=len(settled),
                current_policy_bets=len(current), current_policy_settled=sum(r['ticket'] in settled for r in current),
                latest_placement=max((r['at'] for r in bets), default=None),
                contains_full_counterfactual_market_rosters=False,
                use='source and timing coverage only; selected-leg journal is not a complete comparison ledger',
                production_or_budget_changed=False)


def inverse_price_prior(row):
    values=[1/v for v in row['odds']]
    return [v/sum(values) for v in values]


def consistent_price_offset(cases,index,name,view):
    """A new arm: identical price-prior definition in BOTH source domains.

    Original saved probabilities remain untouched in every comparison baseline.
    This does not turn an archive quote into an execution-certified price.
    """
    data,_=m.attach(cases,index,name,view,{})
    results=[];fits=[];predictions=[]
    groups=sorted({(r['cohort'],r['league'],len(r['sides']))for r in data})
    for cohort,league,k in groups:
        rows=sorted([r for r in data if (r['cohort'],r['league'],len(r['sides']))==(cohort,league,k)],
                    key=lambda r:(timestamp(r['kickoff']),r['event_id']))
        dates=sorted({r['day']for r in rows})
        if len(dates)<5:continue
        for frac in ((.6,.2,.2),(.5,.2,.3)):
            a=max(1,int(len(dates)*frac[0]));b=max(a+1,int(len(dates)*(frac[0]+frac[1])))
            if b>=len(dates):continue
            val_start=min(timestamp(r['decision_at'])for r in rows if r['day']in dates[a:b])
            primary=[r for r in rows if r['day']in dates[:a] and r['outcome']is not None
                     and timestamp(r['kickoff'])<val_start-timedelta(hours=48)]
            auxiliary=[]
            if not rows[0]['auxiliary']:
                auxiliary=[r for r in data if r['auxiliary'] and r['league']==league and len(r['sides'])==k
                           and r['day']not in dates and r['outcome']is not None
                           and timestamp(r['kickoff'])<val_start-timedelta(hours=48)]
            train=primary+auxiliary;test=[r for r in rows if r['day']in dates[b:]]
            transformed_train=[dict(r,market_p=inverse_price_prior(r))for r in train]
            transformed_test=[dict(r,market_p=inverse_price_prior(r))for r in test]
            model=m.fit_model(transformed_train,offset=True)
            control=m.fit_model(transformed_train,offset=True,intercept=True)
            base=dict(cohort=cohort,league=league,feature_set=name,evidence_view=view,
                      model='market_feature_residual_consistent_price',split='-'.join(str(round(x*100))for x in frac),
                      train_n=len(train),train_primary_n=len(primary),train_aux_n=len(auxiliary),test_n=len(test),
                      test_start=dates[b],test_end=dates[-1],strict_asof_training_certified=False,
                      offset_basis='same normalized inverse saved product odds for training and test',
                      status='evaluated'if len(test)>=10 else 'insufficient_heldout_events_descriptive_only')
            fits.append(dict(**base,fit=model,price_intercept_control_fit=control,
                             train_ids=[r['product_id']for r in train],test_ids=[r['product_id']for r in test]))
            if not model or model['status']!='fitted':continue
            if not all(set(r['features'])&set(model['preprocessing']['names'])for r in test):continue
            probabilities=m.predict(transformed_test,model)
            old=[r['market_p']for r in test];references={'original_market':m.metrics(test,old),
                  'consistent_inverse_price_market':m.metrics(test,[r['market_p']for r in transformed_test])}
            control_probabilities=None
            if control and control['status']=='fitted':
                control_probabilities=m.predict(transformed_test,control)
                references['consistent_price_intercept_control']=m.metrics(test,control_probabilities)
            baseline91=None
            if all(r['external_p']is not None for r in test):
                baseline91=[[.9*p+.1*v for p,v in zip(r['market_p'],r['external_p'])]for r in test]
                references['original_actual91']=m.metrics(test,baseline91)
            results.append(dict(**base,metrics=m.metrics(test,probabilities),baselines=references,
                                delta_vs_market=m.paired_delta(test,probabilities,old),
                                delta_vs_price_intercept=m.paired_delta(test,probabilities,control_probabilities)if control_probabilities else None,
                                delta_vs_actual91=m.paired_delta(test,probabilities,baseline91)if baseline91 else None,
                                eligible_single=m.eligible_single_metrics(test,probabilities),two_leg=m.two_leg(test,probabilities)))
            for j,(r,p) in enumerate(zip(test,probabilities)):
                predictions.append(dict(cohort=cohort,league=league,feature_set=name,evidence_view=view,
                     split=base['split'],model=base['model'],event_id=r['event_id'],product_id=r['product_id'],
                     kickoff=r['kickoff'],probabilities=p,odds=r['odds'],outcome=r['outcome'],
                     price_intercept_probabilities=control_probabilities[j]if control_probabilities else None))
    return dict(results=results,fits=fits,predictions=predictions)


def fixed_later_period(cases,index,name,frozen_path):
    frozen=json.loads(frozen_path.read_text(encoding='utf-8'))
    context=frozen['fit_context'];model=frozen['model']
    if model.get('status')!='fitted'or model.get('offset')is not True:
        raise ValueError('unsupported frozen model contract')
    attached,audit=m.attach(cases,index,name,'retrospective_date_lagged',{})
    selected=[];excluded=Counter()
    prior_products=set(context['train_ids'])|set(context['test_ids'])
    for row in attached:
        if row['league']!=context['league']or row['cohort']!=context['cohort']:
            excluded['wrong_fixed_model_scope']+=1;continue
        if len(row['sides'])!=model['k']:
            excluded['wrong_number_of_outcomes']+=1;continue
        if row['day']!=timestamp(row['kickoff']).astimezone(m.KST).date().isoformat():
            excluded['case_day_kickoff_mismatch']+=1;continue
        if row['day']<=context['test_end']or row['product_id']in prior_products:
            excluded['not_after_frozen_pilot_or_overlapping_product']+=1;continue
        if not set(row['features'])&set(model['preprocessing']['names']):
            excluded['no_feature_in_frozen_contract']+=1;continue
        selected.append(row)
    selected=sorted(selected,key=lambda r:(timestamp(r['kickoff']),r['event_id']))
    if not selected:
        return dict(model_refit=False,events=0,audit=audit,excluded=dict(excluded)),[]
    transformed=[dict(r,market_p=inverse_price_prior(r))for r in selected]
    probabilities=m.predict(transformed,model)  # Deliberately NO fit_model call.
    market=[r['market_p']for r in selected]
    old91=[[.9*p+.1*v for p,v in zip(r['market_p'],r['external_p'])]for r in selected]
    vote=[[float(i==max(range(len(r['external_p'])),key=lambda j:r['external_p'][j]))for i in range(len(r['sides']))]for r in selected]
    vote_metrics=m.metrics(selected,vote);vote_metrics['brier']=vote_metrics['logloss']=None
    result=dict(model_refit=False,events=len(selected),dates=len({r['day']for r in selected}),
                first_date=selected[0]['day'],last_date=selected[-1]['day'],audit=audit,excluded=dict(excluded),
                frozen_model_sha256=hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
                strict_asof_training_certified=False,
                status='later_chronological_research_not_pristine_blind_trial',
                metrics=m.metrics(selected,probabilities),baselines={'original_market':m.metrics(selected,market),
                     'original_actual91':m.metrics(selected,old91),'original_vote_direction':vote_metrics},
                delta_vs_actual91=m.paired_delta(selected,probabilities,old91),
                eligible_single=m.eligible_single_metrics(selected,probabilities),two_leg=m.two_leg(selected,probabilities))
    predictions=[dict(event_id=r['event_id'],product_id=r['product_id'],kickoff=r['kickoff'],probabilities=p,
                      outcome=r['outcome'],odds=r['odds'],source_evidence=r['feature_evidence'])for r,p in zip(selected,probabilities)]
    return result,predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True, type=Path)
    parser.add_argument('--feature-file', action='append', default=[])
    parser.add_argument('--old-votes', type=Path)
    parser.add_argument('--new-votes', type=Path)
    parser.add_argument('--paper-journal', type=Path)
    parser.add_argument('--frozen-later-model',type=Path)
    parser.add_argument('--later-feature-file',action='append',default=[],type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases, dedup = m.deduplicate_cases(m.load(args.cases))
    paths = [(n, Path(p)) for n, p in (s.split('=', 1) for s in args.feature_file)]
    index, sources = feature_index(paths)
    names = {n for n, _ in paths}
    m.save(args.output/'coverage.json', coverage(cases, index, names))
    results, fits, predictions = [], [], []
    for name in sorted(names):
        for view in ('captured_pregame', 'retrospective_lagged', 'retrospective_date_lagged'):
            run = m.run(cases, index, name, view, {})
            results.extend(run['results']); fits.extend(run['fits']); predictions.extend(run['predictions'])
            extra=consistent_price_offset(cases,index,name,view)
            results.extend(extra['results']);fits.extend(extra['fits']);predictions.extend(extra['predictions'])
    m.save(args.output/'feature-results.json', results)
    m.save(args.output/'fits.json', fits)
    m.save(args.output/'predictions.jsonl.gz', predictions, True)
    if args.old_votes and args.new_votes:
        m.save(args.output/'vote-comparison.json', compare_votes(cases, m.load(args.old_votes), m.load(args.new_votes)))
    if args.paper_journal:
        m.save(args.output/'paper-source-audit.json', paper_audit(args.paper_journal))
    if args.later_feature_file:
        if not args.frozen_later_model:parser.error('--later-feature-file requires --frozen-later-model')
        later_index,_=feature_index([('later_official_baseball',p)for p in args.later_feature_file])
        later,predictions_=fixed_later_period(cases,later_index,'later_official_baseball',args.frozen_later_model)
        m.save(args.output/'later-period-fixed-model.json',later)
        m.save(args.output/'later-predictions.jsonl.gz',predictions_,True)
    inputs = [args.cases] + [p for n, p in paths] + [p for p in (args.old_votes, args.new_votes, args.paper_journal) if p]
    inputs+=args.later_feature_file+([args.frozen_later_model]if args.frozen_later_model else [])
    m.save(args.output/'manifest.json', dict(version='source-link-step2-v1', production_changed=False,
           research_monthly_loss_cap=None, cases=len(cases), case_audit=dedup, feature_sources=sources,
           inputs=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in inputs],
           scripts={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    (Path(__file__), Path(m.__file__))}, runtime=dict(python=sys.version.split()[0],
                    numpy=m.np.__version__,scipy=__import__('scipy').__version__),result_rows=len(results)))
    print(json.dumps(dict(cases=len(cases), feature_sets=sorted(names), result_rows=len(results))))


if __name__ == '__main__':
    main()
