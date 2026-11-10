"""Export fixed, audited ESPN NBA games as research-only shot profiles."""
import argparse, collections, datetime as dt, gzip, hashlib, json, math, pathlib

POOL_SHA='1ef7f311d53a9511f2fbcc6b5422bafaf1f3e7e8037b4a9cec1cea40e8ebfb9f'
RECEIPTS_SHA='e57db52d219dd496d939129a87d67f1cb65293b2fdec8faefbfe6f2c385df8ab'
LABELS=['two_0_4','two_4_10','two_10_16','two_16_plus','two_unknown','three_0_24','three_24_27','three_27_plus','three_unknown']
POINTS=[2,2,2,2,2,3,3,3,3]
ZONE_SPEC='espn_xy25_0_radial_proxy_v1'
GEOMETRY='internally_consistent_unofficial'

def digest(data): return hashlib.sha256(data).hexdigest()
def verified(path, expected):
    data=path.read_bytes()
    if digest(data)!=expected: raise ValueError(f'hash mismatch: {path}')
    return data

def zone(points,coord):
    if points not in (2,3): raise ValueError('invalid field goal value')
    coord=coord if isinstance(coord,dict) else {}; x,y=coord.get('x'),coord.get('y')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or abs(v)>1000 for v in (x,y)):
        return 4 if points==2 else 8
    r=math.hypot(x-25,y)
    if points==2: return 0 if r<4 else 1 if r<10 else 2 if r<16 else 3
    return 5 if r<24 else 6 if r<27 else 7

def classify(p):
    text=p.get('text','').lower(); n=p.get('pointsAttempted'); made=p.get('scoringPlay') is True
    if not p.get('shootingPlay') or n not in (1,2,3): return None
    if not (('makes ' in text) if made else ('misses ' in text or ' blocks ' in text)): return None
    if n==1 and 'free throw' not in text: return None
    if n in (2,3) and 'free throw' in text: return None
    if n==3 and ('two point' in text or '2-point' in text): return None
    if n==2 and ('three point' in text or '3-point' in text): return None
    if p.get('scoreValue') != (n if made else 0): return None
    return n,made

