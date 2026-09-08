"""Bounded, anonymous MLB historical acquisition; never a production ingestion job.

Run with an explicit, NEW private output directory. Historical event dates are not
availability timestamps. These data cannot establish historical pregame availability
or accuracy lift. Statistical sample units are games, not individual pitches.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # redirects must not silently spend additional requests


def extract(feed):
    """Deduplicate pitches by playId, with at-bat/event index fallback."""
    data = feed['gameData']
    box = feed.get('liveData', {}).get('boxscore', {}).get('teams', {})
    starters = {side: (box.get(side, {}).get('pitchers') or [None])[0]
                for side in ('home', 'away')}
    pitches, seen, duplicates = [], set(), 0
    for play in feed.get('liveData', {}).get('plays', {}).get('allPlays', []):
        about = play.get('about', {})
        side = 'home' if about.get('isTopInning') is True else 'away' if about.get('isTopInning') is False else None
        pitcher = play.get('matchup', {}).get('pitcher', {}).get('id')
        for position, event in enumerate(play.get('playEvents', [])):
            if event.get('details', {}).get('eventType') == 'pitching_substitution':
                pitcher = event.get('player', {}).get('id')
            if event.get('isPitch') is not True:
                continue
            key = str(event.get('playId') or f"{about.get('atBatIndex')}:{event.get('index', position)}")
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            starter = starters.get(side)
            pitches.append(dict(event_key=key, pitcher_id=pitcher,
                                team_id=data.get('teams', {}).get(side, {}).get('id'),
                                is_starter=None if starter is None or pitcher is None else pitcher == starter,
                                speed=event.get('pitchData', {}).get('startSpeed')))
    people = {}
    for p in pitches:
        key = (p['team_id'], p['pitcher_id'])
        if key not in people:
            people[key] = dict(team_id=key[0], pitcher_id=key[1], is_starter=p['is_starter'], pitch_count=0)
        people[key]['pitch_count'] += 1
    return dict(game_id=feed['gamePk'], date=data['datetime']['officialDate'],
                final=data.get('status', {}).get('abstractGameState') == 'Final',
                pitches=pitches, people=list(people.values()), duplicates=duplicates)


def bullpen(target_date, team_id, games, schedule, start_date, days=3):
    """Actual prior relievers only; incomplete coverage stays unknown, never zero."""
    target = date.fromisoformat(target_date)
    lower = target - timedelta(days=days)
    expected = [g for g in schedule if lower.isoformat() <= g['date'] < target_date and team_id in g['teams']]
    observed = {g['game_id']: g for g in games}
    complete = lower >= date.fromisoformat(start_date)
    count = 0
    for item in expected:
        g = observed.get(item['game_id'])
        if not g or not g['final'] or g['date'] != item['date']:
            complete = False
            continue
        rows = [p for p in g['people'] if p['team_id'] == team_id]
        if not rows or any(p['is_starter'] is None for p in rows) or any(p['team_id'] is None for p in g['pitches']):
            complete = False
        count += sum(p['pitch_count'] for p in rows if p['is_starter'] is False)
    return dict(team_id=team_id, target_date=target_date, prior_days=days,
                expected_games=len(expected), coverage_complete=complete,
                bullpen_pitches=count if complete else None,
                provenance='retrospective; received now, not proven available pregame')


def create_db(path):
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE raw(id INTEGER PRIMARY KEY, endpoint TEXT, url TEXT,
          received_at TEXT NOT NULL, sha256 TEXT NOT NULL, status INTEGER, body BLOB NOT NULL);
        CREATE TRIGGER raw_no_update BEFORE UPDATE ON raw BEGIN SELECT RAISE(ABORT,'immutable raw'); END;
        CREATE TRIGGER raw_no_delete BEFORE DELETE ON raw BEGIN SELECT RAISE(ABORT,'immutable raw'); END;
        CREATE TABLE games(game_id INTEGER PRIMARY KEY, raw_id INTEGER, official_date TEXT, final INTEGER);
        CREATE TABLE pitches(game_id INTEGER, event_key TEXT, pitcher_id INTEGER,
          team_id INTEGER, is_starter INTEGER, speed REAL, PRIMARY KEY(game_id,event_key));
        CREATE TABLE people(game_id INTEGER, team_id INTEGER, pitcher_id INTEGER, is_starter INTEGER, pitch_count INTEGER);
    ''')
    return db


class Collector:
    def __init__(self, db, schedule_budget, feed_budget, request_budget):
        self.db = db
        self.limits = {'schedule': schedule_budget, 'feed': feed_budget}
        self.total = request_budget
        self.used = Counter()
        self.opener = build_opener(NoRedirect())

    def get(self, endpoint, url):
        if self.used[endpoint] >= self.limits[endpoint] or sum(self.used.values()) >= self.total:
            raise RuntimeError('request budget exhausted')
        self.used[endpoint] += 1
        try:
            with self.opener.open(Request(url, headers={'User-Agent': 'MLBHistoricalResearchPilot/1.0'}), timeout=30) as response:
                body, status = response.read(8_000_001), response.status
        except HTTPError as exc:
            body, status = exc.read(8_000_001), exc.code
        received = datetime.now(timezone.utc).isoformat()
        raw_id = self.db.execute('INSERT INTO raw(endpoint,url,received_at,sha256,status,body) VALUES(?,?,?,?,?,?)',
                                 (endpoint, url, received, hashlib.sha256(body).hexdigest(), status, body)).lastrowid
        self.db.commit()
        if status != 200:
            raise RuntimeError(f'HTTP {status}; halted without retry')
        if len(body) > 8_000_000:
            raise RuntimeError('response exceeds 8 MB cap; raw body truncated')
        return raw_id, json.loads(body)


def run(args):
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if not 1 <= (end - start).days + 1 <= 14:
        raise ValueError('schedule range must be 1..14 days')
    if not 1 <= args.max_games <= 200 or not 1 <= args.feed_budget <= 200 or args.schedule_budget != 1 or not 1 <= args.request_budget <= 201:
        raise ValueError('budgets: schedule=1, feed/max-games=1..200, requests=1..201')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)  # refuse existing databases/production paths
    db = create_db(output / 'pilot.sqlite')
    collector = Collector(db, args.schedule_budget, args.feed_budget, args.request_budget)
    games, schedule, errors, selected = [], [], [], []
    try:
        _, payload = collector.get('schedule', f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}')
        unique = {}
        for day in payload['dates']:
            for g in day['games']:
                unique[g['gamePk']] = dict(game_id=g['gamePk'], date=day['date'],
                                           final=g['status']['abstractGameState'] == 'Final',
                                           teams=[g['teams'][s]['team']['id'] for s in ('home', 'away')])
        schedule = sorted(unique.values(), key=lambda g: (g['date'], g['game_id']))
        selected = schedule[:args.max_games]
        for item in selected:
            raw_id, feed = collector.get('feed', f"https://statsapi.mlb.com/api/v1.1/game/{item['game_id']}/feed/live")
            g = extract(feed)
            if g['game_id'] != item['game_id']:
                raise ValueError('feed game ID mismatch')
            with db:
                db.execute('INSERT INTO games VALUES(?,?,?,?)', (g['game_id'], raw_id, g['date'], g['final']))
                db.executemany('INSERT INTO pitches VALUES(?,?,?,?,?,?)',
                               [(g['game_id'], p['event_key'], p['pitcher_id'], p['team_id'], p['is_starter'], p['speed']) for p in g['pitches']])
                db.executemany('INSERT INTO people VALUES(?,?,?,?,?)',
                               [(g['game_id'], p['team_id'], p['pitcher_id'], p['is_starter'], p['pitch_count']) for p in g['people']])
            games.append(g)
    except Exception as exc:
        errors.append(dict(type=type(exc).__name__, message=str(exc), policy='halt on first error; no retries'))
    pitches = [p for g in games for p in g['pitches']]
    features = [dict(game_id=g['game_id'], **bullpen(g['date'], team, games, schedule, args.start))
                for g in schedule for team in g['teams']]
    summary = dict(start=args.start, end=args.end, selection='chronological prefix (date, gamePk)',
                   scheduled_games=len(schedule), selected_games=len(selected), collected_games=len(games),
                   incomplete=bool(errors) or len(games) < len(schedule), final_games=sum(g['final'] for g in games),
                   scheduled_date_coverage=dict(Counter(g['date'] for g in schedule)),
                   collected_date_coverage=dict(Counter(g['date'] for g in games)),
                   pitches=len(pitches), pitcher_id_present=sum(p['pitcher_id'] is not None for p in pitches),
                   speed_present=sum(p['speed'] is not None for p in pitches),
                   person_game_rows=sum(len(g['people']) for g in games),
                   games_with_pitches=sum(bool(g['pitches']) for g in games),
                   duplicate_events=sum(g['duplicates'] for g in games),
                   requests=dict(collector.used), budgets=collector.limits | {'total': collector.total}, errors=errors,
                   usable_bullpen_rows=sum(f['coverage_complete'] for f in features),
                   sample_unit='game; pitches are dependent observations',
                   limitation='Coverage pilot only; no accuracy lift tested. Modern historical snapshots do not prove past availability.')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    (output / 'bullpen.json').write_text(json.dumps(features, indent=2), encoding='utf-8')
    db.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--output', required=True, help='NEW private task output directory')
    parser.add_argument('--max-games', type=int, default=30)
    parser.add_argument('--schedule-budget', type=int, required=True)
    parser.add_argument('--feed-budget', type=int, required=True)
    parser.add_argument('--request-budget', type=int, required=True)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2))
    return 2 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
