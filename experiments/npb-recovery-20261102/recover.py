from __future__ import annotations
import collections, gzip, hashlib, json
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from archived_2023 import load as load_2023, daily_pairs

ROOT = Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/rd-baseball-source-links-20261030/experiments/source-link-baseball-20261030')
RAW = Path('data/raw/npb_starters.json')
OUT = Path('experiments/npb-recovery-20261102')
TEAMS = {'한신':'t','요코하마':'db','요미우리':'g','주니치':'d','히로시마':'c','야쿠르트':'s','소프트뱅크':'h','닛폰햄':'f','오릭스':'b','라쿠텐':'e','세이부':'l','지바롯데':'m'}

def sha(b): return hashlib.sha256(b).hexdigest()
def dt(s): return datetime.fromisoformat(s).replace(tzinfo=None)
def match_key(r): return (r['date'], TEAMS.get(r['home']), TEAMS.get(r['away']))
def score(raw):
    soup=BeautifulSoup(raw,'html.parser')
    for table in soup.find_all('table'):
        rows=[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)] for tr in table.find_all('tr',recursive=False)]
        if not rows: # tables often have tbody
            rows=[[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)] for tr in table.find_all('tr')]
        if len(rows)==3 and '計' in rows[0]:
            i=rows[0].index('計')
            if all(len(row)>i and row[i].isdigit() for row in rows[1:]): return int(rows[2][i]), int(rows[1][i])
    return None

def unique_daily(pairs, pair, basename):
    matches=[item for item in pairs if item[1]==pair]
    return len(matches)==1 and matches[0][0]==basename

