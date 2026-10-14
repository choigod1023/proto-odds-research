"""Offline conditional PA smoke gate. Python standard library only; no collection."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import sqlite3

CATEGORIES = ('K', 'BB-HBP', 'HR', 'otherhit', 'otherout')
# Fixed before evaluation: symmetric league pseudo-count 1/category;
# each batter/pitcher gets 100 PA of league shrinkage, then equal pooling.
LEAGUE_ALPHA = 1.0
PLAYER_STRENGTH = 100.0
LABELS = {
    'strikeout': 'K', 'strikeout_double_play': 'K',
    'walk': 'BB-HBP', 'intent_walk': 'BB-HBP', 'hit_by_pitch': 'BB-HBP',
    'home_run': 'HR', 'single': 'otherhit', 'double': 'otherhit', 'triple': 'otherhit',
    'field_out': 'otherout', 'force_out': 'otherout',
    'grounded_into_double_play': 'otherout', 'double_play': 'otherout',
    'triple_play': 'otherout', 'fielders_choice': 'otherout',
    'fielders_choice_out': 'otherout', 'sac_fly': 'otherout',
    'sac_bunt': 'otherout', 'sac_fly_double_play': 'otherout',
    'sac_bunt_double_play': 'otherout',
}
NON_PA_PREFIXES = ('caught_stealing_', 'pickoff_', 'pickoff_caught_stealing_',
                   'stolen_base_')
NON_PA = {'balk', 'wild_pitch', 'passed_ball', 'other_advance', 'runner_out',
          'defensive_indiff', 'game_advisory', 'no_pitch'}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def open_source(path):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def extract(feed):
    """Return accepted rows and auditable exclusions; never use playEvents as PA."""
    rows, audit, seen = [], Counter(), set()
    day = feed['gameData']['datetime']['officialDate']
    date.fromisoformat(day)
    game = feed['gamePk']
    if feed['gameData']['status']['abstractGameState'] != 'Final':
        return [], Counter({'nonfinal_game': 1})
    for play in feed['liveData']['plays']['allPlays']:
        audit['plays_seen'] += 1
        about, result = play.get('about', {}), play.get('result', {})
        label = result.get('eventType', '<missing>')
        if not about.get('isComplete'):
            audit['incomplete'] += 1
            continue
        if label in NON_PA or label.startswith(NON_PA_PREFIXES):
            audit['non_pa:' + label] += 1
            continue
        if result.get('type') != 'atBat':
            audit['non_atbat:' + label] += 1
            continue
        if label not in LABELS:
            audit['unmapped:' + label] += 1
            continue
        index = about.get('atBatIndex')
        matchup = play.get('matchup', {})
        batter = matchup.get('batter', {}).get('id')
        pitcher = matchup.get('pitcher', {}).get('id')
        if not isinstance(index, int) or not isinstance(batter, int) or not isinstance(pitcher, int):
            audit['missing_identity'] += 1
            continue
        if index in seen:
            raise ValueError(f'Duplicate PA index in game {game}')
        seen.add(index)
        rows.append(dict(game=game, day=day, pa=index, batter=batter,
                         pitcher=pitcher, label=label, outcome=LABELS[label]))
        audit['accepted'] += 1
    return rows, audit


def load(path):
    rows, audit, games, raw_hashes = [], Counter(), [], []
    with closing(open_source(path)) as conn:
        records = conn.execute('''SELECT g.game_id,g.official_date,g.final,r.sha256,r.body
            FROM games g LEFT JOIN raw r ON r.id=g.raw_id ORDER BY g.official_date,g.game_id''')
        for game, day, final, expected, body in records:
            if body is None:
                raise ValueError('Missing raw feed')
            actual = hashlib.sha256(body if isinstance(body, bytes) else body.encode()).hexdigest()
            if actual != expected:
                raise ValueError('Raw body hash mismatch')
            feed = json.loads(body)
            if feed['gamePk'] != game or feed['gameData']['datetime']['officialDate'] != day:
                raise ValueError('Feed identity/date mismatch')
            if final != 1 or feed['gameData']['status']['abstractGameState'] != 'Final':
                raise ValueError('Cache contains nonfinal game')
            accepted, counts = extract(feed)
            rows.extend(accepted)
            audit.update(counts)
            games.append(dict(game=game, day=day, accepted=len(accepted), audit=dict(counts)))
            raw_hashes.append(actual)
    if not games or not rows:
        raise ValueError('Empty experiment')
    return rows, audit, games, raw_hashes


def evaluate(rows):
    league, batters, pitchers = Counter(), defaultdict(Counter), defaultdict(Counter)
    predictions = []
    ordered = sorted(rows, key=lambda r: (r['day'], r['game'], r['pa']))
    for day, batch in groupby(ordered, key=lambda r: r['day']):
        batch = list(batch)
        prior_n = league.total()
        prior = [(league[k] + LEAGUE_ALPHA) / (prior_n + 5 * LEAGUE_ALPHA) for k in CATEGORIES]
        for row in batch:
            bc, pc = batters[row['batter']], pitchers[row['pitcher']]
            candidate = [0.5 * ((bc[k] + PLAYER_STRENGTH * prior[i]) / (bc.total() + PLAYER_STRENGTH)
                              + (pc[k] + PLAYER_STRENGTH * prior[i]) / (pc.total() + PLAYER_STRENGTH))
                         for i, k in enumerate(CATEGORIES)]
            for model, probs in (('league', prior), ('batter_pitcher', candidate)):
                y = CATEGORIES.index(row['outcome'])
                predictions.append(dict(**row, model=model, probabilities=list(probs),
                    prior_pa=prior_n, batter_history=bc.total(), pitcher_history=pc.total(),
                    phase='evaluation' if prior_n else 'warmup',
                    log_loss=-math.log(probs[y]),
                    brier=sum((p - int(i == y)) ** 2 for i, p in enumerate(probs))))
        # All games on a date are evaluated before any outcome that date enters history.
        for row in batch:
            league[row['outcome']] += 1
            batters[row['batter']][row['outcome']] += 1
            pitchers[row['pitcher']][row['outcome']] += 1
    return predictions


def summarize(predictions):
    groups = defaultdict(list)
    for row in predictions:
        groups[(row['day'], row['phase'], row['model'])].append(row)
        if row['phase'] == 'evaluation':
            groups[('ALL_EVALUATION', 'evaluation', row['model'])].append(row)
    return [dict(day=day, phase=phase, model=model, n=len(rs),
                 log_loss=sum(r['log_loss'] for r in rs) / len(rs),
                 brier=sum(r['brier'] for r in rs) / len(rs))
            for (day, phase, model), rs in sorted(groups.items())]


def markdown(meta):
    lines = ['# MLB conditional PA first gate', '',
        'Outcome: offline extraction and chronological evaluation ran. Acquisition remains insufficient '
        'for model improvement, game-win, or betting claims. No production change.', '',
        f"Input SQLite SHA-256: `{meta['input_sha256']}`.",
        f"Final cached games: {meta['games']}; dates: {meta['dates']}; accepted PAs: {meta['accepted_pa']}.",
        'Cache is a bounded acquisition sample, not established complete MLB coverage. '
        'Only three dates (two evaluation dates) and 30 games; PA rows are clustered within games/players.', '',
        '## Fixed protocol', '',
        'Categories in probability order: K, BB-HBP, HR, otherhit, otherout. '
        'Intentional walks are included. Otherout includes sacrifice and fielder-choice outcomes '
        '(a fielder choice need not retire the batter). Errors and catcher interference are excluded '
        'as unmapped rather than mislabeled as outs; this restricts the diagnostic population.',
        'League prior: (past category count + 1) / (past PA count + 5). '
        'Candidate: equal average of batter and pitcher distributions, each shrunk with 100 '
        'pseudo-PAs distributed according to that league prior. Parameters fixed before running; '
        'one candidate, no handedness variant or tuning.',
        'Only strictly earlier official dates update counts. First date is warm-up and excluded '
        'from aggregate comparison. Its cold-start scores are shown separately. '
        'No pitch, current outcome, same-day count, or full-sample frequency is a prediction feature.',
        'Actual batter/pitcher identities from completed feeds condition this PA diagnostic. '
        'They are NOT preannounced lineups or pregame-available matchups. Historical feeds were '
        'retrieved retrospectively; point-in-time availability/revisions are not established. '
        'Official-date ordering is a day-batched diagnostic, not a timestamp-safe production replay.',
        'Metrics are mean natural-log loss and multiclass Brier (sum across five classes); lower is better. '
        'No win hit rate is calculated from PA rows.', '', '## Coverage and exclusions', '']
    for day, counts in meta['coverage'].items():
        lines.append(f"- {day}: {counts['games']} games; {counts['pa']} accepted PAs.")
    lines.extend(['', 'Extractor audit (all cached plays):'])
    lines.extend(f'- {k}: {v}' for k, v in sorted(meta['audit'].items()))
    lines.extend(['', 'Accepted outcome counts:'])
    lines.extend(f'- {k}: {v}' for k, v in meta['outcomes'].items())
    lines.extend(['', '## Scores', ''])
    for score in meta['scores']:
        lines.append(f"- {score['day']} / {score['phase']} / {score['model']}: "
                     f"n={score['n']}, log loss={score['log_loss']:.6f}, Brier={score['brier']:.6f}.")
    lines.extend(['', '## Acquisition gate', '',
        'Smoke implementation gate passes if integrity checks and tests pass. Evidence gate remains '
        'insufficient: acquire a materially longer, representative chronological cache with adequate '
        'player history, independent game/date evaluation units, and documented coverage before '
        'assessing improvement. Resolve residual PA taxonomy and reconcile PA totals with box scores. '
        'Pregame work additionally needs timestamped lineup/roster availability and matchup generation. '
        'No further collection was performed in this run.', '',
        'Private results.sqlite stores accepted rows, probabilities, per-game audits, and provenance. '
        'Only code, synthetic tests, and this aggregate report belong in Git. '
        'Source: cached MLB StatsAPI feed bodies referenced by the pilot SQLite games/raw tables; '
        'each referenced raw SHA-256 is verified, and the source file hash is checked before/after.', ''])
    return '\n'.join(lines)


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    # An exclusive new directory prevents accidental overwrite, including the source cache.
    before = sha256(source)
    rows, audit, games, raw_hashes = load(source)
    predictions = evaluate(rows)
    coverage = {}
    for game in games:
        counts = coverage.setdefault(game['day'], dict(games=0, pa=0))
        counts['games'] += 1
        counts['pa'] += game['accepted']
    meta = dict(input_sha256=before, raw_body_sha256=raw_hashes, games=len(games),
        dates=len(coverage), accepted_pa=len(rows), coverage=coverage, audit=dict(audit),
        outcomes={k: sum(r['outcome'] == k for r in rows) for k in CATEGORIES},
        league_alpha=LEAGUE_ALPHA, player_strength=PLAYER_STRENGTH, scores=summarize(predictions))
    if sha256(source) != before:
        raise ValueError('Source changed during run')
    output.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(output / 'results.sqlite')) as conn:
        conn.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE games(game INTEGER PRIMARY KEY,day TEXT,accepted INTEGER,audit TEXT);
            CREATE TABLE predictions(game INTEGER,pa INTEGER,day TEXT,batter INTEGER,pitcher INTEGER,
                label TEXT,outcome TEXT,model TEXT,probabilities TEXT,prior_pa INTEGER,
                batter_history INTEGER,pitcher_history INTEGER,phase TEXT,log_loss REAL,brier REAL,
                PRIMARY KEY(game,pa,model));''')
        conn.executemany('INSERT INTO metadata VALUES(?,?)', [(k, json.dumps(v)) for k, v in meta.items()])
        conn.executemany('INSERT INTO games VALUES(?,?,?,?)',
                         [(g['game'], g['day'], g['accepted'], json.dumps(g['audit'])) for g in games])
        for row in predictions:
            conn.execute('INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                tuple(json.dumps(row[k]) if k == 'probabilities' else row[k] for k in
                      ('game', 'pa', 'day', 'batter', 'pitcher', 'label', 'outcome', 'model',
                       'probabilities', 'prior_pa', 'batter_history', 'pitcher_history',
                       'phase', 'log_loss', 'brier')))
        conn.commit()
    (output / 'report.md').write_text(markdown(meta), encoding='utf-8')
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New private output directory')
    args = parser.parse_args()
    result = run(args.source, args.output)
    print(json.dumps(result, indent=2))
