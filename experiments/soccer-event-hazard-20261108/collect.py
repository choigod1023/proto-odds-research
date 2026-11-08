"""Sequential bounded StatsBomb acquisition; hashes raw responses, saves compact rows."""
import datetime, hashlib, json, pathlib, time
import requests

OUT = pathlib.Path(__file__).parent
ROOT = 'https://raw.githubusercontent.com/statsbomb/open-data/master/data/'
session = requests.Session()
def get(path):
    response = session.get(ROOT + path, timeout=30)
    response.raise_for_status()
    raw = response.content
    return json.loads(raw), {'url': ROOT + path, 'sha256': hashlib.sha256(raw).hexdigest(), 'received_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'bytes': len(raw)}

def main():
    matches, m = get('matches/2/27.json')
    matches.sort(key=lambda x: (x['match_date'], x['kick_off'], x['match_id']))
    rows, manifest, errors = [], [m], []
    for index, match in enumerate(matches):
        try:
            events, meta = get('events/%s.json' % match['match_id'])
            manifest.append(meta)
            shots = []
            for event in events:
                if event.get('period') not in (1, 2) or event.get('type', {}).get('name') != 'Shot':
                    continue
                shot = event['shot']
                if 'statsbomb_xg' in shot:
                    shots.append([event['team']['id'], event['minute'], event['second'], shot['statsbomb_xg'], shot.get('outcome', {}).get('name')])
            rows.append({'id': match['match_id'], 'date': match['match_date'], 'kickoff': match['kick_off'], 'home': match['home_team']['home_team_name'], 'away': match['away_team']['away_team_name'], 'home_id': match['home_team']['home_team_id'], 'away_id': match['away_team']['away_team_id'], 'home_score': match['home_score'], 'away_score': match['away_score'], 'shots': shots})
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append({'id': match['match_id'], 'error': str(exc)[:200]})
        if (index + 1) % 25 == 0:
            print(index+1, len(rows), len(errors), flush=True)
        time.sleep(.1)
    (OUT/'inputs.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    (OUT/'manifest.json').write_text(json.dumps({'season_matches': len(matches), 'sources': manifest, 'errors': errors}, indent=2), encoding='utf-8')
    print('finished', len(rows), len(errors), flush=True)

if __name__ == '__main__':
    main()