def run():
    base=json.loads(RAW.read_text(encoding='utf-8'))
    missing=[r for r in base if not r.get('home_starter') or not r.get('away_starter')]
    basecounts=collections.Counter(match_key(r) for r in base)
    evidence={}; official={}; input_files=[RAW]
    for phase in ['', 'phase2']:
        d=ROOT/phase; inp=d/'parsed-official-games.jsonl.gz'; input_files.append(inp)
        for meta in (d/'raw').glob('*.meta.json'):
            m=json.loads(meta.read_text(encoding='utf8'))
            if m.get('url','').startswith('https://npb.jp/scores/') and m.get('status')==200:
                evidence.setdefault((m.get('sha256'),m.get('url')),[]).append((d,m,meta))
        with gzip.open(inp,'rt',encoding='utf8') as f:
            for line in f:
                r=json.loads(line)
                if r.get('league')=='NPB': official.setdefault(r['game_id'],[]).append(r)
    lookup=collections.defaultdict(list)
    for game_id, records in official.items():
        for r in records:
            lookup[(r['fact_date'],r['home_id'],r['away_id'])].append(r)
    archival=load_2023()
    archival_by_key=collections.defaultdict(list)
    for x in archival: archival_by_key[(x["date"],TEAMS[x["home"]],TEAMS[x["away"]])].append(x)
    daily={}
    for p in (OUT/'raw').glob('*.json'):
        meta=json.loads(p.read_text(encoding='utf8')); url=meta['url']
        if '/gm2023' in url:
            raw=p.with_suffix('.html').read_bytes()
            if sha(raw)!=meta['sha256']: raise ValueError('daily index hash mismatch')
            day=url.rsplit('gm',1)[1][:8]; daily[day[:4]+'-'+day[4:6]+'-'+day[6:8]]=daily_pairs(raw)
    cohort=[]
    for r in missing:
        key=match_key(r); candidates=lookup.get(key,[]); ids={x['game_id'] for x in candidates}
        out={'game_id_string':r['gameId'],'date':r['date'],'home':r['home'],'away':r['away'],'original_home_starter':r['home_starter'],'original_away_starter':r['away_starter'],'original_home_score':r['home_score'],'original_away_score':r['away_score'],'price_link':'not_checked','pregame_receipt':'not_verified','official_result':'not_linked'}
        archive=archival_by_key.get(key,[])
        if None in key: out['reason']='unknown_team_alias'
        elif len(archive)==1 and basecounts[key]==1:
            a=archive[0]; t=datetime.fromisoformat(r['datetime']); h,m=map(int,a['official_start_hhmm'].split(':')); delta=abs((t-t.replace(hour=h,minute=m,second=0,microsecond=0)).total_seconds())
            pairs=daily.get(r['date'],[]); basename=a['source_url'].rsplit('/',1)[-1]; pair=(key[1],key[2])
            if delta>1800: out['reason']='start_time_diff_gt_30m'
            elif not unique_daily(pairs,pair,basename): out['reason']='daily_index_pair_ambiguous_or_missing'
            else:
                out.update(a); out['start_delta_seconds']=delta; out['reason']='retrospective_both_starter_names_only'; out['official_result']='score_matches_original' if (a['home_score'],a['away_score'])==(r['home_score'],r['away_score']) else 'score_differs_original'
        elif len(archive)>1: out['reason']='ambiguous_same_day_team_pair'
        elif basecounts[key]!=1 or len(ids)>1: out['reason']='ambiguous_same_day_team_pair'
        elif not candidates: out['reason']='no_official_archive_game'
        else:
            g=candidates[0]; delta=abs((dt(g['start_time'])-dt(r['datetime'])).total_seconds())
            if delta>1800: out['reason']='start_time_diff_gt_30m'
            else:
                sources=evidence.get((g['source_sha256'],g['source_url']),[])
                if len(sources)!=1: out['reason']='missing_or_ambiguous_raw_document'
                else:
                    d,meta,mp=sources[0]; rawpath=d/meta['raw_path']; raw=gzip.open(rawpath,'rb').read()
                    if sha(raw)!=g['source_sha256']: out['reason']='raw_hash_mismatch'
                    else:
                        starters={team:[x for x in g['pitchers'] if x['team_id']==team and x['role']=='starter'] for team in [key[1],key[2]]}
                        out.update({'official_game_id':g['game_id'],'source_url':g['source_url'],'source_sha256':g['source_sha256'],'source_raw_path':str(rawpath),'source_meta_path':str(mp),'source_observed_at':meta['observed_at'],'official_start':g['start_time'],'original_start':r['datetime'],'start_delta_seconds':delta,'official_score':score(raw)})
                        if any(len(v)!=1 or not v[0].get('player_id') or not v[0].get('name') for v in starters.values()): out['reason']='missing_or_ambiguous_official_starter'
                        else:
                            out['official_home_starter']=starters[key[1]][0]; out['official_away_starter']=starters[key[2]][0]
                            out['reason']='retrospective_both_starters_linked'
                            sc=out['official_score']; out['official_result']='score_matches_original' if sc==[r['home_score'],r['away_score']] or sc==(r['home_score'],r['away_score']) else ('score_differs_original' if sc else 'score_unparsed')
        cohort.append(out)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'cohort.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in cohort),encoding='utf8')
    odds_path=Path('data/raw/overseas/NPB.json'); odds=json.loads(odds_path.read_text(encoding='utf8')); restored_dates={x['date'] for x in cohort if x['reason']=='retrospective_both_starter_names_only'}; date_candidates=[x for x in odds if x.get('date','')[:10] in restored_dates]; input_files.append(odds_path)
    summary={'overseas_file_rows':len(odds),'overseas_rows_on_restored_dates':len(date_candidates),'base_rows':len(base),'missing_rows':len(missing),'reason_counts':dict(collections.Counter(x['reason'] for x in cohort)),'result_counts':dict(collections.Counter(x['official_result'] for x in cohort)),'pregame_receipt_verified_in_recovered':0,'same_price_product_time_verified':None,'input_sha256s':{str(p):sha(p.read_bytes()) for p in input_files+sorted((OUT/'raw').glob('*'))},'scope':'postgame archive only; no independently verified pregame receipt; no price join performed','command':'python experiments/npb-recovery-20261102/recover.py'}
    (OUT/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': run()
