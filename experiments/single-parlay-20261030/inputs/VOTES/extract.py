"""Offline year-matched vote/market frames; all writes stay beside this script.

No ROI, ticket construction, network, production imports, refitting, or tracked edits.
"""
import ast
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf8')
BASE = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')
OUT = Path(__file__).resolve().parent
assert OUT == BASE/'single-vs-low-odds-parlays-20261030/.local-test/VOTES'
YEAR_ROOT = BASE/'year-model-hit-comparison-20261029'
YEAR = YEAR_ROOT/'experiments/year-model-hit-20261029'
UP = BASE/'tipster-year-validation-20261028'
UP_DATA = UP/'experiments/tipster-year-20261028'
AUDIT = BASE/'league-loss-cap-ablation-20261030'
KST = timezone(timedelta(hours=9))
sources = {}


def source(path):
    raw = path.read_bytes()
    sources[path.as_posix()] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return raw


def read(path):
    text = source(path).decode('utf-8-sig')
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def selected(path, names, env):
    """Execute only explicitly selected pure definitions, with no module imports."""
    tree = ast.parse(source(path).decode('utf8'))
    nodes = [n for n in tree.body if
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for target in n.targets for t in ast.walk(target)))
             or (isinstance(n, ast.FunctionDef) and n.name in names)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)


env = dict(re=re, math=math, Counter=Counter, defaultdict=defaultdict)
selected(UP/'scripts/evaluate_tipster_expansion.py', {'team_key'}, env)
selected(UP/'scripts/join_tipster_expansion.py', {'ALIASES', 'canonical'}, env)
selected(UP/'scripts/collect_tipster_year_nba.py', {'NAMES'}, env)
selected(UP/'scripts/tipster_year_aliases.py', {'NBA', 'MLB', 'configure'}, env)
env['configure']()
canonical = env['canonical']
selected(YEAR_ROOT/'src/devig.py', {'_TOL', '_MAX_ITER', '_validated_odds', 'implied',
         'multiplicative', 'shin'}, env)
shin = env['shin']
# Independently rebuild saved year event eligibility/votes, without executing the
# original reporting/ROI code or importing any application runtime.
env['market_probabilities'] = shin
from datetime import date
env['date'] = date
selected(YEAR_ROOT/'scripts/compare_year_model_accuracy.py',
         {'START', 'END', 'MODELS', 'LEAGUES', 'valid_prices', 'events_from_rows'}, env)


def physical_key(r):
    return (r['league'], r.get('match_date') or r.get('date') or r['kickoff'][:10],
            canonical(r['home'], r['league']), canonical(r['away'], r['league']))


def forecast_key(r):
    return (r['league'], r['fixture_id'], r['source'], r.get('author'), r['source_url'], r['side'])


variants = {}
validation = dict(checks={}, exclusions={}, raw_counts={})
for variant in ('strict', 'standard-ot'):
    path = YEAR/f'{variant}.jsonl'
    rows = read(path)
    ledger = read(YEAR/f'{variant}-results.json')['event_ledger']
    rebuilt, excluded, official_groups = env['events_from_rows'](rows)
    assert rebuilt == ledger, variant
    validation['checks'][variant+'_rebuilt_event_ledger_exact_match'] = len(rebuilt)
    validation['exclusions'][variant] = excluded
    validation['raw_counts'][variant] = dict(rows=len(rows), official_groups=official_groups,
                                           priced_events=len(rebuilt))
    groups = defaultdict(list)
    for i, r in enumerate(rows, 1):
        groups[(r['league'], r['fixture_id'])].append((i, r))
    accepted_path = UP_DATA/('results.json' if variant == 'strict' else 'results-standard-ot.json')
    accepted = read(accepted_path)['accepted']
    rich_index = defaultdict(list)
    for i, r in enumerate(accepted):
        if all(k in r for k in ('fixture_id', 'source', 'source_url', 'side')):
            rich_index[forecast_key(r)].append((i, r))
    variants[variant] = dict(rows=rows, ledger=ledger, groups=groups, path=path,
                             accepted_path=accepted_path, rich_index=rich_index)

