"""Offline exhaustive five-class conditional PA diagnostic. No acquisition."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import importlib.util
from itertools import groupby
import json
import math
from pathlib import Path
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse

from pilot import digest, filehash, normalize, validate_page, PERIODS

CLASSES = ('K', 'BB+HBP', 'HR', 'nonHRhit', 'otherPA')
COARSEN = {'K': 'K', 'BB-HBP': 'BB+HBP', 'HR': 'HR', 'otherhit': 'nonHRhit',
           'otherout': 'otherPA', 'residualreach': 'otherPA'}
STRENGTH = 100


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def counts(stat, group):
    n = normalize(stat, group)
    if not n['valid']:
        raise ValueError('Invalid historical denominator/counts')
    result = Counter(dict(zip(CLASSES, (n['K'], n['BB_HBP'], n['HR'],
        n['otherhit'], n['otherout_plus_residualreach']))))
    if result.total() != n['denominator'] or min(result.values()) < 0:
        raise ValueError('Nonexhaustive historical classes')
    return result


def coarsen(rows):
    # Unknown categories fail closed; no filtering and no remainder named outs.
    return [dict(r, outcome=COARSEN[r['outcome']], original_outcome=r['outcome']) for r in rows]


def seeds(path):
    players = {'hitting': defaultdict(Counter), 'pitching': defaultdict(Counter)}
    windows, receipts = defaultdict(list), []
    with closing(readonly(path)) as conn:
        meta = json.loads(conn.execute('SELECT detail FROM analysis ORDER BY id DESC LIMIT 1').fetchone()[0])
        if not meta['sampled_date_checks_passed']:
            raise ValueError('Date verification gate failed')
        for ident, url, uh, body, bh, status in conn.execute(
                'SELECT id,url,url_sha256,body,body_sha256,status FROM raw'):
            if digest(url.encode()) != uh or digest(body) != bh:
                raise ValueError('Raw receipt integrity failure')
            parsed, q = urlparse(url), parse_qs(urlparse(url).query)
            if status != 200 or parsed.path != '/api/v1/stats':
                continue  # Failed probe and game-log validation receipts are never seeds.
            if parsed.scheme != 'https' or parsed.netloc != 'statsapi.mlb.com':
                raise ValueError('Unexpected source')
            group, start, end = (q[k][0] for k in ('group', 'startDate', 'endDate'))
            if (start, end) not in PERIODS or end >= '2025-06-01':
                raise ValueError('Historical cutoff violation')
            if q.get('stats') != ['byDateRange'] or q.get('gameType') != ['R'] or q.get('sportIds') != ['1']:
                raise ValueError('Wrong seed population')
            block = validate_page(json.loads(body), group, start[:4])
            windows[(group, start, end)].append((int(q['offset'][0]), block))
            receipts.append(ident)
    expected = {(g, s, e) for g in players for s, e in PERIODS}
    if set(windows) != expected:
        raise ValueError('Missing historical window')
    audit = []
    for (group, start, end), pages in sorted(windows.items()):
        offset, seen, total_counts = 0, set(), Counter()
        totals = {b['totalSplits'] for _, b in pages}
        if len(totals) != 1:
            raise ValueError('Pagination totals disagree')
        for actual_offset, block in sorted(pages):
            if actual_offset != offset:
                raise ValueError('Pagination gap or overlap')
            offset += len(block['splits'])
            for split in block['splits']:
                player = split['player']['id']
                if player in seen:
                    raise ValueError('Duplicate player/window')
                seen.add(player)
                c = counts(split['stat'], group)
                players[group][player].update(c)
                total_counts.update(c)
        if offset != totals.pop():
            raise ValueError('Incomplete window')
        audit.append(dict(group=group, start=start, end=end, players=len(seen),
                          counts=dict(total_counts), denominator=total_counts.total()))
    league = Counter()
    # One batting-side count per PA; never add pitching-side counts again.
    for c in players['hitting'].values():
        league.update(c)
    return league, players['hitting'], players['pitching'], audit, receipts


def evaluate(rows, league_seed, batter_seed, pitcher_seed, prefix):
    league = Counter(league_seed)
    batters = defaultdict(Counter, {p: Counter(c) for p, c in batter_seed.items()})
    pitchers = defaultdict(Counter, {p: Counter(c) for p, c in pitcher_seed.items()})
    predictions = []
    for day, batch in groupby(sorted(rows, key=lambda r: (r['day'], r['game'], r['pa'])), key=lambda r: r['day']):
        batch = list(batch)
        prior = [(league[k] + 1) / (league.total() + 5) for k in CLASSES]
        for r in batch:
            bc, pc = batters[r['batter']], pitchers[r['pitcher']]
            pooled = [0.5 * ((bc[k] + STRENGTH * prior[i]) / (bc.total() + STRENGTH)
                       + (pc[k] + STRENGTH * prior[i]) / (pc.total() + STRENGTH))
                      for i, k in enumerate(CLASSES)]
            for model, probs in (('league', prior), ('batter_pitcher', pooled)):
                if not math.isclose(sum(probs), 1) or min(probs) <= 0:
                    raise ValueError('Invalid probability vector')
                y = CLASSES.index(r['outcome'])
                predictions.append(dict(r, model=prefix + '_' + model, probabilities=probs,
                    phase='warmup' if day == '2025-06-01' else 'evaluation',
                    prior_pa=league.total(), batter_history=bc.total(), pitcher_history=pc.total(),
                    log_loss=-math.log(probs[y]), brier=sum((p-int(i == y))**2 for i,p in enumerate(probs))))
        # Freeze ALL predictions across this date before any same-date updates.
        for r in batch:
            league[r['outcome']] += 1
            batters[r['batter']][r['outcome']] += 1
            pitchers[r['pitcher']][r['outcome']] += 1
    return predictions


def summarize(predictions):
    groups = defaultdict(list)
    for r in predictions:
        groups[(r['day'], r['model'])].append(r)
        if r['phase'] == 'evaluation':
            groups[('ALL_EVALUATION', r['model'])].append(r)
    return [dict(day=day, model=model, pa=len(rs), games=len({r['game'] for r in rs}),
        log_loss=sum(r['log_loss'] for r in rs)/len(rs),
        brier=sum(r['brier'] for r in rs)/len(rs))
        for (day,model), rs in sorted(groups.items())]


def run(source, history, gate_dir, output):
    before = {str(p.resolve()): filehash(p) for p in (source, history)}
    league, batters, pitchers, seed_audit, receipts = seeds(history)
    sys.path.insert(0, str(gate_dir.resolve()))
    spec = importlib.util.spec_from_file_location('corrected_gate', gate_dir/'gate.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    original, audit, games, _ = gate.load(source)
    if any(k.startswith(('unmapped:', 'missing_identity', 'incomplete', 'non_atbat:')) for k in audit):
        raise ValueError('Unexpected PA exclusions')
    rows = coarsen(original)
    if Counter(r['day'] for r in rows) != Counter({'2025-06-01':1070, '2025-06-02':555, '2025-06-03':622}):
        raise ValueError('Unexpected same-row population')
    predictions = evaluate(rows, league, batters, pitchers, 'seeded')
    predictions += evaluate(rows, Counter(), {}, {}, 'cold')
    expected = {(r['game'],r['pa']) for r in rows if r['day'] > '2025-06-01'}
    for model in {r['model'] for r in predictions}:
        actual = [(r['game'],r['pa']) for r in predictions if r['model']==model and r['phase']=='evaluation']
        if len(actual) != 1177 or set(actual) != expected:
            raise ValueError('Models do not use the same 1177 rows')
    if before != {str(p.resolve()): filehash(p) for p in (source, history)}:
        raise ValueError('Input database changed')
    scores = summarize(predictions)
    meta = dict(classes=CLASSES, prior_strength=STRENGTH, league_alpha=1, source_hashes=before,
        code_hashes={str(p.resolve()):filehash(p) for p in (Path(__file__),Path(__file__).with_name('pilot.py'),gate_dir/'gate.py',gate_dir/'reconcile.py')},
        seed_audit=seed_audit, seed_receipts=receipts, seed_league=dict(league),
        original_counts=dict(Counter(r['outcome'] for r in original)),
        coarsened_counts=dict(Counter(r['outcome'] for r in rows)), extraction_audit=dict(audit),
        rows=len(rows), evaluation_pa=len(expected), games=len(games), scores=scores,
        evaluation_row_sha256=digest(json.dumps(sorted(expected)).encode()),
        zero_history={model:{role:sum(r[role+'_history']==0 for r in predictions if r['model']==model and r['phase']=='evaluation')
            for role in ('batter','pitcher')} for model in ('seeded_batter_pitcher','cold_batter_pitcher')})
    output.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(output/'results.sqlite')) as conn:
        conn.executescript('CREATE TABLE metadata(detail TEXT); CREATE TABLE predictions(game INTEGER,pa INTEGER,model TEXT,detail TEXT,PRIMARY KEY(game,pa,model));')
        conn.execute('INSERT INTO metadata VALUES(?)',(json.dumps(meta),))
        conn.executemany('INSERT INTO predictions VALUES(?,?,?,?)',
            [(r['game'],r['pa'],r['model'],json.dumps(r)) for r in predictions])
        conn.commit()
    (output/'summary.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    lines = ['# Offline exhaustive five-class PA diagnostic', '',
        'Classes: K, BB+HBP, HR, nonHRhit, otherPA. OtherPA includes both former otherout and residualreach; '
        'it is never labeled outs. All 2,247 corrected PA rows are preserved, including errors/interference/choices. '
        'Three non-PA runner events remain excluded by the corrected extractor.', '',
        'Historical seeds use all acquired MLB regular-season player splits for 2024 and January–May 31, 2025. '
        'Every raw URL/body hash, requested window, page offset, unique player ID and count identity is checked. '
        'Full-season game-log receipts used for acquisition validation are never model features. '
        'Range bounds are request-validated and previously sample-reconciled, not echoed in bulk responses. '
        'Retrospective receipt is not proof of point-in-time availability.', '',
        'Seeded league sums batting counts over the full acquired league population once (not only target players; '
        'pitching counts are not added again). Batter PA and pitcher BF each map to [K, BB+HBP, HR, hits−HR, '
        'denominator−K−BB−HBP−hits]. All counts are nonnegative and sum to their denominator. '
        'Both years receive equal per-PA weight, without recency tuning.', '',
        'League probabilities=(counts+1)/(PA+5). Each player distribution has fixed strength 100 toward '
        'that league prior; candidate averages batter and pitcher distributions equally. Missing players '
        'fall back to the league prior. Cold baselines use the same five classes and settings without historical seeds. '
        'June 1 is warmup for every model, even seeded models. Predict a full day before updating any counts. '
        'Comparison uses exactly 1,177 PA in 15 games on June 2–3. No sweeps, network or production changes.', '',
        '## Conditional PA scores', '',
        'Natural-log loss and multiclass Brier (sum over five classes), lower is better.']
    for s in scores:
        lines.append(f"- {s['day']} / {s['model']}: PA={s['pa']}, games={s['games']}, log loss={s['log_loss']:.9f}, Brier={s['brier']:.9f}.")
    allscores = {s['model']:s for s in scores if s['day']=='ALL_EVALUATION'}
    a,b = allscores['seeded_batter_pitcher'],allscores['seeded_league']
    lines += ['', f"Seeded candidate minus seeded league: log loss={a['log_loss']-b['log_loss']:+.9f}; Brier={a['brier']-b['brier']:+.9f}.",
        '', 'These are descriptive conditional PA scores for realized batter/pitcher identities, not pregame lineup '
        'predictions or game-win improvement. Only two evaluation dates and 15 clustered game units; this bounded '
        'sample cannot establish generalization, betting value, or win accuracy.', '',
        '## Audit', '', f"Original counts: {meta['original_counts']}.", f"Coarsened counts: {meta['coarsened_counts']}.",
        f"Evaluation identity SHA-256: {meta['evaluation_row_sha256']}.",
        f"Zero-history PA: {meta['zero_history']}.",
        f"Historical seed audits: {json.dumps(seed_audit)}.", '',
        'Both source databases are opened read-only and their SHA-256 hashes are unchanged:']
    lines += [f'- {p}: {h}' for p,h in before.items()]
    lines += ['', 'Private results.sqlite stores all four models’ row-level probabilities, counts and losses; '
        'summary.json records code hashes and detailed audit. Only code, synthetic tests and aggregate Markdown '
        'are tracked. Source: existing official MLB StatsAPI receipts and corrected cached feed extraction.', '']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(scores=[s for s in scores if s['day']=='ALL_EVALUATION'],source_hashes=before),indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','history','gate-dir','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    run(args.source,args.history,args.gate_dir,args.output)
