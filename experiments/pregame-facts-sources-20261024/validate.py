"""Validate this manually reviewed evidence packet, not article truth."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    records = json.loads((ROOT / 'records.json').read_text(encoding='utf8'))
    log = json.loads((ROOT / 'access_log.json').read_text(encoding='utf8'))
    required = {'id', 'sport', 'kind', 'source_url', 'published_at', 'observed_at',
                'event_id', 'kickoff_at', 'summary', 'verification'}
    forbidden = {'pick', 'final_pick', 'choice', 'odds', 'winner', 'handicap', 'total'}
    assert len({r['id'] for r in records}) == len(records)
    sources = {s['id']: s for s in log['sources']}
    for r in records:
        assert required <= r.keys()
        assert not forbidden.intersection(r)
        assert r['sport'] in {'bs', 'sc', 'bk', 'vl'}
        assert r['kind'] in {'starter', 'absence', 'rotation'}
        assert len(r['summary']) < 180
        assert r['source_url'] == sources[r['source_id']]['url']
        assert datetime.fromisoformat(r['observed_at'].replace('Z', '+00:00')).utcoffset() is not None
        assert r['published_at'] is None  # display time has no verified timezone/version
        assert r['event_id'] is None and r['kickoff_at'] is None
        assert r['pregame_observed_at'] is None and r['usable_for_model'] is False
        if r['verification']['confirmed'] is True:
            assert r['verification']['source_url'].startswith('https://www.koreabaseball.com/')
            assert r['verification']['observed_at'] == r['observed_at']
            assert r['verification_scope'].startswith('retrospective_')
        if r['claim_status'] in {'returned', 'absent', 'coach_absent', 'substitution', 'position_assignment'}:
            assert r['claim_scope'] == 'previous_event'
    assert set(r['sport'] for r in records) == {'bs', 'sc', 'bk', 'vl'}
    report = {
        'records': len(records),
        'claims_by_sport': dict(Counter(r['sport'] for r in records)),
        'readable_analysis_articles_by_sport': dict(Counter(s['sport'] for s in log['sources'])),
        'official_retrospective_corroborated_claims': sum(r['verification']['confirmed'] is True for r in records),
        'pregame_eligible_claims': 0,
        'roi': None,
        'hash_scope': 'local artifact bytes only; NOT origin HTML or historical snapshot hashes',
        'sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ('records.json', 'access_log.json')},
    }
    (ROOT / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