def deterministic_gzip(lines):
    import io
    buf=io.BytesIO()
    with gzip.GzipFile(fileobj=buf,mode='wb',filename='',mtime=0) as z:
        for item in lines: z.write((json.dumps(item,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode('utf8'))
    return buf.getvalue()

def export(archive,audit,out):
    archive=pathlib.Path(archive); audit=pathlib.Path(audit);out=pathlib.Path(out)
    pool_data=verified(audit/'game_team_audit.jsonl.gz',POOL_SHA); receipt_data=verified(audit/'receipt_hashes.json',RECEIPTS_SHA); original_receipts=json.loads(receipt_data)
    allrows=[json.loads(x) for x in gzip.decompress(pool_data).splitlines()]; bygame=collections.defaultdict(list)
    for row in allrows: bygame[row['game_id']].append(row)
    pool={k:v for k,v in bygame.items() if len(v)==2 and all(r['eligible'] for r in v)}
    if len(pool)!=1185: raise ValueError(f'pool count {len(pool)}')
    # Verify every retained receipt and body, including eight 404 bodies.
    statuses=collections.Counter(); raw_receipt_hashes={}
    for key,reference in original_receipts.items():
        receipt=archive/key; raw=receipt.read_bytes(); r=json.loads(raw); raw_receipt_hashes[key]=digest(raw)
        if r['status']!=reference['status'] or r['sha256']!=reference['sha256'] or not reference['hash_ok']: raise ValueError(f'receipt mismatch {key}')
        body=receipt.with_name(receipt.name.replace('.receipt.json','.json' if r['status']==200 else '.body'))
        verified(body,r['sha256']); statuses[f'{receipt.parent.name}_{r["status"]}']+=1
    rows=[]; totals=collections.Counter(); boundary=collections.Counter()
    for gid, ars in sorted(pool.items()):
        receipt=archive/'summary'/f'{gid}.receipt.json'; r=json.loads(receipt.read_bytes()); d=json.loads(verified(receipt.with_name(f'{gid}.json'),r['sha256']))
        h=d['header']; comp=h['competitions'][0]; cs=comp['competitors']; sides={c['homeAway']:c for c in cs}; audit_sides={a['home_away']:a for a in ars}
        if h['id']!=gid or comp['id']!=gid or set(sides)!={'home','away'}: raise ValueError(f'identity {gid}')
        for side,c in sides.items():
            ar=audit_sides[side]
            if (ar['team_id'],ar['team'],ar['date_utc'])!=(c['id'],c['team']['abbreviation'],comp['date']): raise ValueError(f'team/date mismatch {gid}')
        boxes={t['team']['id']:t for t in d['boxscore']['teams']}; accum={sides[s]['id']:{'attempts':[0]*9,'makes':[0]*9,'fta':0,'ftm':0,'periods':collections.defaultdict(collections.Counter)} for s in ('home','away')}
        ids=set(); previous=(0,0)
        for play in d['plays']:
            pid=play.get('id')
            if not pid or pid in ids: raise ValueError(f'duplicate/missing ID {gid}')
            ids.add(pid); current=(play.get('homeScore'),play.get('awayScore'))
            if all(isinstance(v,int) for v in current):
                delta=[current[i]-previous[i] for i in (0,1)]
                if min(delta)<0 or sum(delta)!=play.get('scoreValue',0): raise ValueError(f'score correction {gid} {pid}')
                previous=current
            if not play.get('shootingPlay'): continue
            shot=classify(play); tid=play.get('team',{}).get('id')
            if shot is None or tid not in accum: raise ValueError(f'ambiguous shot {gid} {pid}')
            n,made=shot; acc=accum[tid]; per=acc['periods'][str(play.get('period',{}).get('number'))]
            if n==1:
                acc['fta']+=1;acc['ftm']+=int(made);per['fta']+=1;per['ftm']+=int(made);continue
            idx=zone(n,play.get('coordinate')); acc['attempts'][idx]+=1;acc['makes'][idx]+=int(made)
            per['fga']+=1;per['fgm']+=int(made)
            if n==3: per['threea']+=1;per['threem']+=int(made)
            if idx not in (4,8):
                c=play['coordinate']; distance=math.hypot(c['x']-25,c['y'])
                for edge in ((4,10,16) if n==2 else (24,27)):
                    if abs(distance-edge)<1: boundary[f'{n}pt_within_1ft_{edge}']+=1
        exported=[]
        for side in ('home','away'):
            c=sides[side]; ar=audit_sides[side]; acc=accum[c['id']]; st={x['name']:x.get('displayValue') for x in boxes[c['id']]['statistics']}
            fga=sum(acc['attempts']);fgm=sum(acc['makes']);threea=sum(acc['attempts'][5:]);threem=sum(acc['makes'][5:])
            vals={'fga':fga,'fgm':fgm,'threea':threea,'threem':threem,'fta':acc['fta'],'ftm':acc['ftm']}
            if vals!=ar['box'] or vals!=ar['pbp']: raise ValueError(f'box mismatch {gid} {side}: {vals} vs {ar["box"]}')
            for key,name in [('fg','fieldGoalsMade-fieldGoalsAttempted'),('three','threePointFieldGoalsMade-threePointFieldGoalsAttempted'),('ft','freeThrowsMade-freeThrowsAttempted')]:
                m,a=map(int,st[name].split('-'))
                if (m,a)!=(vals[key+'m'],vals[key+'a']): raise ValueError(f'raw box mismatch {gid} {side} {key}')
            periods={k:dict(v) for k,v in acc['periods'].items()}
            if periods!=ar['period_counts']: raise ValueError(f'period event mismatch {gid} {side}')
            periodpoints=[2*acc['periods'][str(i)]['fgm']+acc['periods'][str(i)]['threem']+acc['periods'][str(i)]['ftm'] for i in range(1,len(c['linescores'])+1)]
            if periodpoints!=[int(x['displayValue']) for x in c['linescores']] or periodpoints!=ar['period_score_check']['expected']: raise ValueError(f'period score mismatch {gid} {side}')
            points=int(c['score']);orb=int(st['offensiveRebounds']);tov=int(st['totalTurnovers'])
            if 2*fgm+threem+acc['ftm']!=points or previous[0 if side=='home' else 1]!=points: raise ValueError(f'score mismatch {gid} {side}')
            pos=fga-orb+tov+0.44*acc['fta']
            if pos<=0: raise ValueError(f'possessions {gid} {side}')
            exported.append({'attempts':acc['attempts'],'makes':acc['makes'],'fta':acc['fta'],'ftm':acc['ftm'],'orb':orb,'tov':tov,'fga':fga,'fgm':fgm,'points':points,'estimated_possessions':pos})
            for i,label in enumerate(LABELS): totals[label+'_attempts']+=acc['attempts'][i];totals[label+'_makes']+=acc['makes'][i]
            totals['fta']+=acc['fta'];totals['ftm']+=acc['ftm']
        time=dt.datetime.fromisoformat(comp['date'].replace('Z','+00:00')); day=time.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
        rows.append({'game_id':gid,'day':day,'date_utc':comp['date'],'teams':[sides[s]['team']['abbreviation'] for s in ('home','away')],'source_summary_sha256':r['sha256'],'sides':exported,'zone_spec_id':ZONE_SPEC,'geometry_status':GEOMETRY})
    out.mkdir(parents=True,exist_ok=True); payload=deterministic_gzip(rows);(out/'games.jsonl.gz').write_bytes(payload)
    manifest={'zone_spec_id':ZONE_SPEC,'geometry_status':GEOMETRY,'zone_labels':LABELS,'zone_points':POINTS,'pool_sha256':POOL_SHA,'audit_receipt_manifest_sha256':RECEIPTS_SHA,'raw_receipt_sha256':digest(json.dumps(raw_receipt_hashes,sort_keys=True,separators=(',',':')).encode()),'games_sha256':digest(payload),'source_counts':dict(statuses),'source_games':len(rows),'source_team_games':2*len(rows),'zone_counts':dict(totals),'boundary_within_1ft':dict(boundary),'source_day_min':min(x['day'] for x in rows),'source_day_max':max(x['day'] for x in rows),'geometry_limit':'Unofficial ESPN internal distance proxy; unknown coordinates retained; not NBA-certified zones','pregame_limit':'Target date D uses only source day <= D-4; retrospective collection does not prove historical availability.'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8'); print(json.dumps(manifest,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--audit',required=True);p.add_argument('--out',required=True);a=p.parse_args();export(a.archive,a.audit,a.out)
if __name__=='__main__': main()
