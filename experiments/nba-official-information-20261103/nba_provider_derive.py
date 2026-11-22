import datetime,gzip,hashlib,json,re,unicodedata
from collections import Counter
from pathlib import Path
import nba_provider_parse as census_parse
from audit import read_props

ROOT=Path(__file__).resolve().parent;D=ROOT/'provider'

def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',s.casefold()) if c.isalnum())

def summary(row, expected):
    d=json.loads(census_parse.checked_body(row,D)); h=d['header']; c=h['competitions'][0]
    if h['id']!=expected['provider_game_id'] or c['id']!=h['id'] or not c['status']['type']['completed']:raise ValueError('identity_or_nonfinal')
    comps={x['homeAway']:x for x in c['competitors']}
    if {k:comps[k]['team']['id'] for k in ('home','away')}!={k:expected[k+'_team_id'] for k in ('home','away')}:raise ValueError('team_mismatch')
    if c['date']!=expected['tipoff_utc']:raise ValueError('tipoff_mismatch')
    box={t['team']['id']:t for t in d['boxscore']['players']}; teams=[]
    for side in ('away','home'):
        x=comps[side]; tid=x['team']['id']; athletes=[a for group in box[tid]['statistics'] for a in group['athletes']]
        starters=[{'id_namespace':'espn','player_id':a['athlete']['id'],'name':a['athlete']['displayName']} for a in athletes if a.get('starter') is True]
        if len(starters)!=5 or len({a['player_id'] for a in starters})!=5:raise ValueError('invalid_starters')
        teams.append({'side':side,'provider_team_id':tid,'name':x['team']['displayName'],'score':int(x['score']),'postgame_starters':starters,'postgame_dnp_count':sum(a.get('didNotPlay') is True for a in athletes)})
    return {'provider':'ESPN','provider_game_id':h['id'],'game_day_et':expected['game_day_et'],'tipoff_utc':c['date'],'namespace':'postgame_only','teams':teams,'source_body_sha256':row['body_sha256'],'received_at':row['received_at']}

def main():
    ledger=json.loads((D/'requests.json').read_text(encoding='utf8'));by={r['name']:r for r in ledger}; census=json.loads((D/'census.json').read_text(encoding='utf8'));rows=[];fail=[]
    for x in census['selected']:
        r=by.get('summary-'+x['provider_game_id'])
        if not r or r.get('status')!=200:fail.append({'game_id':x['provider_game_id'],'reason':'missing_or_failed'});continue
        try:rows.append(summary(r,x))
        except (ValueError,KeyError,TypeError,IndexError) as e:fail.append({'game_id':x['provider_game_id'],'reason':str(e)})
    (D/'postgame.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (D/'summary-exclusions.json').write_text(json.dumps(fail,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    links=[('0022500274','401810129','2025-11-22'),('0022500585','401810440','2026-01-15'),('0022501197','401811052','2026-04-12')]; cross=[]
    for nbaid,eid,date in links:
        r=by.get('summary-'+eid);score=by.get('scoreboard-'+date)
        if not r or not score or r.get('status')!=200 or score.get('status')!=200:cross.append({'nba_game_id':nbaid,'espn_game_id':eid,'status':'unavailable'});continue
        board=json.loads(census_parse.checked_body(score,D));events=[e for e in board['events'] if e['id']==eid]
        if len(events)!=1:raise ValueError('crosscheck scoreboard identity')
        e=events[0];c=e['competitions'][0];teams={x['homeAway']:x['team'] for x in c['competitors']}
        exp={'provider_game_id':eid,'game_day_et':date,'tipoff_utc':e['date'],'home_team_id':teams['home']['id'],'away_team_id':teams['away']['id']}
        er=summary(r,exp);ng=read_props(ROOT/'raw'/('box-'+nbaid+'.html'))['game']
        result={'nba_game_id':nbaid,'espn_game_id':eid,'tipoff_match':datetime.datetime.fromisoformat(ng['gameTimeUTC'].replace('Z','+00:00'))==datetime.datetime.fromisoformat(er['tipoff_utc'].replace('Z','+00:00')),'team_match':True,'score_match':True,'starters_match':True,'teams':[]}
        for side in ('away','home'):
            n=ng[side+'Team'];p=next(x for x in er['teams'] if x['side']==side)
            nteam=n['teamCity']+' '+n['teamName'];team_ok=norm(nteam)==norm(p['name']); score_ok=n['score']==p['score']; nn={norm(a['firstName']+' '+a['familyName']) for a in n['players'] if a.get('position')};pn={norm(a['name']) for a in p['postgame_starters']}; players_ok=nn==pn and len(nn)==5
            result['team_match'] &=team_ok;result['score_match'] &=score_ok;result['starters_match'] &=players_ok;result['teams'].append({'side':side,'team_name_match':team_ok,'score_match':score_ok,'starter_names_match':players_ok,'nba_starters_count':len(nn),'espn_starters_count':len(pn)})
        result['status']='compared';cross.append(result)
    (D/'official-crosscheck.json').write_text(json.dumps(cross,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'selected':len(census['selected']),'summaries_valid':len(rows),'failures':fail,'crosscheck':cross},ensure_ascii=False))

if __name__=='__main__':main()
