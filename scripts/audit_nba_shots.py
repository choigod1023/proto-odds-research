"""Audit archived ESPN NBA summaries; no network and no prediction."""
import argparse, collections, datetime, gzip, hashlib, json, math, pathlib, re, statistics

TEAMS=set('ATL BOS BKN CHA CHI CLE DAL DEN DET GS HOU IND LAC LAL MEM MIA MIL MIN NO NY OKC ORL PHI PHX POR SAC SA TOR UTAH WSH'.split())
STAT={'fieldGoalsMade-fieldGoalsAttempted':'fg','threePointFieldGoalsMade-threePointFieldGoalsAttempted':'three','freeThrowsMade-freeThrowsAttempted':'ft'}
DIST=re.compile(r'\b(\d+)-foot\b',re.I)

def classify(p):
    text=p.get('text','').lower(); attempt=p.get('pointsAttempted'); made=p.get('scoringPlay') is True
    if not p.get('shootingPlay') or attempt not in (1,2,3): return None
    if not (('makes ' in text) if made else ('misses ' in text or ' blocks ' in text)): return None
    if attempt==1 and 'free throw' not in text: return None
    if attempt in (2,3) and 'free throw' in text: return None
    if attempt==3 and ('two point' in text or '2-point' in text): return None
    if attempt==2 and ('three point' in text or '3-point' in text): return None
    if p.get('scoreValue') != (attempt if made else 0): return None
    return attempt,made

def read_checked(receipt):
    r=json.loads(receipt.read_text(encoding='utf8')); path=receipt.with_name(receipt.name.replace('.receipt.json','.body' if r.get('status')!=200 else '.json'))
    if not path.exists(): return r,None,False
    data=path.read_bytes(); return r, data, hashlib.sha256(data).hexdigest()==r.get('sha256')

def box_counts(team):
    vals={s['name']:s.get('displayValue','') for s in team.get('statistics',[])}; out={}
    for name,key in STAT.items():
        m=re.fullmatch(r'(\d+)-(\d+)', vals.get(name,''))
        if not m: return None
        out[key+'m'],out[key+'a']=map(int,m.groups())
    return out

