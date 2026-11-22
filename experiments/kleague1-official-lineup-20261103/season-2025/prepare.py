"""Seed frozen 2025 official schedule and one previously captured match, no network."""
import gzip,hashlib,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
BASE=Path(__file__).resolve().parent
OLD=Path('C:/Users/user/Documents/Codex/2026-10-31/sportstoto-league-kleague1-20261031/kleague-cohort')
COMMIT='566301644c5d427e2e8a6b6492e2bdd69f119279'
PREFIX='experiments/verified-cohort-20261101/kleague1/'

def digest(raw):return hashlib.sha256(raw).hexdigest()
def save_once(path,raw):
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('existing evidence differs: '+str(path))
    else:path.write_bytes(raw)
def blob(name):return subprocess.check_output(['git','show',COMMIT+':'+PREFIX+name],cwd=OLD)

def main():
    (BASE/'raw').mkdir(exist_ok=True)
    manifest=json.loads(blob('manifest.json')); logs=json.loads(blob('post-requests.json'))
    if len(logs)!=10:raise ValueError('schedule source coverage incomplete')
    for e in logs:
        name='schedule-2025-'+e['payload']['month']+'.json';raw=blob(name)
        if digest(raw)!=e['sha256'] or manifest['source_hashes'][name]!=e['sha256']:raise ValueError('official schedule source hash mismatch')
        save_once(BASE/'raw'/name,raw)
    save_once(BASE/'schedule-requests.json',blob('post-requests.json'))
    season=[]
    for e in logs:
        name='schedule-2025-'+e['payload']['month']+'.json';doc=json.loads((BASE/'raw'/name).read_bytes())
        if str(doc.get('resultCode'))!='200':raise ValueError('official schedule failure')
        for r in doc['data']['scheduleList']:
            if str(r.get('year'))!='2025' or str(r.get('leagueId'))!='1' or str(r.get('meetSeq'))!='1':continue
            if not str(r.get('gameDate','')).startswith('2025.'+e['payload']['month']+'.'):raise ValueError('schedule month mismatch')
            season.append({'fixture_id':f"kleague:2025:1:{r['gameId']}",'year':2025,'game_id':int(r['gameId']),'day':r['gameDate'].replace('.','-'),
             'home_team_id':r['homeTeam'],'away_team_id':r['awayTeam'],'home_goal':r['homeGoal'],'away_goal':r['awayGoal'],'status':r['gameStatus'],'end_yn':r['endYn'],'schedule_evidence':'raw/'+name})
    season.sort(key=lambda x:(x['day'],x['game_id']))
    if len(season)!=228 or len({r['fixture_id'] for r in season})!=228 or any(r['status']!='FE' or r['end_yn']!='Y' for r in season):raise ValueError('2025 completed fixture denominator or uniqueness mismatch')
    plan={'schema_version':'1.0','year':2025,'scheduled':228,'completed':228,'source_commit':COMMIT,'source':'official league schedule 2025-02 to 2025-11; requests actually received 2026-11-01','selection':'all finished fixtures in schedule date order; not selected by result or ROI','targets':season}
    save_once(BASE/'plan.json',(json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    root_log=json.loads((ROOT/'requests.json').read_text(encoding='utf8'));pilot=[e for e in root_log if e.get('raw_file') in ('raw/match-2025-96.html.gz','raw/info-2025-96.json')]
    if len(pilot)!=2:raise ValueError('pilot evidence missing or duplicate')
    for e in pilot:
        raw=(ROOT/e['raw_file']).read_bytes();body=gzip.decompress(raw) if e['raw_file'].endswith('.gz') else raw
        if digest(body)!=e['sha256']:raise ValueError('pilot source hash mismatch')
        save_once(BASE/e['raw_file'],raw)
    path=BASE/'requests.json'
    if not path.exists():path.write_bytes((json.dumps(pilot,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    else:
        current=json.loads(path.read_text(encoding='utf8'))
        if not all(any(e==x for x in current) for e in pilot):raise ValueError('existing request log lacks pilot evidence')
    print('2025 official completed fixtures:',len(season),'pilot cache: 1')
if __name__=='__main__':main()
