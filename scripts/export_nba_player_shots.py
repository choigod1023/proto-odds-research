"""Postgame player shot histories; no target-game actual lineup as pregame input."""
import argparse,collections,datetime as dt,gzip,hashlib,io,json,math,pathlib,re,unicodedata
POOL_SHA='1899869c9b8a0cff51e9f2ed7cce57cf08d9a18b51213f46042d9361ebfcd691'
PRED_SHA='fd71c7ecfebb88f432cf2c4ea3ff621f993645e5ab7cec8a8df65fceb6a84a75'

def digest(data):return hashlib.sha256(data).hexdigest()
def read_verified(path,sha):
    data=path.read_bytes()
    if digest(data)!=sha:raise ValueError('hash mismatch '+str(path))
    return data

def timestamp(value):
    x=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if x.tzinfo is None:raise ValueError('timezone required')
    return x

def validate_pregame(e,cutoff,tipoff,source_bytes,expected_game_id):
    allowed={'kind','source_sha256','game_id','teams','captured_at','published_at'}
    if set(e)-allowed:raise ValueError('unexpected or postgame fields prohibited')
    if e.get('kind')!='confirmed_pregame_lineup' or e.get('game_id')!=expected_game_id:raise ValueError('no matching confirmed source')
    if digest(source_bytes)!=e.get('source_sha256'):raise ValueError('source hash mismatch')
    teams=e.get('teams')
    if not isinstance(teams,dict) or set(teams)!={'home','away'} or any(not isinstance(v,list) or len(v)!=5 for v in teams.values()):raise ValueError('expected five named athletes per side')
    ids=teams['home']+teams['away']
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=10:raise ValueError('duplicate or invalid athletes')
    c=timestamp(cutoff);t=timestamp(tipoff)
    if c>t or timestamp(e['captured_at'])>c or timestamp(e['published_at'])>c:raise ValueError('late evidence')
    return True

def minutes(value):
    if re.fullmatch(r'\d+',value):return int(value)*60,60
    m=re.fullmatch(r'(\d+):(\d{2})',value)
    if m and int(m[2])<60:return int(m[1])*60+int(m[2]),1
    return None,None

def normalize(s):
    s=unicodedata.normalize('NFKD',s).casefold();return ''.join(c for c in s if c.isalnum() and not unicodedata.combining(c))
def shot_actor(p,team_players):
    text=p.get('text','');parts=p.get('participants') or []
    if not parts:return None,'no_participant'
    pid=parts[0].get('athlete',{}).get('id')
    if pid not in team_players:return None,'first_participant_not_on_team'
    if ' blocks ' in text:actor=text.split(' blocks ',1)[1].split("'s",1)[0].strip()
    elif ' makes ' in text:actor=text.split(' makes ',1)[0].strip()
    elif ' misses ' in text:actor=text.split(' misses ',1)[0].strip()
    else:return None,'unparseable_actor'
    if normalize(actor)!=normalize(team_players[pid]['name']):return None,'name_id_disagreement'
    return pid,None

def classify(p):
    text=p.get('text','').lower();n=p.get('pointsAttempted');made=p.get('scoringPlay') is True
    if not p.get('shootingPlay') or n not in (1,2,3):return None
    if not (('makes ' in text) if made else ('misses ' in text or ' blocks ' in text)):return None
    if n==1 and 'free throw' not in text:return None
    if n in (2,3) and 'free throw' in text:return None
    if n==3 and ('two point' in text or '2-point' in text):return None
    if n==2 and ('three point' in text or '3-point' in text):return None
    if p.get('scoreValue')!=(n if made else 0):return None
    return n,made

def hard(n,c):
    c=c if isinstance(c,dict) else {};x,y=c.get('x'),c.get('y')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000 for v in (x,y)):return 4 if n==2 else 8
    r=math.hypot(x-25,y)
    if n==2:return 0 if r<4 else 1 if r<10 else 2 if r<16 else 3
    return 5 if r<24 else 6 if r<27 else 7

