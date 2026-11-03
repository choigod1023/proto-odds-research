"""Freeze official 2026 denominator from all twelve saved monthly responses."""
import hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parent

def main():
    logs=json.loads((B/'schedule-requests.json').read_text(encoding='utf8'));rows=[]
    if len(logs)!=12 or {e['key'] for e in logs}!={f'2026-{m:02d}' for m in range(1,13)}:raise ValueError('incomplete month coverage')
    for e in logs:
        data=(B/e['raw_file']).read_bytes()
        if e['status']!=200 or hashlib.sha256(data).hexdigest()!=e['sha256']:raise ValueError('invalid schedule evidence')
        doc=json.loads(data)
        if str(doc.get('resultCode'))!='200':raise ValueError('schedule error')
        for r in doc['data']['scheduleList']:
            if str(r.get('year'))!='2026' or str(r.get('leagueId'))!='1' or str(r.get('meetSeq'))!='1':continue
            if not str(r.get('gameDate','')).startswith(e['key'].replace('-','.')):raise ValueError('month mismatch')
            rows.append({'fixture_id':f"kleague:2026:1:{r['gameId']}",'year':2026,'game_id':int(r['gameId']),'day':r['gameDate'].replace('.','-'),
              'kickoff_local_display':r.get('gameTime'),'home_team_id':r['homeTeam'],'away_team_id':r['awayTeam'],'home':r['homeTeamName'],'away':r['awayTeamName'],
              'game_status':r['gameStatus'],'end_yn':r['endYn'],'home_goal':r['homeGoal'],'away_goal':r['awayGoal'],'schedule_evidence':e['raw_file']})
    if len({r['fixture_id'] for r in rows})!=len(rows):raise ValueError('duplicate fixture ID')
    rows.sort(key=lambda x:(x['day'],x['game_id']))
    if len(rows)!=228:raise ValueError('season denominator differs from expected 228; inspect manually')
    complete=[r for r in rows if r['game_status']=='FE' and r['end_yn']=='Y']
    plan={'schema_version':'1.0','order':['2026','2025','2024','2023'],
       'seasons':{'2026':{'official_scheduled_fixtures':len(rows),'completed':len(complete),'not_completed':len(rows)-len(complete),'target_status':'collect_completed_first'},
       '2025':{'official_scheduled_fixtures':228,'status':'pending; prior PR #484 season result denominator'},
       '2024':{'official_scheduled_fixtures':228,'status':'pending; prior PR #486 season result denominator'},
       '2023':{'official_scheduled_fixtures':228,'status':'pending; prior PR #486 season result denominator'}},
       'schedule_observation':'current official monthly schedule; not proof of historical pregame receipt',
       'targets':rows}
    (B/'cohort-plan.json').write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print('2026 denominator',len(rows),'finished',len(complete),'not finished',len(rows)-len(complete))
if __name__=='__main__':main()
