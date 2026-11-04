"""Fixed, offline low-price study; see experiment README for scope and caveats."""
import argparse
import collections
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path('C:/Users/user/Documents/Codex')
CUTOFF = Decimal('1.20')


def price(value):
    try:
        p = Decimal(str(value))
        return p if p.is_finite() and p > 1 else None
    except Exception:
        return None


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def pregame(r):
    kickoff = instant(r.get('kickoff'))
    times = [instant(r.get(k)) for k in ('as_of', 'captured_at', 'market_observed_at')]
    return kickoff is not None and all(t is not None and t < kickoff for t in times)


def alternative(r):
    """Descriptive only: same product signature, market-derived probability ranking."""
    p = r['predictions']
    selected = [x for x in r['features']['options'] if x.get('offer_id') == p.get('offer_id') and x.get('selection_id') == p.get('selection_id')]
    if len(selected) != 1:
        return None
    original = selected[0]
    options = []
    for o in r['features']['options']:
        v = o.get('final_probability')
        if (o.get('market'), o.get('label'), o.get('line')) != (original.get('market'), original.get('label'), original.get('line')):
            continue
        if not o.get('selection_id') or o.get('selection_id') == p.get('selection_id') or not price(o.get('odds')):
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1:
            options.append(o)
    return min(options, key=lambda o: (-o['final_probability'], o['selection_id'])) if options else None


def stats(rows):
    # Rows carry outcome only after selection. Voids refund stake; pending excluded from ROI.
    settled = [r for r in rows if r['result'] in ('hit', 'miss', 'void')]
    hits = sum(r['result'] == 'hit' for r in settled)
    misses = sum(r['result'] == 'miss' for r in settled)
    voids = len(settled) - hits - misses
    profit = sum((r['odds'] - 1 if r['result'] == 'hit' else -1 if r['result'] == 'miss' else 0) for r in settled)
    ordered = sorted(settled, key=lambda r: (r['kickoff'], r['event_id']))
    equity = peak = drawdown = 0.0
    for r in ordered:
        equity += r['odds'] - 1 if r['result'] == 'hit' else -1 if r['result'] == 'miss' else 0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {'selected': len(rows), 'settled': len(settled), 'pending': len(rows)-len(settled), 'hit': hits, 'miss': misses, 'void': voids, 'hit_rate_ex_void': hits/(hits+misses) if hits+misses else None, 'roi_settled_stake': profit/len(settled) if settled else None, 'profit_units': profit, 'max_drawdown_units_settled_only': drawdown}


def exact_official(r, settlement, official_index):
    """Only an exact documented product, line, and kickoff is admissible."""
    source = settlement.get('source', {})
    p = r['predictions']
    matches = []
    if source.get('mode') == 'official_score_with_frozen_terms':
        candidates = [x.get('official_source', {}) for x in source.get('official_scores', [])]
    elif source.get('official_source'):
        candidates = [source['official_source']]
    else:
        candidates = [x.get('official_source', {}) for x in official_index.get((str(source.get('round')), str(source.get('game_no'))), [])]
    expected = instant(r['kickoff'])
    for c in candidates:
        market_text = c.get('market_text', '')
        if p.get('market') not in market_text or c.get('label', '') != (p.get('label') or ''):
            continue
        # No period substitution, including first-half/quarter products.
        if any(x in market_text for x in ('전반', '후반', '쿼터', '1쿼터', '2쿼터', '세트')):
            continue
        try:
            actual = datetime.strptime(c['kickoff_text'], '%Y%m%d%H%M%S').replace(tzinfo=timezone(timedelta(hours=9)))
        except (ValueError, KeyError, TypeError):
            continue
        if actual != expected or not c.get('evidence_sha256') or not c.get('game_no'):
            continue
        matches.append(c)
    return matches[0] if len(matches) == 1 else None