def soft(n,c):
    i=hard(n,c)
    if i in (4,8):return {i:1.}
    r=math.hypot(c['x']-25,c['y']);lo=max(0.,r-1);hi=r+1;edges=[0,4,10,16,math.inf] if n==2 else [0,24,27,math.inf];offset=0 if n==2 else 5
    return {offset+i:w for i in range(len(edges)-1) if (w:=max(0,min(hi,edges[i+1])-max(lo,edges[i]))/(hi-lo))>0}

def pair(s):
    m=re.fullmatch(r'(\d+)-(\d+)',s)
    if not m:raise ValueError('invalid box statistic')
    return int(m[1]),int(m[2])
def gzip_rows(rows):
    buf=io.BytesIO()
    with gzip.GzipFile(fileobj=buf,mode='wb',filename='',mtime=0) as z:
        for r in rows:z.write((json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode())
    return buf.getvalue()

def export(poolfile,predfile,archive,out):
    pool=[json.loads(x) for x in gzip.decompress(read_verified(poolfile,POOL_SHA)).splitlines()];pred=[json.loads(x) for x in gzip.decompress(read_verified(predfile,PRED_SHA)).splitlines()]
    if len(pool)!=1185 or len({g['game_id'] for g in pool})!=1185 or len(pred)!=1260:raise ValueError('unexpected pool size')
    rows=[];counts=collections.Counter();examples=[];team_audits=[]
    for g in pool:
        gid=g['game_id'];receipt=archive/'summary'/f'{gid}.receipt.json';rec=json.loads(receipt.read_bytes())
        if rec['status']!=200 or rec['sha256']!=g['source_summary_sha256']:raise ValueError('receipt mismatch '+gid)
        d=json.loads(read_verified(receipt.with_name(gid+'.json'),g['source_summary_sha256']));comp=d['header']['competitions'][0];cs={x['homeAway']:x for x in comp['competitors']}; teamids=[cs[x]['id'] for x in ('home','away')]
        if d['header']['id']!=gid or comp['date']!=g['date_utc'] or [cs[x]['team']['abbreviation'] for x in ('home','away')]!=g['teams']:raise ValueError('identity '+gid)
        players={};metadata={}
        for block in d['boxscore']['players']:
            tid=block['team']['id'];players[tid]={};metadata[tid]=0
            if tid not in teamids or len(block.get('statistics',[]))!=1:raise ValueError('player box group '+gid)
            stats=block['statistics'][0];keys=stats['keys']
            for entry in stats['athletes']:
                aid=entry['athlete'].get('id')
                if not aid:
                    vals=entry.get('stats',[])
                    if not vals or (vals[0]=='--' and all(x in ('0','0-0','--') for x in vals[1:])):
                        counts['missing_athlete_id_without_recorded_activity']+=1;metadata[tid]+=1;continue
                    raise ValueError('missing athlete id with activity '+gid)
                v=dict(zip(keys,entry.get('stats',[])));rawmin=v.get('minutes');seconds,precision=minutes(rawmin) if rawmin is not None else (None,None)
                if aid in players[tid]:raise ValueError('duplicate athlete '+gid)
                box={}
                for key,target in [('fieldGoalsMade-fieldGoalsAttempted','fg'),('threePointFieldGoalsMade-threePointFieldGoalsAttempted','three'),('freeThrowsMade-freeThrowsAttempted','ft')]:
                    if key in v:
                        made,attempt=pair(v[key]);box[target+'m']=made;box[target+'a']=attempt
                players[tid][aid]={'name':entry['athlete']['displayName'],'starter_postgame':entry.get('starter'),'did_not_play':entry.get('didNotPlay'),'minutes_source':rawmin,'minutes_seconds_displayed':seconds,'minutes_precision_seconds':precision,'box':box,'hard_attempts':[0]*9,'hard_makes':[0]*9,'soft_attempts':[0.]*9,'soft_makes':[0.]*9,'fta':0,'ftm':0}
        if set(players)!=set(teamids):raise ValueError('missing team players '+gid)
        bad={tid:collections.Counter() for tid in teamids};seen=set()
        for p in d['plays']:
            pid=p.get('id')
            if not pid or pid in seen:raise ValueError('duplicate play '+gid)
            seen.add(pid)
            if not p.get('shootingPlay'):continue
            result=classify(p);tid=p.get('team',{}).get('id')
            if result is None or tid not in players:raise ValueError('ambiguous team shot '+gid)
            n,made=result;counts['shooting_events']+=1
            aid,reason=shot_actor(p,players[tid])
            if not aid:
                bad[tid][f'{n}a']+=1;bad[tid][f'{n}m']+=int(made);counts['unattributed_'+reason]+=1
                if len(examples)<30:examples.append({'game_id':gid,'play_id':pid,'text':p.get('text'),'reason':reason})
                continue
            counts['attributed_events']+=1;counts['multi_participant_events']+=int(len(p.get('participants',[]))>1);a=players[tid][aid]
            if n==1:a['fta']+=1;a['ftm']+=int(made);continue
            i=hard(n,p.get('coordinate'));a['hard_attempts'][i]+=1;a['hard_makes'][i]+=int(made)
            for i,w in soft(n,p.get('coordinate')).items():a['soft_attempts'][i]+=w;a['soft_makes'][i]+=w*int(made)
        overtime=max(0,len(cs['home'].get('linescores',[]))-4);expected_seconds=(240+25*overtime)*60
        for i,tid in enumerate(teamids):
            team=players[tid];totals=collections.Counter();raw_seconds=0;precision_sum=0;missing_minutes=metadata[tid]
            for aid,a in team.items():
                if a['minutes_seconds_displayed'] is not None:raw_seconds+=a['minutes_seconds_displayed'];precision_sum+=a['minutes_precision_seconds'] or 0
                elif not a['did_not_play']:missing_minutes+=1
                actual={'fgm':sum(a['hard_makes']),'fga':sum(a['hard_attempts']),'threem':sum(a['hard_makes'][5:]),'threea':sum(a['hard_attempts'][5:]),'ftm':a['ftm'],'fta':a['fta']}
                for k,v in actual.items():totals[k]+=v
                ok=bool(a['box']) and actual==a['box'];counts['player_box_match' if ok else ('player_box_mismatch' if a['box'] else 'player_box_unavailable')]+=1
                if any(abs(sum(a[k][sl])-sum(a[h][sl]))>1e-8 for k,h in [('soft_attempts','hard_attempts'),('soft_makes','hard_makes')] for sl in (slice(0,5),slice(5,9))):raise ValueError('soft conservation '+gid)
                rows.append({'game_id':gid,'day':g['day'],'date_utc':g['date_utc'],'team':g['teams'][i],'home_away':['home','away'][i],'athlete_id':aid,'source_summary_sha256':g['source_summary_sha256'],'source_received_at':rec['received_at'],'accounting_ok':ok,'team_attribution_complete':not any(bad[tid].values()),**a})
            expected={'fga':g['sides'][i]['fga'],'fgm':g['sides'][i]['fgm'],'threea':sum(g['sides'][i]['attempts'][5:]),'threem':sum(g['sides'][i]['makes'][5:]),'fta':g['sides'][i]['fta'],'ftm':g['sides'][i]['ftm']}
            residual={'fga':bad[tid]['2a']+bad[tid]['3a'],'fgm':bad[tid]['2m']+bad[tid]['3m'],'threea':bad[tid]['3a'],'threem':bad[tid]['3m'],'fta':bad[tid]['1a'],'ftm':bad[tid]['1m']}
            if any(totals[k]+residual[k]!=v for k,v in expected.items()):raise ValueError('team accounting '+gid)
            delta=raw_seconds-expected_seconds;exact=precision_sum==sum(1 for a in team.values() if a['minutes_precision_seconds']==1) and missing_minutes==0
            status='exact_match' if exact and delta==0 else 'exact_mismatch' if exact else 'display_precision_consistent' if abs(delta)<=precision_sum and missing_minutes==0 else 'unresolved'
            counts['team_minutes_'+status]+=1;counts['team_with_unattributed']+=int(sum(residual.values())>0)
            team_audits.append({'game_id':gid,'team':g['teams'][i],'seconds_displayed_sum':raw_seconds,'expected_team_seconds':expected_seconds,'precision_seconds_sum':precision_sum,'missing_minutes':missing_minutes,'minutes_status':status,'unattributed':residual,'box_totals':expected,'attributed_totals':dict(totals)})
    out.mkdir(parents=True,exist_ok=True);raw=gzip_rows(rows);audit=gzip_rows(team_audits);(out/'player_games.jsonl.gz').write_bytes(raw);(out/'team_audit.jsonl.gz').write_bytes(audit)
    after=0;missing=0;unavailable=0
    for r in pred:
        path=archive/'summary'/f'{r["espn_id"]}.receipt.json'
        if not path.exists():
            path=archive/'summary'/f'{r["espn_id"]}.unavailable.receipt.json'
            if not path.exists():missing+=1;continue
            unavailable+=1
        rec=json.loads(path.read_bytes());body=path.with_name(path.name.replace('.receipt.json','.json' if rec['status']==200 else '.body'));read_verified(body,rec['sha256']);cutoff=timestamp(r['day']+'T00:00:00+09:00')
        if timestamp(rec['received_at'])>cutoff:after+=1
    coverage={'target_games':len(pred),'strict_verified_pregame_lineup_games':0,'target_summary_received_after_kst_date_start':after,'target_summary_receipt_missing':missing,'target_summary_404_receipts':unavailable,'reason':'No separately preserved timestamped pregame confirmed lineup evidence was identified; postgame box cannot establish pregame availability. Date start is conservative bound, not scheduled-tipoff cutoff.','proxy_roster_minutes':'not constructed; historical rows only','official_sources':['https://official.nba.com/nba-board-of-governors-approves-clarification-of-rule-governing-traveling-violations/','https://www.nba.com/players/todays-lineups?hidenav=true','https://official.nba.com/nba-injury-report-2026-27-season']}
    (out/'pregame-lineup-coverage.json').write_text(json.dumps(coverage,indent=2)+'\n',encoding='utf8')
    manifest={'pool_sha256':POOL_SHA,'predictions_sha256':PRED_SHA,'player_games_sha256':digest(raw),'team_audit_sha256':digest(audit),'source_games':len(pool),'target_games':len(pred),'player_rows':len(rows),'counts':dict(counts),'unattributed_examples':examples,'hard_labels':['two_0_4','two_4_10','two_10_16','two_16_plus','two_unknown','three_0_24','three_24_27','three_27_plus','three_unknown'],'soft':'uniform radius interval [max(0,r-1),r+1]; hard-zone boundaries','geometry_status':'internally_consistent_unofficial','source_scope':'retrospectively retrieved ESPN summary; no contemporaneous evidence','minutes':'integer MIN fields are converted to seconds at 60-second display precision, not exact seconds; MM:SS 1-second precision'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8');print(json.dumps({'counts':dict(counts),'coverage':coverage,'player_rows':len(rows),'examples':examples[:5]},indent=2,ensure_ascii=False))

def main():
    a=argparse.ArgumentParser();a.add_argument('--pool',required=True);a.add_argument('--predictions',required=True);a.add_argument('--archive',required=True);a.add_argument('--out',required=True);x=a.parse_args();export(pathlib.Path(x.pool),pathlib.Path(x.predictions),pathlib.Path(x.archive),pathlib.Path(x.out))
if __name__=='__main__':main()
