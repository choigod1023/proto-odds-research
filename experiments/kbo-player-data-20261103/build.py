"""Offline retrospective KBO linkage. No HTTP, production DB, or model fitting.

Publish inventory.json last; consumers must verify its hashes before reading rows.
A historical local-clock or prior-calendar-day assumption is never a receipt proof.
"""
import csv, gzip, hashlib, json, math, os, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
CSV=Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\internal-game-factors\data\processed\games.csv.tmp')
PATHS={'games':ROOT/'data/model_candidates/kbo/archived-games.jsonl.gz',
       'batters':ROOT/'data/raw/detail/kbo_batters_indiv_2023_2026.json',
       'pitchers':ROOT/'data/raw/detail/kbo_baseball_2023_2026.json',
       'starters':ROOT/'data/raw/kbo_starters.json','domestic_market':CSV,
       'tipster':ROOT/'data/raw/picksters/tailslips_pick_events.jsonl'}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def feature(value,source,basis='retrospective_prior_calendar_day_assumption',day=None):
    return {'value':value,'available_at':None,'availability_basis':basis,'source_id':source,'latest_source_date':day}

def read_market(path):
    result=defaultdict(list); counts=Counter()
    with path.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if not (r['league']=='KBO' and r['sport']=='bs' and r['market_family']=='승패' and r['market_tag']=='hm' and r['n_way']=='2' and not r['market_label']): continue
            h=re.fullmatch(r'(.+?)\s+(\d+)',r['home']); a=re.fullmatch(r'(\d+)\s+(.+)',r['away']); d=re.fullmatch(r'(\d\d)\.(\d\d)\([^)]*\)\s+(\d\d):(\d\d)',r['date_text'])
            if not h or not a or not d: counts['market_parse_failure']+=1; continue
            try:
                dt=datetime(int(r['year']),*[int(x) for x in d.groups()]); prices=[float(v) for v in r['odds'].split(',')]
                if len(prices)!=2 or any(not math.isfinite(v) or v<=1 for v in prices) or r['is_void']!='False':raise ValueError
            except ValueError: counts['market_invalid_or_void']+=1; continue
            result[(dt.date().isoformat(),h[1],a[2])].append({'kickoff':dt.isoformat(), 'home_score':int(h[2]),'away_score':int(a[1]),'prices':prices,'market_id':f"proto:{r['year']}:{r['round']}:{r['game_no']}:full_game_2way"})
    return result,counts


def roster(box,side):
    players=(box.get('data') or {}).get(side) or []; byorder=defaultdict(list)
    for x in players:
        n=x.get('order'); code=x.get('code')
        if type(n)==int and 1<=n<=9 and code and x.get('pos')!='교': byorder[n].append(str(code))
    # The first player listed per batting slot is an inferred postgame starting order.
    if set(byorder)!=set(range(1,10)):return None
    ids=[byorder[i][0] for i in range(1,10)]
    return ids if len(set(ids))==9 else None


