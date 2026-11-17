"""Date-causal KBO/NPB relief features; not certified purchase-time snapshots."""
from collections import Counter,defaultdict
from datetime import date,timedelta
from pathlib import Path
import argparse,gzip,hashlib,json,re

from collect_npb_bullpen_history import outs


def read(path):
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf8') as f:return [json.loads(s) for s in f if s.strip()]


def key(r):return r['league'],r['date'],r['home'],r['away']


def normalize_kbo(raw,master,team_map):
    known={key(r):r for r in master if r['league']=='KBO'};aliases=team_map['KBO'];result=[];counts=Counter()
    opening={y:min(r['date'] for r in known.values() if r['date'][:4]==y) for y in {r['date'][:4] for r in known.values()}}
    for r in raw.values():
        h=aliases.get(r['home'],r['home']);a=aliases.get(r['away'],r['away']);k=('KBO',r['date'],h,a);original=known.get(k)
        if r['date'][:4] not in opening or r['date']<opening[r['date'][:4]]:counts['before_regular_season_opening']+=1;continue
        if original and (r['home_score'],r['away_score'])!=(original['hg'],original['ag']):counts['curated_score_mismatch']+=1;continue
        pitchers=[]
        try:
            for side in ['home','away']:
                ps=[]
                for p in r['data'][side]:
                    if not p.get('pcode'):raise ValueError('missing_pitcher_id')
                    ps.append(dict(player='kbo:'+str(p['pcode']),name=p['name'],outs=outs(p['inn']),runs=float(p.get('r') or 0),
                        bb=float(p.get('bb') or 0),k=float(p.get('kk') or 0)))
                pitchers.append(ps)
            if any(not ps for ps in pitchers):raise ValueError('missing_pitching_table')
            if sum(p['runs'] for p in pitchers[0])!=r['away_score'] or sum(p['runs'] for p in pitchers[1])!=r['home_score']:raise ValueError('pitching_score_mismatch')
        except (KeyError,TypeError,ValueError) as e:counts[str(e)]+=1;continue
        if not original:counts['history_without_unique_curated_score_anchor']+=1
        result.append(dict(league='KBO',date=r['date'],home=h,away=a,hg=r['home_score'],ag=r['away_score'],pitchers=pitchers,
                           official_id=r['gameId'],source_kind='retrospective_naver_pitching_archive',curated_score_anchor=bool(original)))
    return result,dict(counts)


def normalize_npb_old(rows):
    out=[];counts=Counter()
    for r in rows:
        if not r.get('official_pitching'):counts['no_official_pitching']+=1;continue
        try:
            pitchers=[]
            for side in ['home','away']:
                ps=[]
                for p in r['official_pitching'][side]:
                    pid=re.search(r'/players/(\d+)\.html',p['player_url'])
                    if not pid:raise ValueError('missing_pitcher_id')
                    ps.append(dict(player='npb:'+pid[1],name=p['name'],outs=outs(p['innings']),runs=int(p['runs']),bb=int(p['bb']),k=int(p['k']),pitches=int(p['pitches']),bf=int(p['bf'])))
                pitchers.append(ps)
            if sum(p['runs'] for p in pitchers[0])!=r['ag'] or sum(p['runs'] for p in pitchers[1])!=r['hg']:raise ValueError('pitching_score_mismatch')
            out.append(dict(league='NPB',date=r['date'],home=r['home'],away=r['away'],hg=r['hg'],ag=r['ag'],pitchers=pitchers,
                            official_id=r['official_box_url'],url=r['official_box_url'],source_kind='official_2023_box_reused'))
        except (ValueError,KeyError,TypeError) as e:counts[str(e)]+=1
    return out,dict(counts)


def feature(row,history):
    day=date.fromisoformat(row['date']);sides=[];proof={}
    for side,team in [('home',row['home']),('away',row['away'])]:
        past=[r for r in history.get((row['league'],team),[]) if 0<(day-date.fromisoformat(r['date'])).days<=28]
        y=[r for r in past if (day-date.fromisoformat(r['date'])).days==1]
        three=[r for r in past if (day-date.fromisoformat(r['date'])).days<=3]
        ids={r['player'] for r in y};previous={r['player'] for r in past if (day-date.fromisoformat(r['date'])).days==2}
        ranks=defaultdict(float)
        for r in past:ranks[r['player']]+=r['outs']
        top=sorted(ranks,key=lambda pid:(-ranks[pid],pid))[:3]
        total=lambda rows,k:sum(float(r[k]) for r in rows)
        sides.append(dict(relief_outs1=total(y,'outs'),relief_outs3=total(three,'outs'),relief_consecutive=len(ids&previous),
            relief_top3_outs_used1=sum(pid in ids for pid in top),
            relief_kbb_per_out28=(total(past,'k')-total(past,'bb')+45*.20)/(total(past,'outs')+45)))
        proof[side]=dict(team=team,past_appearances28=len(past),top3_past_outs_ids=top,
            latest_source_date=max((r['date'] for r in past),default=None),source_fixture_ids=sorted({r['fixture'] for r in past}))
    return {n:sides[0][n]-sides[1][n] for n in sides[0]},proof


def missing_history(row,available,expected):
    day=date.fromisoformat(row['date'])
    return [key(r) for r in expected if r['league']==row['league'] and
            0<(day-date.fromisoformat(r['date'])).days<=28 and
            ({row['home'],row['away']}&{r['home'],r['away']}) and key(r) not in available]


