"""Read archived source bytes; bind KBL player stats to an official fixture."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path('C:/Users/user/AppData/Local/Temp/basketball-source-20261108d')
FIXTURES = ROOT/'experiments/pregame-basketball-20261104/season-records.jsonl'
GAME = 'S45G01N116'


def extract(meta, body, fixtures):
    if meta.get('status') != 200 or hashlib.sha256(body).hexdigest() != meta.get('body_sha256'):
        raise ValueError('missing successful verified source')
    if not meta.get('received_at') or meta.get('timestamp_basis') != 'http_response_body_completed':
        raise ValueError('response-completion timestamp required')
    try:
        received = datetime.fromisoformat(meta['received_at'].replace('Z', '+00:00'))
        if received.utcoffset() is None:
            raise ValueError('timezone required')
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('invalid response-completion timestamp') from exc
    if meta.get('url') != f'https://api.kbl.or.kr/match/{GAME}/player-stat':
        raise ValueError('game source mismatch')
    found = [r for r in fixtures if r.get('kind') in ('fixture', 'result') and
             (r.get('payload') or {}).get('source_fixture_id') == GAME]
    by_kind = {k: [r for r in found if r['kind'] == k] for k in ('fixture', 'result')}
    if any(len(v) != 1 for v in by_kind.values()):
        raise ValueError('fixture/result not unique')
    f, result = by_kind['fixture'][0], by_kind['result'][0]
    if any(f.get(k) != result.get(k) for k in ('league','kickoff','home','away')):
        raise ValueError('fixture/result mismatch')
    teams = {str(f['payload']['home_team_id']), str(f['payload']['away_team_id'])}
    if len(teams) != 2:
        raise ValueError('ambiguous team ids')
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError('unexpected box schema')
    players, totals, seen = [], defaultdict(Counter), set()
    fields = ('playMin','playSec','score','fg','fgA','fgt','fgtA','threep','threepA',
              'ft','ftA','offr','defr','to','poss','pace','offrtg','defrtg')
    for row in rows:
        player, stats = row['player'], row['records']
        pid, team = str(player.get('pcode') or ''), str(player.get('tcode') or '')
        if not pid or pid in seen or team not in teams:
            raise ValueError('duplicate player or unexpected team')
        seen.add(pid)
        if any(type(stats.get(k)) is not int or stats[k] < 0 for k in ('playMin','playSec','score')) or stats['playSec'] >= 60:
            raise ValueError('invalid score or duration')
        totals[team]['players'] += 1
        totals[team]['score'] += stats['score']
        totals[team]['seconds'] += 60*stats['playMin'] + stats['playSec']
        players.append({'player_id': pid, 'team_id': team, 'startFlag': row.get('startFlag'),
                        'statistics': {k: stats.get(k) for k in fields}})
    expected = {str(f['payload']['home_team_id']): result['payload']['home_score'],
                str(f['payload']['away_team_id']): result['payload']['away_score']}
    if set(totals) != teams or any(totals[k]['score'] != expected[k] for k in teams):
        raise ValueError('player score does not reconcile official result')
    return {'source_game_id': GAME, 'fixture_kickoff': f['kickoff'], 'availability': 'postgame_only',
            'source_url': meta['url'], 'body_sha256': meta['body_sha256'],
            'received_at': meta['received_at'], 'timestamp_basis': meta['timestamp_basis'],
            'fixture_body_sha256': f['body_sha256'], 'team_totals': dict(totals), 'players': players}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, default=EVIDENCE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    label = 'kbl-box-S45G01N116'
    meta = json.loads((args.evidence/(label+'.json')).read_text(encoding='utf-8'))
    body = gzip.decompress((args.evidence/(label+'.body.gz')).read_bytes())
    fixtures = [json.loads(row) for row in FIXTURES.read_text(encoding='utf-8').splitlines()]
    args.output.write_text(json.dumps(extract(meta, body, fixtures), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