def geom(x,y,feet):
    # Explicit hypotheses only: court dimensions in feet and basket 5.25 ft from baseline.
    out={}
    for swap in (False,True):
        a,b=(y,x) if swap else (x,y)
        for scale in (1,0.1):
            u,v=a*scale,b*scale
            for basket in (0,5.25):
                for full in (False,True):
                    d=math.hypot(u-25,v-basket)
                    if full: d=min(d,math.hypot(u-25,v-(94-basket)))
                    out[f'{"yx" if swap else "xy"}_scale{scale}_basket{basket}_{"full" if full else "half"}']=abs(d-feet)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--archive',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    root=pathlib.Path(a.archive); out=pathlib.Path(a.out); out.mkdir(parents=True,exist_ok=True)
    counts=collections.Counter(); scoreboard=collections.defaultdict(list); hashes={}; dates=[]; geometry=collections.defaultdict(list); examples=[]; rows=[]; invalid=[]
    for kind in ('scoreboard','summary'):
        for rec in sorted((root/kind).glob('*.receipt.json')):
            r,data,ok=read_checked(rec); counts[f'{kind}_status_{r.get("status")}']+=1
            counts[f'{kind}_hash_{"ok" if ok else "bad_or_missing"}' if r.get('status')==200 else f'{kind}_non200_body_{"hash_ok" if ok else "unavailable"}']+=1
            hashes[f'{kind}/{rec.name}']={'status':r.get('status'),'sha256':r.get('sha256'),'hash_ok':ok}
            if not ok or r.get('status')!=200: continue
            if kind=='scoreboard':
                for event in json.loads(data).get('events',[]): scoreboard[event.get('id')].append(event)
    for rec in sorted((root/'summary').glob('*.receipt.json')):
        r,data,ok=read_checked(rec)
        if not ok or r.get('status')!=200: continue
        d=json.loads(data); gid=rec.name.split('.')[0]; h=d.get('header',{}); comps=h.get('competitions',[])
        if len(comps)!=1: counts['exclude_competition']+=1; continue
        comp=comps[0]; cs=comp.get('competitors',[]); byside={c.get('homeAway'):c for c in cs}; abbr={c.get('team',{}).get('abbreviation') for c in cs}
        if len(cs)!=2 or set(byside)!={'home','away'} or len(abbr)!=2 or not abbr<=TEAMS: counts['exclude_non_nba_or_teams']+=1; continue
        evs=scoreboard.get(gid,[])
        if h.get('id')!=gid or comp.get('id')!=gid or len(evs)!=1: counts['exclude_id_or_scoreboard_duplicate_or_missing']+=1; continue
        event=evs[0]; esc=event.get('competitions',[{}])[0]; es={c.get('homeAway'):c.get('id') for c in esc.get('competitors',[])}
        if es!={s:c.get('id') for s,c in byside.items()} or event.get('date')!=comp.get('date'): counts['exclude_identity_mismatch']+=1; continue
        date=comp.get('date',''); dates.append(date); counts['identity_games']+=1
        teams={c['id']:c['team']['abbreviation'] for c in cs}; boxes={x.get('team',{}).get('id'):box_counts(x) for x in d.get('boxscore',{}).get('teams',[])}
        plays=d.get('plays',[]); ids=collections.Counter(p.get('id') for p in plays); dup=sum(v-1 for k,v in ids.items() if k is not None and v>1); missing=sum(1 for p in plays if not p.get('id')); counts['duplicate_play_ids']+=dup; counts['missing_play_ids']+=missing
        pc={tid:collections.Counter() for tid in teams}; last=plays[-1] if plays else {}; scoreok=all(str(last.get(s+'Score'))==str(c.get('score')) for s,c in byside.items()); counts['last_score_match' if scoreok else 'last_score_mismatch']+=1
        shotissues=0; badcoord=0; byperiod=collections.defaultdict(collections.Counter); previous=(0,0); score_issues=0
        for p in plays:
            current=(p.get('homeScore'),p.get('awayScore'))
            if all(isinstance(x,int) for x in current):
                delta=(current[0]-previous[0],current[1]-previous[1])
                if min(delta)<0: counts['score_decreases']+=1; score_issues+=1
                if sum(delta)!=p.get('scoreValue',0): counts['score_delta_value_disagreements']+=1; score_issues+=1
                previous=current
            if not p.get('shootingPlay'): continue
            counts['shooting_plays']+=1; result=classify(p); tid=p.get('team',{}).get('id')
            if result is None or tid not in teams or not p.get('id') or ids[p.get('id')]>1:
                counts['ambiguous_or_duplicate_shooting_play']+=1; shotissues+=1; continue
            n,made=result; counts[f'valid_{n}pt_attempts']+=1
            text=p.get('text','').lower()
            if n==3 and not ('three point' in text or '3-point' in text): counts['three_without_explicit_text']+=1
            if ' blocks ' in text: counts['blocked_shots']+=1
            typ='ft' if n==1 else 'fg'
            for counter in (pc[tid],byperiod[(tid,p.get('period',{}).get('number'))]):
                counter[typ+'a']+=1; counter[typ+'m']+=int(made)
                if n==3: counter['threea']+=1; counter['threem']+=int(made)
            if n==1: continue
            c=p.get('coordinate') or {}; x,y=c.get('x'),c.get('y')
            if not isinstance(x,(int,float)) or not isinstance(y,(int,float)) or abs(x)>1000 or abs(y)>1000:
                counts['field_goal_coordinate_missing_or_sentinel']+=1; badcoord+=1; continue
            counts['field_goal_coordinate_present']+=1; match=DIST.search(p.get('text',''))
            if match:
                distance=int(match.group(1)); counts['field_goal_text_distance']+=1
                for k,v in geom(x,y,distance).items(): geometry[k].append(v)
                if len(examples)<12: examples.append({'game_id':gid,'play_id':p['id'],'text':p['text'],'coordinate':c,'residuals_ft':geom(x,y,distance)})
        for tid,ab in teams.items():
            actual={k:pc[tid][k] for k in ('fgm','fga','threem','threea','ftm','fta')}; box=boxes.get(tid); mismatch={k:{'pbp':v,'box':box.get(k) if box else None} for k,v in actual.items() if box is None or v!=box.get(k)}
            eligible=not mismatch and not shotissues and not dup and not missing and scoreok and not score_issues
            counts['team_box_match' if not mismatch else 'team_box_mismatch']+=1; counts['eligible_team_games' if eligible else 'excluded_team_games']+=1
            rows.append({'game_id':gid,'date_utc':date,'team':ab,'team_id':tid,'home_away':next(s for s,c in byside.items() if c['id']==tid),'eligible':eligible,'pbp':actual,'box':box,'mismatch':mismatch,'duplicate_ids':dup,'missing_ids':missing,'ambiguous_shots':shotissues,'final_score_match':scoreok,'score_issues':score_issues,'coordinate_missing_game':badcoord,'period_counts':{str(period):dict(c) for (t,period),c in byperiod.items() if t==tid},'zones':None})
    summary={'counts':dict(counts),'date_min':min(dates,default=None),'date_max':max(dates,default=None),'geometry':{k:{'n':len(v),'median_abs_error_ft':statistics.median(v),'mean_abs_error_ft':statistics.mean(v),'over_5ft_fraction':sum(x>5 for x in v)/len(v)} for k,v in geometry.items()},'geometry_examples':examples,'coordinate_status':'uncertified','source_geometry_status':'internally_consistent_unofficial','zone_export':'withheld','prediction_accuracy_measured':False}
    (out/'geometry_examples.json').write_text(json.dumps({'source_geometry_status':'internally_consistent_unofficial','hypothesis':'xy, scale 1, basket (25,0), half court; not certified', 'examples':[dict(e, hypothesis_distance=math.hypot(e['coordinate']['x']-25,e['coordinate']['y'])) for e in examples]},indent=2,ensure_ascii=False)+'\n',encoding='utf8'); (out/'results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8'); (out/'receipt_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf8')
    with gzip.GzipFile(filename='',mode='wb',fileobj=(out/'game_team_audit.jsonl.gz').open('wb'),mtime=0) as z:
        for row in rows: z.write((json.dumps(row,sort_keys=True)+'\n').encode())
    print(json.dumps(summary,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
