"""Resolve archived KBO prediction identities without using scores or outcomes."""
import argparse
import base64
import collections
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('C:/Users/user/Documents/Codex')
INPUTS = {
    'features': (BASE/'kbeffects/experiments/kbo-player-effects-20261103/inputs/features.jsonl.gz', '346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42'),
    'fresh': (BASE/'kbeffects/experiments/kbo-player-effects-20261103/inputs/fresh-history-2026-official_batting_and_starter.jsonl.gz', '82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706'),
    'starters': (BASE/'kbtmodel/experiments/kbo-actual-training-20261103/inputs/actual-starters-2026.jsonl.gz', 'ca1d6dbc1f301ee4681237cbf1cd864df5079a3ab9364c2f7b50129a9574a69d'),
    'official_raw': (BASE/'kbtdata/experiments/kbo-actual-training-data-20261103/actual-starters-2026-raw.jsonl.gz', '1203fd23f085c56937273a5a42c0780ed703369b4b715fededc0a84a82e6e786'),
    'forecast': (BASE/'market-outcomes-validation/experiments/m1-service-20261104/replay/replayed-709.json.gz', 'a597ed066534cce92cfb12606d7f7e3edcc7591b3a6c1d17e7777c8b04e8b505'),
    'manifest': (BASE/'expanded-kbo-markets-20261105-worker/experiments/expanded-kbo-markets-20261105/source-manifest.json', '2c0aa54de595bf9d44e0f4bba2d0666f1eaf311294c17667313b28a6415d9b5f'),
}
TEAM = {'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}
KST = dt.timezone(dt.timedelta(hours=9))


def sha(raw): return hashlib.sha256(raw).hexdigest()

def load_pinned(key):
    path, expected = INPUTS[key]
    raw = path.read_bytes()
    if sha(raw) != expected: raise ValueError(f'{key}: hash mismatch')
    return raw

def jsonlines(raw): return [json.loads(line) for line in gzip.decompress(raw).splitlines() if line]

def index_unique(rows):
    result = {}
    for r in rows:
        fid = r['fixture_id']
        if fid in result: raise ValueError('duplicate fixture_id: '+fid)
        result[fid] = r
    return result

def resolve_one(feature, fresh, starter, games, quotes):
    """Return identity/proof or fail closed. Quotes include clocks, never outcomes."""
    day = feature['event_date']; date = day.replace('-', '')
    home, away = TEAM.get(feature['home_id']), TEAM.get(feature['away_id'])
    if not home or not away or home == away: raise ValueError('invalid_team')
    schedule, lineup = fresh['official_schedule_pitcher_context'], fresh['official_lineup_context']
    gid = schedule['official_game_id']
    if (len(gid) != 13 or gid[:8] != date or gid[8:10] != away or gid[10:12] != home
        or gid != lineup['official_game_id'] or gid != starter['official_game_id']
        or lineup['home_team_id'] != home or lineup['away_team_id'] != away
        or schedule['pitchers']['home']['team_id'] != home or schedule['pitchers']['away']['team_id'] != away):
        raise ValueError('identity_conflict')
    matching = [g for g in games if str(g.get('G_DT')) == date and g.get('HOME_ID') == home and g.get('AWAY_ID') == away]
    if len(matching) != 1: raise ValueError('schedule_not_unique')
    game = matching[0]
    if game.get('G_ID') != gid: raise ValueError('schedule_id_conflict')
    if game.get('HEADER_NO') != 0 or gid[-1] != '0': raise ValueError('doubleheader')
    if not quotes: raise ValueError('betman_missing')
    if len(quotes) != 1: raise ValueError('betman_kickoff_not_unique')
    clock, proofs = next(iter(quotes.items()))
    official_time = str(game.get('G_TM'))
    if clock.strftime('%H:%M') != official_time or clock.second != 0 or clock.microsecond != 0: raise ValueError('kickoff_mismatch:'+official_time+':'+clock.strftime('%H:%M'))
    return {'official_game_id': gid, 'event_date': day, 'home_id': home, 'away_id': away,
            'kickoff': clock.isoformat(), 'schedule_body_sha256': schedule['schedule_evidence']['sha256'],
            'betman_sources': sorted(proofs, key=lambda p:(p['round'],p['body_sha256']))}

def run():
    raw = {k:load_pinned(k) for k in INPUTS}
    forecasts = json.loads(gzip.decompress(raw['forecast']))
    features, fresh, starters = (index_unique(jsonlines(raw[k])) for k in ('features','fresh','starters'))
    synthetic = {k for k in forecasts if k.startswith('score:')}
    official = set(forecasts)-synthetic
    if len(forecasts)!=709 or len(synthetic)!=239 or len(official)!=470: raise ValueError('unexpected forecast population')
    if any(len(k)!=17 or not k[:8].isdigit() or not k.endswith('2026') for k in official): raise ValueError('invalid existing official forecast ID')
    if not synthetic <= features.keys() or not synthetic <= fresh.keys() or not synthetic <= starters.keys(): raise ValueError('missing input identity')
    wanted = {fresh[k]['official_schedule_pitcher_context']['schedule_evidence']['sha256'] for k in synthetic}
    if len(wanted)!=66: raise ValueError('unexpected official schedule count')
    bodies = {}
    for record in jsonlines(raw['official_raw']):
        if record['sha256'] in wanted:
            body = base64.b64decode(record['raw_base64'], validate=True)
            if sha(body)!=record['sha256'] or len(body)!=record['bytes']: raise ValueError('official body hash mismatch')
            if record['sha256'] in bodies: raise ValueError('duplicate official body')
            bodies[record['sha256']] = json.loads(body)['game']
    if set(bodies)!=wanted: raise ValueError('missing official schedule body')
    manifest = json.loads(raw['manifest'])
    if len(manifest['sources'])!=220: raise ValueError('unexpected union manifest count')
    quotes = collections.defaultdict(lambda: collections.defaultdict(list))
    for src in manifest['sources']:
        path = Path(manifest['source_roots'][src['source_root_index']])/src['body_file']
        body = gzip.decompress(path.read_bytes())
        if sha(body)!=src['body_sha256']: raise ValueError('Betman body hash mismatch: '+str(path))
        data = json.loads(body)['compSchedules']
        for vals in data['datas']:
            row = dict(zip(data['keys'], vals))
            if row.get('leagueCode')!='BS001': continue
            time = dt.datetime.fromtimestamp(row['gameDate']/1000, KST)
            key = (time.date().isoformat(), row['homeId'], row['awayId'])
            proof = {'round': src['round'], 'body_sha256':src['body_sha256'], 'body_path':str(path)}
            if proof not in quotes[key][time]: quotes[key][time].append(proof)
    resolved, excluded = {}, []
    for fid in sorted(synthetic):
        feature = features[fid]; context = fresh[fid]['official_schedule_pitcher_context']
        key = (feature['event_date'], TEAM.get(feature['home_id']), TEAM.get(feature['away_id']))
        try: resolved[fid] = resolve_one(feature, fresh[fid], starters[fid], bodies[context['schedule_evidence']['sha256']], quotes.get(key, {}))
        except ValueError as exc: excluded.append({'fixture_id':fid, 'reason':str(exc)})
    gids = [v['official_game_id'] for v in resolved.values()]
    if len(gids)!=len(set(gids)): raise ValueError('resolved ID collision')
    # Existing forecast IDs have season appended to the official game ID.
    if any(gid+'2026' in official for gid in gids): raise ValueError('collision with existing official forecast')
    if len(resolved)!=237 or len(excluded)!=2 or any(not e['reason'].startswith('kickoff_mismatch:') for e in excluded): raise ValueError('unexpected eligibility population')
    provenance = {k:{'path':str(path),'sha256':digest} for k,(path,digest) in INPUTS.items()}
    output = {'schema':'kbo-synthetic-identity-resolution-v1','inputs':provenance,'resolved':resolved}
    audit = {'schema':'kbo-synthetic-identity-audit-v1','inputs':provenance,'forecast_count':709,'existing_official_count':470,
             'synthetic_count':239,'official_schedule_body_count':len(bodies),'betman_body_count':len(manifest['sources']),
             'resolved_count':len(resolved),'excluded_count':len(excluded),'excluded':excluded,
             'note':'Identity evidence is retrospective; no historical receipt or execution price proven. Scores and outcomes are not used.'}
    return output, audit

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=HERE);args=parser.parse_args()
    output,audit=run(); args.output_dir.mkdir(parents=True,exist_ok=True)
    for name,obj in [('resolved.json',output),('audit.json',audit)]:
        (args.output_dir/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'resolved':audit['resolved_count'],'excluded':audit['excluded']},ensure_ascii=False))
if __name__=='__main__': main()