def build():
    hashes={k:digest(p) for k,p in PATHS.items()}
    with gzip.open(PATHS['games'],'rt',encoding='utf8') as f: games=[json.loads(x) for x in f]
    batters=json.loads(PATHS['batters'].read_text(encoding='utf8')); pitchers=json.loads(PATHS['pitchers'].read_text(encoding='utf8'))
    starter_rows=json.loads(PATHS['starters'].read_text(encoding='utf8')); starters=defaultdict(list)
    for row in starter_rows:starters[row['gameId']].append(row)
    markets,counts=read_market(CSV)
    tipster_rows=0; tipster_kbo_marked=0
    with PATHS['tipster'].open(encoding='utf8') as handle:
        for line in handle:
            record=json.loads(line); tipster_rows+=1
            tipster_kbo_marked+=int('KBO' in str(record.get('game_header') or ''))
    bykey=Counter((g['date'],g['home'],g['away']) for g in games)
    if len({g['source_fixture_id'] for g in games})!=len(games):raise ValueError('duplicate fixture ids')
    games.sort(key=lambda g:(g['date'],g['source_fixture_id']))
    byday=defaultdict(list)
    for g in games:byday[g['date']].append(g)
    batting=defaultdict(lambda:Counter()); pitching=defaultdict(lambda:Counter()); team=defaultdict(lambda:Counter()); relief={}
    rows=[]; labels=[]
    for day,fixtures in sorted(byday.items()):
        for g in fixtures:
            ident=g['source_fixture_id']; box=batters.get(ident); pit=pitchers.get(ident); st=starters.get(ident,[])
            # Source IDs are crosswalk keys, not independent KBO identity authentication.
            for src in [box,pit]:
                if src is not None and (src.get('date'),src.get('home'),src.get('away'),src.get('home_score'),src.get('away_score')) != (day,g['home'],g['away'],g['hg'],g['ag']):raise ValueError('conflicting source fixture '+ident)
            kickoff=None
            if len(st)==1:
                try:
                    x=datetime.fromisoformat(st[0]['datetime']);
                    if x.date().isoformat()==day:kickoff=x
                except (KeyError,TypeError,ValueError):pass
            if kickoff is None:counts['kickoff_missing_or_ambiguous']+=1
            else:
                if (st[0]['home'],st[0]['away'],st[0]['home_score'],st[0]['away_score'])!=(g['home'],g['away'],g['hg'],g['ag']):raise ValueError('starter fixture mismatch')
            matches=list(markets.get((day,g['home'],g['away']),[])) if kickoff is not None else []
            if kickoff is None:counts['market_excluded_no_kickoff']+=1
            if bykey[(day,g['home'],g['away'])]>1:
                if kickoff is None:matches=[];counts['doubleheader_no_clock']+=1
                else:matches=[m for m in matches if m['kickoff']==kickoff.isoformat()]
            elif kickoff is not None:
                matches=[m for m in matches if m['kickoff']==kickoff.isoformat()]
            market=None
            if len(matches)==1:
                m=matches[0]
                if (m['home_score'],m['away_score'])!=(g['hg'],g['ag']):counts['market_score_conflict']+=1
                else:market={'market_id':m['market_id'],'kind':'full_game_2way','selections':{'home':m['prices'][0],'away':m['prices'][1]},'odds_observed_at':None,'settlement_rule':'draw_void_refund','execution_price_verified':False}
            elif len(matches)>1:counts['ambiguous_same_market']+=1
            else:counts['market_missing_or_unmatched']+=1
            features={}
            for side in ('home','away'):
                club=g[side]; b=roster(box or {},side); pp=((pit or {}).get('data') or {}).get(side) or []
                pitcher_id=str(pp[0]['pcode']) if pp and pp[0].get('pcode') else None
                announced_name=st[0].get(side+'_starter') if len(st)==1 else None
                if pitcher_id and announced_name and pp[0].get('name')!=announced_name:
                    pitcher_id=None;counts[side+'_starter_source_conflict']+=1
                features[side+'_team_prior']=feature(dict(team[club]) if team[club] else None,'games',day=team[club].get('last_day'))
                features[side+'_actual_lineup_assumed']=feature(b,'batters','postgame_actual_lineup_inferred_not_pregame')
                features[side+'_lineup_prior']=feature([{'player_id':p,'history':dict(batting[p]) if batting[p] and not batting[p].get('incomplete_stat') else None} for p in b] if b else None,'batters',day=(max((batting[p].get('last_day','') for p in b),default='') or None) if b else None)
                features[side+'_actual_starter_assumed']=feature(pitcher_id,'pitchers','postgame_first_pitcher_assumed_not_pregame')
                features[side+'_starter_prior']=feature(dict(pitching[pitcher_id]) if pitcher_id and pitching[pitcher_id] and not pitching[pitcher_id].get('incomplete_stat') else None,'pitchers',day=pitching[pitcher_id].get('last_day') if pitcher_id else None)
                features[side+'_prior_game_relief_appearances']=feature(relief.get(club),'pitchers',day=(relief.get(club) or {}).get('date'))
                if not b:counts[side+'_lineup_missing']+=1
                if not pitcher_id:counts[side+'_starter_missing']+=1
            features['venue']=feature((box.get('data') or {}).get('stadium') if box else None,'batters','postgame_venue_unverified')
            features['tipster_votes']=feature(None,'tipster','not_linked')
            features['bullpen_24h_48h_pitch_count']=feature(None,'pitchers','game_end_and_window_coverage_unproven')
            sources=[{'source_id':k,'path_or_url':str(p),'sha256':hashes[k],'received_at':None,'availability_basis':'retrospective_file_no_actual_historical_receipt'} for k,p in PATHS.items() if k!='domestic_market' or market]
            rows.append({'fixture_id':ident,'league':'KBO','season':int(day[:4]),'kickoff_at':kickoff.isoformat()+'+09:00' if kickoff else None,'decision_at':(kickoff-timedelta(minutes=30)).isoformat()+'+09:00' if kickoff else None,'decision_time_basis':'hypothetical_kickoff_minus_30_not_actual' if kickoff else 'unknown', 'home_id':g['home'],'away_id':g['away'],'identity_basis':'source_team_name_not_independently_official_id','market':market,'availability_tier':'retrospective_unproven','features':features,'sources':sources})
            labels.append({'fixture_id':ident,'market_id':market['market_id'] if market else None,'home_score':g['hg'],'away_score':g['ag'],'official_result':'home' if g['hg']>g['ag'] else 'away' if g['hg']<g['ag'] else 'draw','received_at':None,'revision':None,'settlement_rule':market['settlement_rule'] if market else None,'result_source_status':'archived_score_official_independently_unverified'})
        # Day-batched update avoids the first game leaking into a same-day game.
        daily_team_counts=Counter(g[side] for g in fixtures for side in ('home','away'))
        for g in fixtures:
            ident=g['source_fixture_id']; day=g['date']; b=batters.get(ident); p=pitchers.get(ident)
            for side in ('home','away'):
                club=g[side]; tm=team[club];tm['games']+=1;tm['runs_for']+=g['hg' if side=='home' else 'ag'];tm['runs_against']+=g['ag' if side=='home' else 'hg'];tm['last_day']=day
                if b:
                    for player in (b.get('data') or {}).get(side) or []:
                        if player.get('code'):
                            entry=batting[str(player['code'])]
                            for key in ('ab','hit','bb','hr'):
                                val=player.get(key)
                                if type(val)==int and val>=0:entry[key]+=val
                                else:entry['incomplete_stat']=1
                            entry['last_day']=day
                ps=((p or {}).get('data') or {}).get(side) or []
                for player in ps:
                    if player.get('pcode'):
                        entry=pitching[str(player['pcode'])];entry['appearances']+=1
                        for key in ('er','hit','bb','hr','kk'):
                            val=player.get(key)
                            if type(val)==int and val>=0:entry[key]+=val
                            else:entry['incomplete_stat']=1
                        entry['last_day']=day
                relief[club]={'date':day,'appearances':len(ps)-1} if daily_team_counts[club]==1 and ps and all(x.get('pcode') for x in ps) else None
    assert len(rows)==len(labels)==len(games)
    ids=[(r['fixture_id'],r['market']['market_id'] if r['market'] else None) for r in rows]
    if len(set(ids))!=len(ids):raise ValueError('duplicate fixture/market')
    market_ids=[r['market']['market_id'] for r in rows if r['market']]
    if len(set(market_ids))!=len(market_ids):raise ValueError('market mapped to multiple fixtures')
    out={}
    for name,data in [('rows.jsonl',rows),('labels.jsonl',labels)]:
        temp=OUT/(name+'.tmp');temp.write_text(''.join(json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n' for x in data),encoding='utf8',newline='\n');os.replace(temp,OUT/name);out[name]=digest(OUT/name)
    inventory={'schema_version':'kbo-player-challenger-input-v1','status':'ready_retrospective_only','protocol_commit':'867afbdd','source_hashes':hashes,'source_paths':{k:str(v) for k,v in PATHS.items()},'counts':{'fixtures':len(rows),'retrospective_unproven':len(rows),'point_in_time_verified':0,'with_market':sum(r['market'] is not None for r in rows),'missing_market':sum(r['market'] is None for r in rows),'with_kickoff':sum(r['kickoff_at'] is not None for r in rows),'both_lineups':sum(r['features']['home_actual_lineup_assumed']['value'] is not None and r['features']['away_actual_lineup_assumed']['value'] is not None for r in rows),'both_starters':sum(r['features']['home_actual_starter_assumed']['value'] is not None and r['features']['away_actual_starter_assumed']['value'] is not None for r in rows),'tipster_verified':0,'tipster_rows_audited':tipster_rows,'tipster_kbo_header_marked':tipster_kbo_marked,'price_receipt_verified':0},'season_counts':dict(Counter(str(r['season']) for r in rows)),'date_range':[games[0]['date'],games[-1]['date']],'exclusion_and_missing_reasons':dict(counts),'output_sha256':out,'limitations':['Team IDs are source names; fixture/player IDs not independently authenticated.','Archived scores are not independently authenticated official results.','Same-day updates excluded; previous calendar day completion and availability are retrospective assumptions, not observed receipt.','Player lineup and starter identities use postgame inference; never prospective.','Prices lack actual receipt; market is null on missing, ambiguous, time or score conflict.','No independently linked tipster votes, bullpen 24/48h or actual decision timestamp. TailSlips events were scanned by game_header marker only; no KBO header is not proof that all possible tipster sources lack KBO.','A batter/pitcher aggregate has partial source coverage; absent history or invalid stat makes the player history null, not zero. Previous-game relief count is not a 24/48h pitch count.','Consumers must wait for this ready manifest and verify SHA256 before reading both output files.']}
    tmp=OUT/'inventory.json.tmp';tmp.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,OUT/'inventory.json');print(json.dumps(inventory['counts'],ensure_ascii=False));print(json.dumps(inventory['exclusion_and_missing_reasons'],ensure_ascii=False))

if __name__=='__main__':build()
