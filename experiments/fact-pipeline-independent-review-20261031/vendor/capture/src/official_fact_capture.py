"""Minimal, detached official research facts; no database or model integration."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

KBO_URL = 'https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList'
MLB_URL = 'https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live'
KST = timezone(timedelta(hours=9))

def timestamp(value):
    if not isinstance(value, str): raise ValueError('timestamp_required')
    try: dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc: raise ValueError('invalid_timestamp') from exc
    if dt.utcoffset() is None: raise ValueError('timezone_required')
    return dt.astimezone(timezone.utc)

def semantic_hash(record):
    """Identity/value content only: receipt, URLs, document revisions and prices are not new player facts."""
    core = {k:record[k] for k in ('official_game_id','league','sport','kind','after_value','units','player_ids','kickoff_at')}
    core['kickoff_at'] = timestamp(core['kickoff_at']).isoformat()
    if isinstance(core['after_value'],dict):
        core['after_value'] = {k:v for k,v in core['after_value'].items() if k != 'player_name'}
    payload=json.dumps(core,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def make_fact(*, official_game_id, league, sport, kind, after_value, player_ids, kickoff_at, url, raw, received_at, confirmed):
    if not isinstance(raw,bytes) or not raw: raise ValueError('raw_response_required')
    received=timestamp(received_at); kickoff=timestamp(kickoff_at)
    if received >= kickoff-timedelta(minutes=30): raise ValueError('not_before_t30')
    if not player_ids or any(not isinstance(x,str) or not x.strip() for x in player_ids): raise ValueError('player_id_required')
    fact={'fact_id':None,'canonical_game_id':None,'official_game_id':official_game_id,'league':league,'sport':sport,'kind':kind,
          'before_value':None,'after_value':after_value,'units':None,'player_ids':player_ids,
          'claim':{'url':None,'published_at':None,'received_at':None,'raw_sha256':None},
          'official':{'url':url,'published_at':None,'received_at':received.isoformat(),'raw_sha256':hashlib.sha256(raw).hexdigest(),
                      'identity_checked':True,'confirmed':confirmed,'external_receipt_verified':False},
          'decision_at':None,'kickoff_at':kickoff.isoformat(),
          'market':{'product_id':None,'market':None,'period':None,'line':None,'outcomes':None,'odds':None,'observed_at':None,'received_at':None},
          'revision':None,'missing_reasons':['canonical_game_id_not_independently_matched','no_prior_value','no_tipster_claim','no_decision','no_market_quote','no_external_receipt_witness']}
    fact['semantic_sha256']=semantic_hash(fact)
    fact['fact_id']='official-fact-v1:'+fact['semantic_sha256']
    return fact

def normalize_kbo_schedule(raw, *, expected_date, received_at):
    payload=json.loads(raw)
    if str(payload.get('code'))!='100' or not isinstance(payload.get('game'),list): raise ValueError('invalid_kbo_calendar')
    if not (len(expected_date)==8 and expected_date.isdigit()): raise ValueError('invalid_date')
    games=[]; facts=[]; seen=set(); missing=[]
    for g in payload['game']:
        if str(g.get('LE_ID'))!='1' or str(g.get('G_DT'))!=expected_date: raise ValueError('calendar_date_or_league_mismatch')
        gid=g.get('G_ID')
        if not isinstance(gid,str) or not gid or gid in seen: raise ValueError('missing_or_duplicate_game_id')
        seen.add(gid)
        if str(g.get('GAME_STATE_SC'))!='1' or str(g.get('CANCEL_SC_ID'))!='0': continue
        try: kickoff=datetime.strptime(g['G_DT']+' '+g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=KST)
        except (KeyError,TypeError,ValueError) as exc: raise ValueError('invalid_kickoff') from exc
        if timestamp(received_at)>=kickoff.astimezone(timezone.utc)-timedelta(minutes=30): continue
        if not g.get('HOME_ID') or not g.get('AWAY_ID') or g['HOME_ID']==g['AWAY_ID']: raise ValueError('invalid_teams')
        if not gid.startswith(expected_date+str(g['AWAY_ID'])+str(g['HOME_ID'])): raise ValueError('game_id_team_mismatch')
        home_pid, away_pid = g.get('B_PIT_P_ID'), g.get('T_PIT_P_ID')
        if home_pid and home_pid == away_pid: raise ValueError('same_player_on_both_teams')
        game={'official_game_id':gid,'league':'KBO','kickoff_at':kickoff.isoformat(),'home_team_id':g['HOME_ID'],'away_team_id':g['AWAY_ID'],
              'lineup_request':{'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':gid}}
        games.append(game)
        for side,prefix,team in [('home','B',g['HOME_ID']),('away','T',g['AWAY_ID'])]:
            pid=g.get(prefix+'_PIT_P_ID'); name=(g.get(prefix+'_PIT_P_NM') or '').strip()
            if g.get('START_PIT_CK') not in (1,True) or type(pid) is not int or pid<=0 or not name:
                missing.append({'official_game_id':gid,'side':side,'kind':'probable_starting_pitcher','reason':'not_announced_or_player_id_missing'}); continue
            facts.append(make_fact(official_game_id=gid,league='KBO',sport='baseball',kind='probable_starting_pitcher',
                after_value={'side':side,'team_id':str(team),'player_id':str(pid),'player_name':name,'announcement_status':'probable'},
                player_ids=[str(pid)],kickoff_at=kickoff.isoformat(),url=KBO_URL,raw=raw,received_at=received_at,confirmed=False))
    return {'games':games,'facts':facts,'missing':missing}

def normalize_mlb_feed(raw, *, game_pk, scheduled_at, received_at):
    """Use existing MLB feed guards; a probable pitcher is never final confirmation."""
    from mlb_candidate_feed import extract
    if type(game_pk) is not int or game_pk<=0: raise ValueError('invalid_game_pk')
    url=MLB_URL.format(game_pk=game_pk); feed=json.loads(raw)
    capture=extract(feed,game_pk=game_pk,scheduled_at=scheduled_at,received_at=received_at,source_url=url,response_sha256=hashlib.sha256(raw).hexdigest())
    out=[]
    for side in ('home','away'):
        game_team = ((feed.get('gameData') or {}).get('teams') or {}).get(side) or {}
        box_team = ((((feed.get('liveData') or {}).get('boxscore') or {}).get('teams') or {}).get(side) or {}).get('team') or {}
        if type(game_team.get('id')) is not int or game_team['id'] != box_team.get('id'):
            raise ValueError('mlb_team_identity_mismatch')
        team_id = str(game_team['id'])
        row=capture['sides'][side]; starter=row['starter']; pid=str(starter['id'])
        out.append(make_fact(official_game_id=str(game_pk),league='MLB',sport='baseball',kind='probable_starting_pitcher',
            after_value={'side':side,'team_id':team_id,'player_id':pid,'player_name':starter['name'],'announcement_status':'probable'},player_ids=[pid],
            kickoff_at=scheduled_at,url=url,raw=raw,received_at=received_at,confirmed=False))
        lineup=row['lineup']; ids=[str(x['id']) for x in lineup]
        out.append(make_fact(official_game_id=str(game_pk),league='MLB',sport='baseball',kind='announced_batting_order',
            after_value={'side':side,'team_id':team_id,'player_ids':ids,'announcement_status':'confirmed'},player_ids=ids,
            kickoff_at=scheduled_at,url=url,raw=raw,received_at=received_at,confirmed=True))
    return out