strict_ids = {(r['league'], r['fixture_id']) for r in variants['strict']['ledger']}
selected_events = [('strict', e) for e in variants['strict']['ledger']]
selected_events += [('standard-ot', e) for e in variants['standard-ot']['ledger']
                    if e['league'] == 'NBA' and (e['league'], e['fixture_id']) not in strict_ids]
assert len(selected_events) == 597
assert len({physical_key(e) for _, e in selected_events}) == 597

# The same primary offer file was used in the upstream yearly exact join. Do not
# add other prices from later archives or infer round numbers from dates.
offer_path = UP_DATA/'prices/offers.jsonl'
offerbank = defaultdict(list)
for i, r in enumerate(read(offer_path), 1):
    offerbank[physical_key(r)].append((i, r))

frames = []
used_rows = Counter()
zero_alternatives = 0
enriched_rows = 0
round_ambiguities = []
for variant, e in selected_events:
    v = variants[variant]
    batch = v['groups'][(e['league'], e['fixture_id'])]
    directions = defaultdict(set)
    for _, r in batch:
        if r.get('side') in e['order']:
            directions[(r['source'], r.get('author') or '<publisher>')].add(r['side'])
    counts = Counter(next(iter(s)) for s in directions.values() if len(s) == 1)
    voters = sum(counts.values())
    assert voters == e['voters'] and dict(counts) == e['vote_counts']
    conflicts = [list(k) for k, s in directions.items() if len(s) != 1]
    matches = [(i, o) for i, o in offerbank[physical_key(e)]
               if o['odds'] == e['prices'] and o['odds_order'] == e['order']]
    assert matches, e['fixture_id']
    offers_by_coordinate = defaultdict(list)
    for i, o in matches:
        assert o['market'] == 'full_game_moneyline'
        # This is validation after the identity/odds match, never an identity key.
        assert {(r['home_score'], r['away_score']) for _, r in batch} == {(o['home_score'], o['away_score'])}
        coordinate = (o.get('raw_year'), o.get('raw_round'), o.get('raw_game_no'))
        offers_by_coordinate[coordinate].append((i, o))
    if len(offers_by_coordinate) > 1:
        round_ambiguities.append(e['fixture_id'])
    raw_forecasts = []
    aware_official = []
    for i, r in batch:
        rich = v['rich_index'].get(forecast_key(r), [])
        assert rich, ('missing_raw_forecast_provenance', forecast_key(r))
        for j, original in rich:
            preserved = {k: value for k, value in original.items() if any(token in k for token in
                         ('observ', 'captur', 'publish', 'cutoff', 'pregame', 'sha256', 'price_quality', 'odds_time_quality', 'settlement_scope_assumption'))}
            preserved.update(source=original['source'], author=original.get('author'),
                             source_url=original['source_url'], side=original['side'],
                             raw_input_file=v['path'].as_posix(), raw_row_1based=i,
                             source_file=v['accepted_path'].as_posix(), json_pointer=f'/accepted/{j}')
            raw_forecasts.append(preserved)
            enriched_rows += 1
        raw_time = r.get('kickoff')
        if raw_time:
            dt = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
            if dt.utcoffset() is not None:
                aware_official.append(dt.astimezone(KST).isoformat())
    if aware_official:
        assert len(set(aware_official)) == 1
        kickoff = aware_official[0]
        precision, time_basis = 'aware_timestamp', 'saved_official_fixture_kickoff'
    else:
        saved_archive = {o.get('kickoff') for _, o in matches if o.get('kickoff')}
        valid_archive = {datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(KST).isoformat()
                         for s in saved_archive if datetime.fromisoformat(s.replace('Z', '+00:00')).utcoffset() is not None}
        if len(valid_archive) == 1:
            kickoff = next(iter(valid_archive))
            precision, time_basis = 'aware_timestamp', 'saved_retrospective_proto_archive_kickoff'
        else:
            kickoff = e['match_date']+'T12:00:00+09:00'
            precision, time_basis = 'date_only_canonical_noon', 'synthetic_canonical_time_not_observed'
    dt = datetime.fromisoformat(kickoff)
    assert dt.date().isoformat() == e['match_date']
    cohort = ('retrospective_year_strict' if variant == 'strict'
              else 'retrospective_year_standard_ot_nba_increment')
    digest = hashlib.sha256(json.dumps([cohort, e['league'], e['fixture_id'],
                 sources[v['path'].as_posix()]['sha256']], separators=(',', ':')).encode()).hexdigest()[:20]
    p_market = shin(e['prices'])
    shares = [counts[side]/voters for side in e['order']]
    p91 = [round(.9*p+.1*s, 4) for p, s in zip(p_market, shares)]
    assert e['order'][max(range(len(p_market)), key=p_market.__getitem__)] == e['picks']['market']
    assert e['order'][max(range(len(p91)), key=p91.__getitem__)] == e['picks']['market90_vote10']
    choices = []
    for coordinate, records in sorted(offers_by_coordinate.items(), key=lambda x: str(x[0])):
        issue_year, issue_round, game_no = coordinate
        proof = records[0][1]
        # offer_id is an explicitly derived coordinate key; never claim that it
        # was an operator-issued ID or independently verified purchase offer.
        complete = all(x is not None for x in coordinate)
        offer_id = f'proto:{issue_year}:{issue_round}:{game_no}:{proof["market"]}' if complete else None
        for j, side in enumerate(e['order']):
            support = shares[j]
            if support == 0:
                zero_alternatives += 1
            refund = e['winner'] == 'draw' and len(e['order']) == 2
            hit = not refund and side == e['winner']
            choices.append(dict(
                offer_id=offer_id, choice_id=(offer_id+':'+side if offer_id else None),
                offer_id_kind='derived_key_from_saved_proto_coordinates',
                offer_id_independently_verified=False,
                market=proof['market'], market_family='moneyline', label=side, side=side, line=None,
                odds=e['prices'][j], issue_year=issue_year,
                round=issue_round if issue_round is not None else 'unknown', game_no=game_no,
                round_known=issue_round is not None, exact_offer_coordinates_saved=complete,
                raw_offer_price_index=j, odds_order=e['order'],
                scores=dict(market=p_market[j], market90_vote10=p91[j], vote_support=support),
                vote_count=counts[side], vote_share=support, vote_support_eligible=support > 0,
                eligible_models=['market', 'market90_vote10']+(['vote_support'] if support > 0 else []),
                unsupported_models=['vote_support'] if support == 0 else [],
                probability_models=['market', 'market90_vote10'],
                probability_calibrated=False, vote_support_is_probability=False,
                status='refund' if refund else 'hit' if hit else 'miss', hit=hit, refund=refund,
                result_known=True, result_evidence='official_exact_fixture',
                quote_observed_at=None, quote_observed_at_verified=False,
                archive_captured_at=proof.get('observed_at'), availability_proven=False,
                executable=False, proto_offer_evidence='archived_exact_fixture',
                offer_proofs=[dict(source_file=offer_path.as_posix(), source_row_1based=i,
                                   raw_offer=o) for i, o in records]))
    raw_rounds = sorted({o['raw_round'] for _, o in matches if o.get('raw_round') is not None})
    raw_years = sorted({o['raw_year'] for _, o in matches if o.get('raw_year') is not None})
    raw_nos = sorted({o['raw_game_no'] for _, o in matches if o.get('raw_game_no') is not None})
    frame = dict(schema='single_parlay_event_frame_v1', cohort=cohort,
        event_id=e['fixture_id'], league=e['league'], home=e['home'], away=e['away'],
        home_canonical=canonical(e['home'], e['league']), away_canonical=canonical(e['away'], e['league']),
        match_date=e['match_date'], kickoff=kickoff, kickoff_precision=precision,
        kickoff_source=time_basis, source_official_kickoff=e.get('kickoff'),
        available_at=(dt-timedelta(hours=24)).isoformat(),
        available_at_semantics='hypothetical_kickoff_minus_24h_not_source_observation',
        availability_proven=False, retrospective=True, executable=False,
        quote_observed_at=None, quote_observed_at_verified=False,
        snapshot_id='retrospective-votes-'+digest,
        source_file=v['path'].as_posix(), source_variant=variant,
        source_result_file=(YEAR/f'{variant}-results.json').as_posix(),
        probability_models=['market', 'market90_vote10'],
        model_semantics=dict(market='Uncalibrated Shin market-implied estimate computed from actual saved Proto full price vector',
            market90_vote10='Uncalibrated estimate: round(0.9 * Shin_market + 0.1 * actual_author_vote_share, 4); no renormalization',
            vote_support='Actual unambiguous source/author vote fraction; ranking support only, never calibrated p or EV; eligible iff > 0'),
        model_calibrated=dict(market=False, market90_vote10=False, vote_support=False),
        probability_computation='transparent_recomputation_from_actual_odds_and_votes; not_saved_operating_forecast',
        vote_support_eligibility_rule='score > 0', voters=voters,
        vote_counts={side:counts[side] for side in e['order']},
        conflicting_source_authors_excluded=conflicts,
        source_plurality_pick=e['picks']['tipster_vote'], source_plurality_tie=e['picks']['tipster_vote'] is None,
        original_model_picks={k:e['picks'][k] for k in ['market', 'market90_vote10', 'tipster_vote']},
        issue_year=raw_years[0] if len(raw_years) == 1 else 'unknown',
        round=raw_rounds[0] if len(raw_rounds) == 1 else 'unknown',
        game_no=raw_nos[0] if len(raw_nos) == 1 else None,
        round_known=len(raw_rounds) == 1, round_ambiguous_multiple_saved_offers=len(raw_rounds) > 1,
        saved_issue_rounds=[dict(issue_year=y, round=r, game_no=g) for y,r,g in offers_by_coordinate],
        round_inference_from_date=False, distinct_physical_event=True,
        home_score=batch[0][1]['home_score'], away_score=batch[0][1]['away_score'],
        winner=e['winner'], result_known=True, result_evidence='official_exact_fixture',
        result_source_urls=sorted({r['result_source_url'] for _, r in batch}),
        settlement_scope='regular_time_90min' if len(e['order']) == 3 else 'full_game_including_overtime',
        settlement_scope_assumptions=sorted({r['settlement_scope_assumption'] for _,r in batch if r.get('settlement_scope_assumption')}),
        raw_forecasts=raw_forecasts, choices=choices,
        limitations=['All first captures are retrospective; publication claims do not prove pregame availability.',
                     'No actual quote receipt, ticket execution, historical single-buy designation, or settlement time is authenticated.',
                     'Scores of vote-unsupported price directions remain zero and are ineligible for pure vote selection.',
                     'Multiple archived issue/round offers are preserved as separate choices for the same physical event; no duplicate-event ticket legs.'])
    frames.append(frame)
    used_rows[variant] += len(batch)

