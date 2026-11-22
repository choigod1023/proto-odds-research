"""Join frozen official schedules to official Betman 1X2 snapshots, offline."""
import gzip
import hashlib
import json
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from collect import ROOT,KST,load_fixtures,select_rounds,sha,readgz

TEAM={
 'K01':('울산',['울산현대축구단','울산HDFC']), 'K02':('수원',['수원삼성블루윙즈']),
 'K03':('포항',['포항스틸러스']), 'K04':('제주',['제주유나이티드','제주SKFC']),
 'K05':('전북',['전북현대모터스']), 'K06':('부산',['부산아이파크']),
 'K07':('전남',['전남드래곤즈']), 'K08':('성남',['성남FC']),
 'K09':('서울',['FC서울']), 'K10':('대전',['대전하나시티즌']),
 'K17':('대구',['대구FC']), 'K18':('인천',['인천유나이티드']),
 'K20':('경남',['경남FC']), 'K21':('강원',['강원FC']), 'K22':('광주',['광주FC']),
 'K26':('부천',['부천FC1995']), 'K27':('안양',['FC안양']),
 'K29':('수원FC',['수원FC']), 'K31':('서울E',['서울이랜드']),
 'K32':('안산',['안산그리너스']), 'K34':('충남아산',['충남아산프로축구단']),
 'K35':('김천',['김천상무프로축구단']), 'K36':('김포',['김포FC']),
 'K37':('충북청주',['충북청주프로축구단']), 'K38':('천안',['천안시티FC']),
 'K39':('화성',['화성FC']), 'K40':('파주',['파주프런티어']),
 'K41':('김해',['김해FC2008']), 'K42':('용인',['용인FC'])}
LEAGUE={1:'SC001',2:'SC003'}

def names_match(f,c):
    for side in ('home','away'):
        tid=f.get(side+'Team')
        if tid not in TEAM or c.get(side+'Id')!=tid: return False
        short,official=TEAM[tid]
        if f.get(side+'TeamName')!=short: return False
        if str(c.get(side+'Name') or '').replace(' ','') not in official: return False
    return True

def market_kind(c):
    modern=(c.get('betId')=='1' and c.get('betTypId')=='1' and c.get('betNm')=='축구 승무패')
    legacy=(c.get('betId') is None and c.get('betTypId') is None)
    if not (modern or legacy) or c.get('handi')!=0 or c.get('gameSubject') not in (None,''): return False
    if any(c.get(k) not in (0,0.0,None) for k in ('winHandi','drawHandi','loseHandi')):return False
    return True

def strict_match(f,c):
    if f.get('gameStatus')!='FE' or f.get('endYn')!='Y':return False
    if c.get('leagueCode')!=LEAGUE.get(f['leagueId']) or not names_match(f,c) or not market_kind(c):return False
    if c.get('gameReject')!='0' or c.get('buyReject')!='0':return False
    try:
        official=datetime.strptime(f['gameDate']+' '+f['gameTime'],'%Y.%m.%d %H:%M').replace(tzinfo=KST)
        bet=datetime.fromtimestamp(c['gameDate']/1000,KST)
        prices=[float(c[k]) for k in ('winAllot','drawAllot','loseAllot')]
        hg,ag=int(f['homeGoal']),int(f['awayGoal'])
    except (TypeError,ValueError,KeyError,OverflowError):return False
    if bet!=official or not all(1<p<1000 for p in prices):return False
    result='0' if hg>ag else '1' if hg==ag else '2'
    return c.get('mchScore')==f'{hg}:{ag}' and c.get('gameResult')==result

def unique_candidate(candidates):
    # Never select a more attractive price when multiple rounds qualify.
    return candidates[0] if len(candidates)==1 else None