def run(ledger, matched, quotes, archive, predictions, official):
    with gzip.open(ledger, 'rt', encoding='utf-8') as f:
        records = [json.loads(line) for line in f]
    predictions_rows = [r for r in records if r['record_type'] == 'prediction' and pregame(r)]
    predictions_rows.sort(key=lambda r: (instant(r['captured_at']), r.get('ledger_sequence', 0)))
    first = {}
    for r in predictions_rows:
        first.setdefault(r['event_id'], r)
    settlements = {r['snapshot_id']: r for r in records if r['record_type'] == 'settlement'}
    with open(official, encoding='utf-8') as f: official_rows = json.load(f)
    official_index = collections.defaultdict(list)
    for o in official_rows:
        official_index[(str(o.get('round')), str(o.get('game_no')))].append(o)
    rows, alts = [], []
    for r in first.values():
        p = r['predictions']; odds = price(p.get('odds'))
        if p.get('action') != 'market_reference' or odds is None:
            continue
        s = settlements.get(r['snapshot_id'], {})
        outcome = s.get('outcome', {})
        proof = exact_official(r, s, official_index)
        result = outcome.get('result') if proof and outcome.get('selection_id') == p.get('selection_id') else None
        rows.append({'event_id': r['event_id'], 'snapshot_id': r['snapshot_id'], 'kickoff': r['kickoff'], 'odds': float(odds), 'probability': p.get('probability'), 'market': p.get('market'), 'label': p.get('label'), 'selection_id': p.get('selection_id'), 'result': result, 'settlement_source': s.get('source') if result else None, 'official_match': proof if result else None})
        if odds <= CUTOFF:
            a = alternative(r)
            alts.append({'event_id': r['event_id'], 'snapshot_id': r['snapshot_id'], 'option': a, 'outcome': None})
    low = [r for r in rows if Decimal(str(r['odds'])) <= CUTOFF]
    high = [r for r in rows if Decimal(str(r['odds'])) > CUTOFF]
    monthly = {}
    for m in sorted({r['kickoff'][:7] for r in rows}):
        monthly[m] = {'original': stats([r for r in rows if r['kickoff'].startswith(m)]), 'skip': stats([r for r in high if r['kickoff'].startswith(m)]), 'low': stats([r for r in low if r['kickoff'].startswith(m)])}
    with open(matched, encoding='utf-8') as f: common = json.load(f)
    m1 = 'kbo-player-effects-v2-2026-M1'
    common_rows = []
    for r in common:
        side = r['selections'][m1]
        common_rows.append({'event_id': r['fixture_id'], 'kickoff': r['kickoff'], 'odds': r['prices'][side], 'result': 'hit' if side == r['winner'] else 'miss'})
    with gzip.open(quotes, 'rt', encoding='utf-8') as f: captured_quotes = json.load(f)
    with gzip.open(archive, 'rt', encoding='utf-8') as f: archive_rows = [json.loads(line) for line in f]
    with open(predictions, encoding='utf-8') as f: m1_predictions = json.load(f)
    scenarios = {k: {model: len(values) for model, values in models.items()} for k, models in m1_predictions['scenarios'].items()}
    return {'method': 'first captured pre-kickoff snapshot per event; selected product settlement by snapshot and selection id', 'ledger': {'records':len(records),'pregame_prediction_records':len(predictions_rows),'events':len(first),'eligible_market_reference_events':len(rows),'original':stats(rows),'skip_at_most_1_20':stats(high),'low_original':stats(low),'low_abstain':len(low),'low_with_same_signature_descriptive_alternative':sum(x['option'] is not None for x in alts),'alternative_settled':0,'validated_ev_alternatives':0,'monthly':monthly,'low_rows':low,'alternative_rows':alts}, 'common103_retrospective':{'n':len(common_rows),'m1_original':stats(common_rows),'m1_low':stats([r for r in common_rows if Decimal(str(r['odds']))<=CUTOFF]),'m1_skip':stats([r for r in common_rows if Decimal(str(r['odds']))>CUTOFF])}, 'source_inventory':{'archived_price_rows':len(archive_rows),'archived_low_price_options':sum(1 for r in archive_rows for x in r.get('payload',{}).get('odds',{}).values() if price(x) and price(x)<=CUTOFF),'archived_sports':dict(collections.Counter(r.get('sport') for r in archive_rows)),'archived_years':dict(collections.Counter(r.get('kickoff','')[:4] for r in archive_rows)), 'extended_quote_rows':len(captured_quotes),'m1_scenarios':scenarios}}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=ROOT/'experiments/m1-lowodds-20261104/results.json'); args=parser.parse_args()
    report=run(EVIDENCE/'tipster-cohort-evidence/ledger-after.jsonl.gz', EVIDENCE/'matched-results/experiments/matched-baseball-20261104/rows.json', EVIDENCE/'tipster-extended-evidence/pregame-odds-snapshots.json.gz', ROOT/'experiments/pregame-proto-prices-20261104/normalized-price-records-v2.jsonl.gz', EVIDENCE/'kbeffects/experiments/kbo-player-effects-20261103/artifacts/predictions.json', EVIDENCE/'tipster-cohort-evidence/normalized-official-rows.json')
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({k:v for k,v in report.items() if k != 'ledger'},ensure_ascii=False)); print(json.dumps({k:v for k,v in report['ledger'].items() if k not in ('low_rows','alternative_rows')},ensure_ascii=False))

if __name__ == '__main__': main()
