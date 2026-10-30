import json, requests, hashlib, datetime, pathlib
out=pathlib.Path('experiments/accuracy-connection-20261030'); ledger=[]; games=[]; upcoming=[]
s=requests.Session(); s.headers.update({'User-Agent':'sportstoto-research/1.0','Referer':'https://www.kleague.com/'})
def post(url, **kw):
 try:
  r=s.post(url,timeout=15,allow_redirects=False,**kw); t=datetime.datetime.now(datetime.timezone.utc).isoformat(); rec={'url':url,'request':kw,'received_at':t,'status':r.status_code,'raw_sha256':hashlib.sha256(r.content).hexdigest()}; ledger.append(rec); r.raise_for_status(); return r.json(),rec
 except Exception as e:
  if not ledger or ledger[-1].get('request')!=kw: ledger.append({'url':url,'request':kw,'received_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'error':type(e).__name__})
  else: ledger[-1]['error']=type(e).__name__
  return None,None
url='https://www.kleague.com/getScheduleList.do'
for y,m,league in [(2025,m,2) for m in range(2,13)]+[(2026,m,l) for m in (10,11) for l in (1,2)]:
 b=dict(leagueId=str(league),teamId='',ticketStatus='',year=str(y),month=f'{m:02}',ticketYn='N'); p,rec=post(url,json=b)
 if not p or p.get('resultCode')!='200': break
 for x in p['data']['scheduleList']:
  if x['year']!=y or x['leagueId']!=league or x['meetSeq']!=league: continue
  try: kickoff=datetime.datetime.strptime(x['gameDate']+' '+x['gameTime'],'%Y.%m.%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
  except (ValueError,TypeError): continue
  if kickoff.month!=m: continue
  row={'league':f'K리그{league}','official_game_id':f"kleague:{y}:{x['meetSeq']}:{x['gameId']}",'home_team_id':x['homeTeam'],'away_team_id':x['awayTeam'],'home':x['homeTeamName'],'away':x['awayTeamName'],'kickoff_at':kickoff.isoformat(),'source_url':url,'raw_sha256':rec['raw_sha256'],'first_received_at':rec['received_at'],'source_published_at_claim':None,'revision_received_at':None,'external_receipt_verified':False,'availability_tier':'retrospective_result' if y==2025 else 'actual_receipt_unverified_externally','product_id':None}
  if y==2025 and x['endYn']=='Y' and x['gameStatus']=='FE' and x['homeGoal'] is not None and x['awayGoal'] is not None:
   row.update(score=[x['homeGoal'],x['awayGoal']],result_final_at=None,settlement_scope='full_match_official_schedule'); games.append(row)
  elif y==2026 and kickoff>datetime.datetime.fromisoformat(rec['received_at']):
   row.update(status=x['gameStatus'],lineup=None); upcoming.append(row)
# KBO official schedules
s.headers['Referer']='https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx'
for day in ('20261030','20261031','20261101'):
 p,rec=post('https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList',data={'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day})
 if not p: continue
 rec['code']=p.get('code'); rec['game_count']=len(p.get('game',[])) if isinstance(p.get('game'),list) else None
 for g in p.get('game',[]):
  try: ko=datetime.datetime.strptime(g['G_DT']+' '+g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
  except (ValueError,KeyError,TypeError): continue
  if ko<=datetime.datetime.fromisoformat(rec['received_at']) or str(g.get('GAME_STATE_SC'))!='1' or str(g.get('CANCEL_SC_ID'))!='0': continue
  row={'league':'KBO','official_game_id':g['G_ID'],'home_team_id':g.get('HOME_ID'),'away_team_id':g.get('AWAY_ID'),'kickoff_at':ko.isoformat(),'product_id':None,'source_url':rec['url'],'raw_sha256':rec['raw_sha256'],'first_received_at':rec['received_at'],'source_published_at_claim':None,'revision_received_at':None,'external_receipt_verified':False,'availability_tier':'actual_receipt_unverified_externally','lineup':None,'starters':[]}
  for side,prefix in [('home','B'),('away','T')]:
   if g.get(prefix+'_PIT_P_NM') and g.get('START_PIT_CK') in (1,True): row['starters'].append({'side':side,'name':g[prefix+'_PIT_P_NM'],'player_id':None,'role':'starting_pitcher','order':None,'announcement_status':'probable','expected_minutes_or_innings':None})
  body,lr=post('https://www.koreabaseball.com/ws/Schedule.asmx/GetLineUpAnalysis',data={'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':g['G_ID']})
  if lr: row['lineup_probe']={'raw_sha256':lr['raw_sha256'],'first_received_at':lr['received_at'],'available':bool(isinstance(body,list) and body and body[0] and body[0][0].get('LINEUP_CK') is True)}
  upcoming.append(row)
for name,obj in [('requests.json',ledger),('official-2025.json',games),('upcoming-probes.json',upcoming)]: (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print('requests',len(ledger),'errors',sum('error' in x for x in ledger),'finals',len(games),'unique',len({x['official_game_id'] for x in games}),'upcoming',len(upcoming))