def build(kbo,master,npb_old,prices,cohort,team_map,output):
    sources=output/'sources';sources.mkdir(parents=True,exist_ok=True)
    expected=read(master)
    records,ex_kbo=normalize_kbo(json.loads(kbo.read_text(encoding='utf8')),expected,json.loads(team_map.read_text(encoding='utf8')))
    old,ex_npb=normalize_npb_old(read(npb_old));records+=old
    source_status=Counter()
    for p in sorted(sources.glob('npb-*-boxes.jsonl')):
        latest={r['url']:r for r in read(p)}
        for r in latest.values():
            source_status[p.name+':'+('accepted' if r['accepted'] else r.get('reason','failed'))]+=1
            if r['accepted']:records.append(r)
    unique={};excluded=Counter()
    for r in records:
        fixture=str(r['official_id'])
        if fixture in unique:
            if key(unique[fixture])!=key(r) or (unique[fixture]['hg'],unique[fixture]['ag'])!=(r['hg'],r['ag']):raise ValueError('conflicting_official_fixture')
            excluded['duplicate_fixture']+=1;continue
        unique[fixture]=r
    records=list(unique.values());history=defaultdict(list);fixtures=defaultdict(list)
    for r in records:
        fixtures[key(r)].append(r)
        for i,team in enumerate([r['home'],r['away']]):
            for p in r['pitchers'][i][1:]:history[r['league'],team].append(dict(p,date=r['date'],fixture=r['official_id']))
    targets=[];coverage_excluded=[];inputs=[]
    for r in read(prices):
        if r['league'] not in {'KBO','NPB'}:continue
        matches=fixtures.get(key(r),[])
        if len(matches)!=1:excluded['historical_price_fixture_missing_or_ambiguous']+=1;continue
        g=matches[0]
        if (r['hg'],r['ag'])!=(g['hg'],g['ag']):excluded['historical_price_score_mismatch']+=1;continue
        role='train' if r['date'].startswith('2023') else 'validation'
        missing=missing_history(r,fixtures,expected)
        if missing:
            excluded[r['league']+':'+role+':incomplete_past_28days']+=1;coverage_excluded.append(dict(league=r['league'],date=r['date'],role=role,missing=missing));continue
        x,proof=feature(r,history);targets.append(dict(r,role=role,features=x,proof=proof));inputs.append(dict(r,role=role))
    for r in read(cohort):
        if r['league'] not in {'KBO','NPB'}:continue
        matches=fixtures.get(key(r),[])
        if len(matches)>1 or (len(matches)==1 and (matches[0]['hg'],matches[0]['ag'])!=(r['hg'],r['ag'])):
            excluded['evaluation_fixture_ambiguous_or_score_mismatch']+=1;continue
        missing=missing_history(r,fixtures,expected)
        if missing:
            excluded[r['league']+':evaluation:incomplete_past_28days']+=1;coverage_excluded.append(dict(league=r['league'],date=r['date'],role='evaluation',missing=missing));continue
        x,proof=feature(r,history);targets.append(dict(r,role='evaluation',target=r,features=x,proof=proof));inputs.append(dict(r,role='evaluation',target=r))
    (output/'asian-features.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(targets,key=lambda r:(r['league'],r['date'],r['home']))),encoding='utf8',newline='\n')
    with gzip.open(sources/'asian-normalized-history.jsonl.gz','wt',encoding='utf8',newline='\n') as f:
        for r in sorted(records,key=lambda r:(r['league'],r['date'],r['home'])):f.write(json.dumps(r,ensure_ascii=False)+'\n')
    (output/'asian-cohort-inputs.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in inputs),encoding='utf8',newline='\n')
    (output/'history-coverage-exclusions.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in coverage_excluded),encoding='utf8',newline='\n')
    meta=dict(history_by_league_year=dict(Counter(r['league']+':'+r['date'][:4] for r in records)),
        feature_counts=dict(Counter(r['league']+':'+r['role'] for r in targets)),excluded=dict(excluded),kbo_excluded=ex_kbo,npb_old_excluded=ex_npb,new_npb_source_status=dict(source_status),
        source_sha256={str(p.name):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in [kbo,master,npb_old,prices,cohort]},
        timing='strict prior calendar dates; not a certified timestamped or executable-price backtest')
    (output/'asian-feature-audit.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(meta,ensure_ascii=False))


def replay(output):
    history=defaultdict(list)
    for r in read(output/'sources'/'asian-normalized-history.jsonl.gz'):
        for i,team in enumerate([r['home'],r['away']]):
            for p in r['pitchers'][i][1:]:history[r['league'],team].append(dict(p,date=r['date'],fixture=r['official_id']))
    facts=[]
    for r in read(output/'asian-cohort-inputs.jsonl'):
        x,proof=feature(r,history);facts.append(dict(r,features=x,proof=proof))
    (output/'asian-features.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in sorted(facts,key=lambda r:(r['league'],r['date'],r['home']))),encoding='utf8',newline='\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--replay',action='store_true')
    for n in ['kbo','master','npb-old','prices','cohort','team-map']:p.add_argument('--'+n,type=Path)
    a=p.parse_args()
    if a.replay:replay(a.output)
    else:build(a.kbo,a.master,a.npb_old,a.prices,a.cohort,a.team_map,a.output)