frames.sort(key=lambda f: (f['kickoff'], f['league'], f['event_id']))
assert len(frames) == len({physical_key(f) for f in frames}) == len({f['snapshot_id'] for f in frames})
assert all(f['availability_proven'] is False and f['executable'] is False for f in frames)
assert all('vote_support' not in f['probability_models'] for f in frames)
assert all(c['scores']['vote_support'] == c['vote_count']/f['voters']
           and c['vote_support_eligible'] == (c['scores']['vote_support'] > 0)
           for f in frames for c in f['choices'])
assert all(datetime.fromisoformat(f['kickoff'])-datetime.fromisoformat(f['available_at']) == timedelta(hours=24) for f in frames)
payload = ''.join(json.dumps(f, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n' for f in frames).encode('utf8')
compressed = gzip.compress(payload, mtime=0)
(OUT/'events.jsonl.gz').write_bytes(compressed)


def coverage(sample):
    choices = [c for f in sample for c in f['choices']]
    pairs = sorted({(c['issue_year'], c['round']) for c in choices if c['round_known']})
    return dict(frames=len(sample), unique_physical_events=len({physical_key(f) for f in sample}),
        choices=len(choices), preserved_price_directions=sum(len(set(c['label'] for c in f['choices'])) for f in sample),
        first_date=min(f['match_date'] for f in sample), last_date=max(f['match_date'] for f in sample),
        dates=len({f['match_date'] for f in sample}), source_rows=sum(len(f['raw_forecasts']) for f in sample),
        pluralities_tied=sum(f['source_plurality_tie'] for f in sample),
        multi_author_events=sum(f['voters'] > 1 for f in sample),
        kickoff_basis=dict(Counter(f['kickoff_source'] for f in sample)),
        exact_round_choices=sum(c['round_known'] for c in choices),
        unknown_round_choices=sum(not c['round_known'] for c in choices),
        frame_single_round=sum(f['round_known'] for f in sample),
        frame_ambiguous_multiple_saved_rounds=sum(f['round_ambiguous_multiple_saved_offers'] for f in sample),
        issue_rounds=[dict(issue_year=y, round=r) for y,r in pairs],
        unsupported_zero_vote_choices=sum(c['scores']['vote_support'] == 0 for c in choices))


# Audit sources are inspected only to bound overlap and avoid substituting a
# market-only archive or learned model for genuine author votes. Parent's latest
# scope fixes this export to the already-matched yearly sample.
audit_paths = [AUDIT/'experiments/league-loss-cap-20261030/source-audits/MLB-sources-and-models.json',
               AUDIT/'experiments/league-loss-cap-20261030/source-audits/NPB-manifest.json',
               AUDIT/'experiments/league-loss-cap-20261030/source-audits/KBO-normalization-checks.json']
for path in audit_paths:
    read(path)
kbo = read(AUDIT/'.local-test/KBO/cohorts.json')['records']
npb = read(AUDIT/'.local-test/NPB/events.json')
year_keys = {physical_key(f) for f in frames}
audit_review = dict(kbo_vote_events=sum(bool(r.get('voters')) for r in kbo),
    kbo_additional_physical_events=sum(physical_key(r) not in year_keys for r in kbo if r.get('voters')),
    npb_vote_events=len(npb), npb_additional_physical_events=sum(physical_key(r) not in year_keys for r in npb),
    npb_additional_not_exported='Five retrospective expansion events outside parent latest year-only export scope',
    mlb='old116 is already covered; external194 market/learned heads have no actual tipster input',
    archive_only='Separate parent worker; omitted here')
summary = coverage(frames)
validation['checks'].update(unique_physical_events=len(frames), complete_proto_offer_match_events=len(frames),
    raw_forecasts_enriched=enriched_rows, raw_rows_used=dict(used_rows),
    vote_counts_exact_match=True, market_and_fixed91_saved_picks_exact_match=True,
    zero_vote_scores_preserved=zero_alternatives, zero_vote_pure_eligibility_rejected=True,
    quote_observed_at_all_null=True, retrospective_nonexecutable_all=True,
    source_proven_available_events=0, no_roi_computed=True)
validation['checks']['source_hashes_unchanged'] = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == m['sha256'] for p,m in sources.items())
assert validation['checks']['source_hashes_unchanged']
manifest = dict(schema='single_parlay_vote_export_manifest_v1', output=(OUT/'events.jsonl.gz').as_posix(),
    sha256=hashlib.sha256(compressed).hexdigest(), decompressed_sha256=hashlib.sha256(payload).hexdigest(),
    compressed_bytes=len(compressed), summary=summary,
    by_cohort={k:coverage([f for f in frames if f['cohort']==k]) for k in sorted({f['cohort'] for f in frames})},
    by_league={k:coverage([f for f in frames if f['league']==k]) for k in sorted({f['league'] for f in frames})},
    source_paths=sources, audit_review=audit_review,
    round_ambiguity_event_ids=round_ambiguities,
    export_scope='strict full priced event ledger + nonoverlapping standard-ot NBA increment only; no archive-only or learned-model cohorts',
    probability_models=['market', 'market90_vote10'],
    model_definitions=frames[0]['model_semantics'],
    offer_identity='Derived from saved raw_year/raw_round/raw_game_no/market; no invented operator ID; full raw offer proofs retained per choice',
    selection_notes=['Tied-vote events remain; direction support >0 is the pure vote eligibility gate, while source plurality tie is separately flagged.',
                     'Offer reissues are separate choices in a single physical-event frame. Frame round is unknown when multiple actual saved rounds exist.',
                     'No date-based round inference, no source-proven placement times, no calibrated vote p/EV, no ROI or ticket simulation.'])
for name, value in [('manifest.json', manifest), ('validation.json', validation)]:
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
lines = ['# Year-matched tipster/market/fixed91 offline dataset', '',
         f'- Frames / physical events: {len(frames)}. Strict: 508; additional NBA standard-OT: 89.',
         f'- Dates (KST): {summary["first_date"]}–{summary["last_date"]}; {summary["dates"]} observed dates.',
         f'- Choices: {summary["choices"]}; unique event/direction pairs: {summary["preserved_price_directions"]}.',
         f'- Actual known-round choices: {summary["exact_round_choices"]}; unknown-round choices: {summary["unknown_round_choices"]}.',
         f'- Frames with one round: {summary["frame_single_round"]}; frames with multiple saved issue rounds: {summary["frame_ambiguous_multiple_saved_rounds"]}.',
         f'- Zero-vote choices: {zero_alternatives}; all retain zero score and are ineligible for pure vote selection.',
         '- Every frame is retrospective and nonexecutable. available_at is hypothetical kickoff minus 24h; availability_proven=false; quote_observed_at=null.',
         '- Saved official aware kickoff is used where available; otherwise saved aware Proto archive kickoff; canonical noon would be explicitly marked if needed.',
         '- market is Shin(actual archived odds); market90_vote10 is round(0.9*market+0.1*actual author vote share,4), matching saved choices. Both are uncalibrated estimates.',
         '- vote_support is actual source/author support, never calibrated p/EV. probability_models excludes it. No ROI was calculated.',
         '- Offer IDs are derived coordinate keys, explicitly not independently authenticated operator IDs. Raw offer records, capture times, publication claims and cutoff flags are preserved.',
         '- Standard-OT increment assumes overtime inclusion without original-bookmaker verification and is a distinct cohort.', '', '## Coverage', '']
for league, d in manifest['by_league'].items():
    lines.append(f'- {league}: {d["frames"]} events; {d["first_date"]}–{d["last_date"]}; {len(d["issue_rounds"])} actual year/round pairs.')
lines += ['', '## Exact sources', '']
for p in sources:
    lines.append(f'- [{Path(p).name}]({p}) — SHA256 `{sources[p]["sha256"]}`')
lines += ['', '## Validation and limits', '',
          '- Python rebuilt the 508/597 saved event ledgers exactly from actual raw forecasts; deduplicated by league/date/canonical teams; checked source/author counts, saved model directions, prices, official results, positive-support eligibility, source hashes and 24h proxy offset.',
          '- Older loss-cap audit contributes no rows here: KBO198 and MLB116 overlap; five additional NPB expansion events were identified but excluded under the parent’s latest year-only scope.',
          '- Results rely on existing official-result joins, not a new independent operator audit. Raw archive scores are checked against those results after matching; outcomes never determine identity, ranking or vote eligibility.',
          '- No source-proven publication/quote availability, execution, real settlement time or fresh unseen holdout is established.',
          '- Full coverage, exact issue-round lists, source hashes and checks are in manifest.json and validation.json.']
(OUT/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf8')
print(json.dumps(dict(output=manifest['output'], sha256=manifest['sha256'], summary={k:v for k,v in summary.items() if k!='issue_rounds'},
     by_league={k:dict(frames=v['frames'], first=v['first_date'], last=v['last_date'], issue_rounds=len(v['issue_rounds'])) for k,v in manifest['by_league'].items()},
     validation=validation['checks']), ensure_ascii=False, indent=2))
