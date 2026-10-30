"""Independent checks of serialized frames; writes only private verification JSON."""
from collections import Counter
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf8')
OUT = Path(__file__).resolve().parent
manifest = json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
raw = (OUT/'events.jsonl.gz').read_bytes()
assert hashlib.sha256(raw).hexdigest() == manifest['sha256']
payload = gzip.decompress(raw)
assert hashlib.sha256(payload).hexdigest() == manifest['decompressed_sha256']
frames = [json.loads(l) for l in payload.decode('utf8').splitlines()]
assert len(frames) == len({(f['league'], f['match_date'], f['home_canonical'], f['away_canonical']) for f in frames}) == 597
assert Counter(f['cohort'] for f in frames) == Counter(retrospective_year_strict=508, retrospective_year_standard_ot_nba_increment=89)
assert all(f['league'] == 'NBA' for f in frames if f['cohort'].endswith('_increment'))
source_cache = {}
checks = Counter()
max_market_error = 0.0


def load(path):
    if path not in source_cache:
        t = Path(path).read_text(encoding='utf8')
        source_cache[path] = json.loads(t) if path.endswith('.json') else [json.loads(l) for l in t.splitlines() if l.strip()]
    return source_cache[path]


def independent_market(odds):
    q = [1/o for o in odds]
    total = math.fsum(q)
    if total <= 1:
        return [x/total for x in q]
    if len(q) == 2:
        # Closed form: two-way Shin equals additive margin removal.
        return [(1+q[0]-q[1])/2, (1+q[1]-q[0])/2]
    def vector(z):
        return [(math.sqrt(z*z+4*(1-z)*x*x/total)-z)/(2*(1-z)) for x in q]
    low, high = 1e-14, .9
    for _ in range(100):
        mid = (low+high)/2
        if math.fsum(vector(mid)) > 1:
            low = mid
        else:
            high = mid
    return vector((low+high)/2)


for f in frames:
    assert f['availability_proven'] is False and f['executable'] is False
    assert f['quote_observed_at'] is None and f['quote_observed_at_verified'] is False
    assert set(f['probability_models']) == {'market', 'market90_vote10'}
    assert f['model_calibrated']['vote_support'] is False
    assert datetime.fromisoformat(f['kickoff']).utcoffset() is not None
    assert datetime.fromisoformat(f['kickoff'])-datetime.fromisoformat(f['available_at']) == timedelta(hours=24)
    assert not f['round_inference_from_date']
    assert len(f['choices']) == len({c['choice_id'] for c in f['choices']})
    assert not any(k in f for k in ['roi', 'ev', 'settled_at'])
    raw_counts = Counter()
    electorate = {}
    for p in f['raw_forecasts']:
        original = load(p['source_file'])['accepted'][int(p['json_pointer'].split('/')[-1])]
        assert original['source_url'] == p['source_url']
        assert original['side'] == p['side'] and original.get('author') == p['author']
        assert p['cutoff_proven'] is False
        for k in p:
            if k in original:
                assert original[k] == p[k]
        observed = p.get('observed_at')
        if observed:
            assert datetime.fromisoformat(observed).utcoffset() is not None
            assert datetime.fromisoformat(observed) > datetime.fromisoformat(f['kickoff'])
            checks['forecast_captures_postgame_date_verified'] += 1
        electorate.setdefault((p['source'], p.get('author') or '<publisher>'), set()).add(p['side'])
    for sides in electorate.values():
        if len(sides) == 1:
            raw_counts[next(iter(sides))] += 1
    assert f['voters'] == sum(raw_counts.values())
    assert all(f['vote_counts'][s] == raw_counts[s] for s in f['vote_counts'])
    for c in f['choices']:
        p = c['offer_proofs'][0]
        original = load(p['source_file'])[p['source_row_1based']-1]
        assert original == p['raw_offer']
        assert (c['issue_year'], c['round'], c['game_no']) == (original['raw_year'], original['raw_round'], original['raw_game_no'])
        assert c['offer_id'] == f"proto:{original['raw_year']}:{original['raw_round']}:{original['raw_game_no']}:{original['market']}"
        assert c['offer_id_independently_verified'] is False
        j = original['odds_order'].index(c['label'])
        assert c['odds'] == original['odds'][j]
        assert original['market'] == c['market'] and c['line'] is None
        assert datetime.fromisoformat(original['observed_at']) > datetime.fromisoformat(f['kickoff'])
        expected = independent_market(original['odds'])
        max_market_error = max(max_market_error, abs(expected[j]-c['scores']['market']))
        assert abs(expected[j]-c['scores']['market']) < 1e-10
        support = raw_counts[c['label']]/sum(raw_counts.values())
        assert support == c['scores']['vote_support'] == c['vote_share']
        assert ('vote_support' in c['eligible_models']) == c['vote_support_eligible'] == (support > 0)
        assert c['scores']['market90_vote10'] == round(.9*c['scores']['market']+.1*support, 4)
        assert c['vote_support_is_probability'] is False and 'vote_support' not in c['probability_models']
        if not support:
            assert 'vote_support' in c['unsupported_models']
            checks['zero_support_ineligible'] += 1
        refund = f['home_score'] == f['away_score'] and len(original['odds_order']) == 2
        winner = 'home' if f['home_score'] > f['away_score'] else 'away' if f['away_score'] > f['home_score'] else 'draw'
        assert f['winner'] == winner
        assert c['refund'] == refund
        assert c['hit'] == (not refund and c['label'] == winner)
        assert c['status'] == ('refund' if refund else 'hit' if c['hit'] else 'miss')
        assert c['result_evidence'] == f['result_evidence'] == 'official_exact_fixture'
        assert c['quote_observed_at'] is None and c['availability_proven'] is False and c['executable'] is False
        assert not any(k in c for k in ['roi', 'ev', 'probability', 'p', 'settled_at'])
        checks['exact_offers_and_settlement_choices_verified'] += 1
    known_rounds = {c['round'] for c in f['choices']}
    if len(known_rounds) == 1:
        assert f['round'] == next(iter(known_rounds)) and f['round_known']
    else:
        assert f['round'] == 'unknown' and not f['round_known'] and f['round_ambiguous_multiple_saved_offers']
        checks['multiple_saved_round_frames_explicitly_ambiguous'] += 1
    checks['frames_verified'] += 1
checks['source_files_hash_verified'] = sum(hashlib.sha256(Path(p).read_bytes()).hexdigest() == d['sha256'] for p,d in manifest['source_paths'].items())
assert checks['source_files_hash_verified'] == len(manifest['source_paths'])
result = dict(passed=True, checks=dict(checks), max_independent_market_probability_error=max_market_error,
              event_sha256=manifest['sha256'], roi_computed=False, all_writes_private=True)
(OUT/'independent-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
print(json.dumps(result, ensure_ascii=False, indent=2))
