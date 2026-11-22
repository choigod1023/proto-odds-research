"""Collect bounded training event paths, independent of held-out events."""
import datetime, hashlib, json, pathlib, time
import requests

HERE = pathlib.Path(__file__).parent
rows = json.loads((HERE/'inputs.json').read_text(encoding='utf8'))
session = requests.Session()
paths, manifest = {}, []
for index, match in enumerate(r for r in rows if r['date'] < '2016-02-01'):
    url = 'https://raw.githubusercontent.com/statsbomb/open-data/master/data/events/%s.json' % match['id']
    response = session.get(url, timeout=30); response.raise_for_status()
    events = response.json(); path = []
    for e in events:
        if e.get('period') not in (1,2): continue
        typ = e.get('type',{}).get('name'); team = e.get('team',{}).get('id')
        if team not in (match['home_id'],match['away_id']): continue
        goal = typ == 'Shot' and e.get('shot',{}).get('outcome',{}).get('name') == 'Goal'
        own = typ == 'Own Goal Against'
        card = e.get('foul_committed',{}).get('card',{}).get('name') or e.get('bad_behaviour',{}).get('card',{}).get('name')
        if goal or own or card in ('Red Card','Second Yellow'):
            if own: team = match['away_id'] if team == match['home_id'] else match['home_id']
            path.append([min(89, e['minute']), team, 'goal' if goal or own else 'red'])
    paths[str(match['id'])] = path
    manifest.append({'url':url,'sha256':hashlib.sha256(response.content).hexdigest(),'received_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    if (index+1)%25 == 0: print(index+1,flush=True)
    time.sleep(.1)
(HERE/'training_paths.json').write_text(json.dumps(paths),encoding='utf8')
(HERE/'state_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
print('done',len(paths),flush=True)