def main():
    fixtures=load_fixtures(); ledger=json.loads((ROOT/'evidence/attempts.json').read_text(encoding='utf8'))
    closed=next(x for x in ledger if x['name']=='closed-list' and x['http_status']==200)
    raw=readgz(ROOT/closed['body_file']);assert sha(raw)==closed['body_sha256']
    mapping,plan=select_rounds(fixtures,json.loads(raw)['schedules']['data'])
    snaps={};checked={}
    for ts,x in plan.items():
        name=f"detail-{x['gmOsidTsYear']}-{x['gmOsidTs']}"
        rec=next((r for r in reversed(ledger) if r['name']==name and r.get('http_status')==200),None)
        if rec is None:continue
        raw=readgz(ROOT/rec['body_file']);assert sha(raw)==rec['body_sha256']
        data=json.loads(raw)
        if data.get('gmTs')!=ts:raise ValueError('wrong round '+name)
        cs=data['compSchedules'];snaps[ts]=(rec,[dict(zip(cs['keys'],values)) for values in cs['datas']])
        checked[name]=rec['body_sha256']
    cohorts=defaultdict(Counter);out=[];excluded=[]; maps=defaultdict(lambda:{'schedule_names':set(),'betman_names':set()})
    for key,f in sorted(fixtures.items()):
        year,league,gid=key;group=f'{year}-K{league}';cohorts[group]['scheduled']+=1
        def exclude(reason,details=None):
            cohorts[group][reason]+=1
            excluded.append({'fixture_id':f'kleague:{year}:{league}:{gid}','reason':reason,'details':details})
        for side in ('home','away'):
            maps[f[side+'Team']]['schedule_names'].add(f.get(side+'TeamName'))
        if f.get('gameStatus')!='FE' or f.get('endYn')!='Y':exclude('not_finished');continue
        cohorts[group]['finished']+=1
        rounds=mapping[key]
        if not rounds:exclude('no_round_in_sale_window');continue
        if any(ts not in snaps for ts in rounds):exclude('round_response_missing');continue
        candidates=[];restricted=[]
        for ts in rounds:
            rec, data=snaps[ts]
            for c in data:
                if c.get('leagueCode')!=LEAGUE[league]:continue
                for side in ('home','away'):
                    if c.get(side+'Id') in TEAM:maps[c[side+'Id']]['betman_names'].add(c.get(side+'Name'))
                if strict_match(f,c):candidates.append((ts,rec,c))
                elif c.get('buyReject') not in (None,'0') and strict_match(f,c|{'buyReject':'0'}):
                    restricted.append({'gmTs':ts,'matchSeq':c['matchSeq'],'buyReject':c['buyReject']})
        if not candidates:
            if restricted:exclude('selection_buy_reject_status',restricted)
            else:exclude('no_exact_valid_market')
            continue
        selected=unique_candidate(candidates)
        if selected is None:exclude('ambiguous_multiple_markets',[{'gmTs':ts,'matchSeq':c['matchSeq']} for ts,_,c in candidates]);continue
        ts,rec,c=selected;g=plan[ts]
        out.append({'fixture_id':f'kleague:{year}:{league}:{gid}','season':year,'league_id':league,
           'kickoff_kst':datetime.fromtimestamp(c['gameDate']/1000,KST).isoformat(),
           'home_team_id':f['homeTeam'],'away_team_id':f['awayTeam'],
           'league_home_name':f['homeTeamName'],'league_away_name':f['awayTeamName'],
           'betman_home_name':c['homeName'],'betman_away_name':c['awayName'],
           'official_score':[f['homeGoal'],f['awayGoal']],'settlement_code':c['gameResult'],
           'gmId':'G101','gmTs':ts,'round_no':g['gmOsidTs'],'matchSeq':c['matchSeq'],
           'betman_league_code':c['leagueCode'],'market_type':{'betId':c['betId'],'betTypId':c['betTypId'],'betNm':c['betNm'],'handi':c['handi'],'gameSubject':c['gameSubject']},
           'prices':{'home':c['winAllot'],'draw':c['drawAllot'],'away':c['loseAllot']},
           'gameReject':c['gameReject'],'buyReject':c['buyReject'],
           'received_at':rec['received_at'],'price_observed_at':None,'execution_price_verified':False,
           'availability_tier':'retrospective_official_snapshot_time_unverified',
           'response_file':rec['body_file'],'response_sha256':rec['body_sha256']})
        cohorts[group]['linked']+=1
    duplicates=Counter((x['gmTs'],x['matchSeq']) for x in out)
    if any(n>1 for n in duplicates.values()):raise ValueError('same market linked twice')
    raw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in out).encode('utf8')
    path=ROOT/'joined-prices.jsonl.gz';path.write_bytes(gzip.compress(raw,mtime=0))
    coverage={k:dict(v) for k,v in sorted(cohorts.items())}
    (ROOT/'coverage.json').write_bytes((json.dumps(coverage,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    (ROOT/'exclusions.jsonl').write_bytes(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in excluded).encode('utf8'))
    crosswalk={k:{'canonical_schedule_short_name':TEAM[k][0],'allowed_betman_names':TEAM[k][1],
                   'observed_schedule_names':sorted(v['schedule_names']),
                   'observed_betman_names':sorted(x for x in v['betman_names'] if x)}
               for k,v in sorted(maps.items()) if k in TEAM}
    (ROOT/'team-crosswalk.json').write_bytes((json.dumps(crosswalk,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    manifest={'status':'ready_for_join' if out else 'blocked_with_actual_response_evidence',
      'fixture_count':len(fixtures),'matched':len(out),'candidate_rounds':len(plan),'rounds_with_response':len(snaps),
      'new_request_count':sum(x.get('origin')=='kleague_new_request' for x in ledger),
      'max_new_requests':250,'joined_gzip_sha256':sha(path.read_bytes()),'joined_uncompressed_sha256':sha(raw),
      'source_manifest_sha256':sha((ROOT/'inputs/sources.json').read_bytes()),
      'ledger_sha256':sha((ROOT/'evidence/attempts.json').read_bytes()),
      'coverage_sha256':sha((ROOT/'coverage.json').read_bytes()),'team_crosswalk_sha256':sha((ROOT/'team-crosswalk.json').read_bytes()),
      'exclusions_sha256':sha((ROOT/'exclusions.jsonl').read_bytes()),
      'official_response_sha256':checked,'limitation':'current receipt; no historical observation, pregame availability, T-30 or executable price verified'}
    (ROOT/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print(json.dumps({'matched':len(out),'rounds':len(snaps),'coverage':coverage},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
