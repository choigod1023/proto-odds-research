"""Join explicit collection artifacts and run the fixed one-year offline audit."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path

from scripts.evaluate_tipster_year import evaluate, START, END
from scripts.join_tipster_expansion import join


def read(path):
    text = path.read_text(encoding='utf-8-sig')
    if text.lstrip().startswith('['):
        return json.loads(text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(obj, dict):
        for field in ('candidates','predictions','fixtures','offers','rows','ledger'):
            if isinstance(obj.get(field), list):
                return obj[field]
    raise ValueError('expected array, JSONL or explicit records field: '+str(path))


def build(candidate_paths, fixture_paths, offer_paths, out):
    from scripts.tipster_year_aliases import configure
    configure()
    manifest = []
    def load(paths, kind):
        rows = []
        for path in paths:
            part = read(path)
            manifest.append(dict(path=str(path).replace('\\','/'), kind=kind, records=len(part),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            rows.extend(part)
        return rows
    candidates = load(candidate_paths, 'candidate')
    fixtures = load(fixture_paths, 'official_result')
    offers = load(offer_paths, 'full_price_vector')
    normalized = []
    for row in candidates:
        r = dict(row)
        r['source_url'] = r.get('source_url') or r.get('url')
        r['period'] = r.get('period') or r.get('market_scope')
        if r.get('league') in {'MLB','NPB','KBO'} and r['period']=='full_game':
            r['period'] = 'full_game_including_overtime'
            r['period_normalization_basis'] = 'full_baseball_game_includes_extra_innings_audit_convention'
        r['odds_time_quality'] = r.get('odds_time_quality') or r.get('price_evidence_tier')
        if (r.get('source') in {'Miky938 Tips','Betblog'} and r.get('reported_decimal_odds')
                and not r['odds_time_quality']):
            r['odds_time_quality'] = 'retrospective_listed_price_claim'
        r['reported_decimal_odds'] = r.get('reported_decimal_odds') or r.get('original_decimal_odds')
        if r.get('market')=='total' and not r.get('side'):
            r['side'] = r.get('selection')
        normalized.append(r)
    finals = []
    for row in fixtures:
        r = dict(row)
        r['source_url'] = r.get('source_url') or r.get('official_result_url') or r.get('result_source_url') or r.get('official_url')
        r['home'] = r.get('home_name_official') or r.get('home')
        r['away'] = r.get('away_name_official') or r.get('away')
        r['kickoff'] = r.get('kickoff') or r.get('official_kickoff') or r.get('kickoffKST') or r.get('start_at')
        if not (r.get('match_date') or r.get('date')) and r.get('kickoff'):
            r['match_date'] = r['kickoff'][:10]
        if r.get('league') in {'MLB','NBA'} and r.get('kickoff'):
            t = datetime.fromisoformat(r['kickoff'].replace('Z','+00:00'))
            if t.utcoffset() is None:
                raise ValueError('official international kickoff needs timezone')
            kst = t.astimezone(timezone(timedelta(hours=9)))
            r['official_date'] = r.get('official_date') or r.get('match_date') or r.get('date')
            r['match_date'] = kst.date().isoformat()
            r['kickoff'] = kst.isoformat()
        finals.append(r)
    joined = join(normalized, finals, offers)
    result = evaluate(joined)
    # Keep strict primary results; this named convention is a separate analysis.
    convention = []
    for row in joined:
        r = dict(row)
        if (r.get('league') in {'NBA','KBL'} and r.get('market') in {'moneyline','spread','total'}
                and r.get('period') in {'full_game','unverified_overtime_rule'}):
            r['period'] = 'full_game_including_overtime'
            r['settlement_scope_assumption'] = 'standard_full_game_includes_overtime_not_verified_per_original_bookmaker'
        convention.append(r)
    convention_result = evaluate(convention)
    result['inputs'] = manifest
    result['official_result_inputs_by_league'] = dict(Counter(r.get('league') for r in finals))
    result['raw_source_counts'] = dict(Counter(r.get('source') for r in candidates))
    result['raw_league_counts'] = dict(Counter(r.get('league') for r in candidates))
    result['raw_source_dates'] = {}
    for source in sorted({r['source'] for r in candidates}):
        batch = [r for r in candidates if r['source']==source]
        dates = sorted({r['match_date'] for r in batch if r.get('match_date')})
        result['raw_source_dates'][source] = dict(records=len(batch), first=dates[0] if dates else None,
            last=dates[-1] if dates else None, dates=len(dates),
            target_window_records=sum(START<=r.get('match_date','')<=END for r in batch),
            claimed_authors=len({r['author'] for r in batch if r.get('author')}))
    for key in ('inputs','official_result_inputs_by_league','raw_source_counts','raw_league_counts','raw_source_dates'):
        convention_result[key] = result[key]
    out.mkdir(parents=True, exist_ok=True)
    for name, value in [('joined.json', joined), ('results.json', result),
                        ('joined-standard-ot.json', convention), ('results-standard-ot.json', convention_result)]:
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, action='append', required=True)
    p.add_argument('--fixture', type=Path, action='append', required=True)
    p.add_argument('--offer', type=Path, action='append', default=[])
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    r = build(a.candidate, a.fixture, a.offer, a.out)
    print(json.dumps(dict(coverage=r['coverage'], excluded=r['excluded'],
        raw_source_dates=r['raw_source_dates'], paired=r['paired_author_consensus']['n']), ensure_ascii=False))
