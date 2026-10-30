"""Read only frozen research exports; never a production DB or collector."""
import gzip
import hashlib
import json
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent
ROOT = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')


def read(path):
    op = gzip.open if path.suffix == '.gz' else open
    with op(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def write(path, rows):
    raw = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows).encode()
    with path.open('wb') as f:
        with gzip.GzipFile(fileobj=f, mode='wb', mtime=0, filename='') as z:
            z.write(raw)


def main():
    folder = HERE / 'inputs'
    folder.mkdir(exist_ok=True)
    paths = {
        'baseball_history': ROOT/'shrunk-market-residual-20261030/experiments/shrunk-market-residual-20261030/inputs/history.jsonl.gz',
        'baseball_identities': ROOT/'shrunk-market-residual-20261030/experiments/shrunk-market-residual-20261030/inputs/canonical_targets.jsonl.gz',
        'soccer_history': ROOT/'kleague-decay-backtest-20261029/experiments/kleague-decay-20261029/history.jsonl',
        'soccer_identities': ROOT/'kleague-decay-backtest-20261029/experiments/kleague-decay-20261029/targets.jsonl',
        'raw_frames': ROOT/'two-leg-model-literature-20261030/experiments/two-leg-literature-20261030/inputs/votes.jsonl.gz',
        'cases': ROOT/'link-new-source-evidence-20261030/experiments/source-link-step2-20261030/inputs/cases.jsonl.gz',
        'aliases': ROOT/'shrunk-market-residual-20261030/experiments/shrunk-market-residual-20261030/results/results.json',
    }
    provenance = {name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in paths.items()}
    history = []
    for key in ('baseball_history', 'soccer_history'):
        for row in read(paths[key]):
            # Do NOT import target-game players, current lineup, pitcher or stats.
            history.append({k: row[k] for k in ('league', 'date', 'home', 'away', 'hg', 'ag')} |
                           {k: row[k] for k in ('official_id', 'game_type', 'sources', 'source', 'origins', 'url') if k in row})
    counts = {}
    for row in history:
        key = row['league'], row['date'], row['home'], row['away']
        counts[key] = counts.get(key, 0) + 1
    assert all(v == 1 for v in counts.values()), 'ambiguous date/team duplicate'
    identities = {(r['league'], r['fixture_id']): r for name in ('baseball_identities', 'soccer_identities') for r in read(paths[name])}
    aliases = json.loads(paths['aliases'].read_text(encoding='utf-8'))['aliases']
    cases = read(paths['cases'])
    frames = {r['event_id']: r for r in read(paths['raw_frames'])}
    for c in cases:
        bridge = identities.get((c['league'], c['event_id']))
        if bridge:
            for side in ('home', 'away'):
                aliases.setdefault(c['league'], {})[c[side]] = bridge[side]
    audit, targets = {}, []
    known_by_league = {l:{r[s] for r in history if r['league']==l for s in ('home','away')}
                       for l in set(r['league'] for r in history)}
    for c in cases:
        if c['league'] not in ('KBO', 'NPB', 'MLB', 'K리그1', 'K리그2'):
            continue
        if c.get('void') or c.get('outcome') not in range(len(c['sides'])):
            continue
        if c['cohort'] not in ('historical_market_proxy', 'retrospective_year_strict__claimed_time_only'):
            continue
        actual = c['cohort'] != 'historical_market_proxy'
        if actual:
            f = frames.get(c['event_id'])
            bad = 'missing_frame'
            try:
                kickoff = datetime.fromisoformat(f['kickoff'])
                dates = [datetime.fromisoformat(r['source_reported_published_at'].replace('Z', '+00:00')) for r in f['raw_forecasts']]
                bad = 'unknown_or_naive_publication' if not dates or any(d.tzinfo is None for d in dates) else 'postkickoff_publication' if any(d >= kickoff for d in dates) else None
            except (KeyError, ValueError, TypeError, AttributeError):
                bad = 'unknown_or_naive_publication' if f is not None else 'missing_frame'
            if bad:
                audit[bad] = audit.get(bad, 0) + 1
                continue
            if c.get('external_p') is None:
                continue
        bridge = identities.get((c['league'], c['event_id'])) if actual else None
        home = bridge['home'] if bridge else aliases.get(c['league'], {}).get(c['home'], c['home'])
        away = bridge['away'] if bridge else aliases.get(c['league'], {}).get(c['away'], c['away'])
        known = known_by_league[c['league']]
        if home not in known or away not in known:
            audit['unresolved_exact_team'] = audit.get('unresolved_exact_team', 0) + 1
            continue
        row = {k: c[k] for k in ('cohort', 'event_id', 'league', 'kickoff', 'decision_at', 'sides', 'odds', 'outcome', 'market_p', 'price_evidence')}
        row.update(home=home, away=away, actual_votes=actual, vote_p=c.get('external_p'))
        if actual:
            row['fixed_pick'] = f['original_model_picks']['market90_vote10']
            row['vote_pick'] = f.get('source_plurality_pick')
            row['votes_tie'] = f.get('source_plurality_tie', False)
            row['publication_evidence'] = [{k:v[k] for k in ('source_url','source','author',
                'source_reported_published_at','observed_at','received_at','document_sha256','source_sha256') if k in v}
                for v in f['raw_forecasts']]
            row['source_receipt_certified'] = False
            row['identity_bridge'] = {'official_fixture_id':c['event_id'],'source_names':[c['home'],c['away']],
                                     'canonical_names':[home,away]}
        targets.append(row)
    write(folder/'history.jsonl.gz', sorted(history, key=lambda x: (x['league'], x['date'], x['home'], x['away'])))
    write(folder/'targets.jsonl.gz', targets)
    (folder/'provenance.json').write_text(json.dumps({'sources': provenance, 'history_rows': len(history), 'target_rows': len(targets), 'exclusions': audit,
        'receipt_certified': 0, 'notes': ['date/team normalized prior history may omit doubleheaders, not full census', 'publication claims not independent receipt evidence', 'no fuzzy/outcome-based identities']}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({'history': len(history), 'targets': len(targets), 'exclusions': audit}, ensure_ascii=False))


if __name__ == '__main__':
    main()
