"""Conservative retrospective fixture-to-official-market correspondence."""
import gzip,json,re
from collections import Counter,defaultdict
from datetime import datetime
from collect import ROOT,KST,fixtures,inputs,plan_rounds,readgz,sha

KBL={'06':'수원KT소닉붐','10':'울산현대모비스피버스','16':'원주DB프로미','35':'서울삼성썬더스','50':'창원LG세이커스','55':'서울SK나이츠','60':'부산KCC이지스','64':'대구한국가스공사페가수스','66':'고양소노스카이거너스','70':'안양정관장레드부스터스'}
V={'1001':['대한항공'],'1002':['삼성'],'1004':['KB손해보험'],'1005':['현대캐피탈'],'1006':['한국전력'],'1008':['OK저축은행','OK금융그룹'],'1009':['우리카드']}
NPB={'t':'C1','c':'C2','s':'C3','db':'C4','d':'C5','g':'C6','l':'P1','m':'P2','e':'P3','b':'P4','f':'P5','h':'P6'}
J1={'gosaka':('128','감바오사카'),'cosaka':('133','세레소오사카'),'kobe':('136','비셀고베'),'urawa':('122','우라와레드다이아몬즈'),'shonan':('130','쇼난벨마레'),'kashima':('120','가시마앤틀러스'),'yokohamafc':('296','요코하마FC'),'okayama':('30314','파지아노오카야마'),'tokyov':('123','도쿄베르디'),'hiroshima':('129','산프레체히로시마'),'yokohamafm':('124','요코하마F마리노스'),'ftokyo':('270','FC도쿄'),'nagoya':('127','나고야그램퍼스'),'shimizu':('126','시미즈에스펄스'),'machida':('30532','FC마치다젤비아'),'niigata':('193','알비렉스니가타'),'kawasakif':('86','가와사키프론탈레'),'kyoto':('134','교토상가FC'),'kashiwa':('132','가시와레이솔'),'fukuoka':('135','아비스파후쿠오카')}
LEAGUE={'KBL':'BK001','VLEAGUE_M':'VL001','NPB':'BS004','J1':'SC007'}
PRODUCT={'KBL':('3','농구 승패'),'VLEAGUE_M':('4','배구 승패'),'NPB':('2','야구 승패'),'J1':('1','축구 승무패')}
def normalize(s):return re.sub(r'\s+','',s or '')
def team_id(league,team):
    return NPB.get(team) if league=='NPB' else J1.get(team,(None,None))[0] if league=='J1' else team
def valid_names(f,r):
    for side in ('home','away'):
        team=f[side];name=normalize(r.get(side+'Name'))
        if r.get(side+'Id')!=team_id(f['league'],team):return False
        if f['league']=='KBL' and name!=KBL.get(team):return False
        if f['league']=='VLEAGUE_M' and not any(x in name for x in V.get(team,[])):return False
        if f['league']=='J1' and name!=J1.get(team,(None,None))[1]:return False
    return True
def market(league,r):
    bid,bname=PRODUCT[league]; modern=(r.get('betId')==bid and r.get('betTypId')==('1' if league=='J1' else '2') and r.get('betNm')==bname and r.get('handi')==(0 if league=='J1' else 21))
    legacy=(r.get('betId') is None and r.get('betTypId') is None and r.get('handi')==0)
    if not (modern or legacy) or r.get('gameSubject') not in (None,''):return False
    if any(r.get(k) not in (0,0.0) for k in ('winHandi','drawHandi','loseHandi')):return False
    if league=='J1' and r.get('handi')!=0:return False
    if league!='J1' and r.get('drawAllot') not in (0,0.0):return False
    try:return all(1<float(r[k])<1000 for k in (('winAllot','drawAllot','loseAllot') if league=='J1' else ('winAllot','loseAllot')))
    except (KeyError,TypeError,ValueError):return False
def candidate(f,r):
    if f['state']!='completed' or r.get('leagueCode')!=LEAGUE[f['league']] or not valid_names(f,r) or not market(f['league'],r):return False
    try:
        official=datetime.fromisoformat(f['kickoff']);stamp=datetime.fromtimestamp(r['gameDate']/1000,KST);delta=abs((stamp-official).total_seconds());score=f['score'];h,a=int(score[0]),int(score[1])
    except (KeyError,TypeError,ValueError,OverflowError):return False
    tolerance=1800 if f['league']=='NPB' else 900 if f['league']=='J1' else 0
    if stamp.date()!=official.date() or delta>tolerance:return False
    if r.get('mchScore')!=f'{h}:{a}':return False
    code='0' if h>a else '2' if h<a else '1' if f['league']=='J1' else '4'
    return r.get('gameResult')==code
def unique(cs):return cs[0] if len(cs)==1 else None
def safe_status(r):return r.get('gameReject')=='0' and r.get('buyReject')=='0'
def main():
    fs=fixtures();ledger=json.loads((ROOT/'evidence/attempts.json').read_bytes());closed=next(x for x in ledger if x['name']=='closed-list' and x['http_status']==200)
    body=readgz(ROOT/closed['body_file']);assert sha(body)==closed['body_sha256'];mapping,plan=plan_rounds(fs,json.loads(body)['schedules']['data'])
    snaps={};indexed=defaultdict(list)
    for ts,item in plan.items():
        name=f"detail-{item['gmOsidTsYear']}-{item['gmOsidTs']}";r=next((x for x in reversed(ledger) if x['name']==name and x.get('http_status')==200),None)
        if not r:continue
        raw=readgz(ROOT/r['body_file']);assert sha(raw)==r['body_sha256'];d=json.loads(raw)
        if d.get('gmTs')!=ts:raise ValueError('wrong official round')
        cs=d['compSchedules'];snaps[ts]=r
        for arr in cs['datas']:
            x=dict(zip(cs['keys'],arr));code=x.get('leagueCode')
            if code not in LEAGUE.values():continue
            try:day=datetime.fromtimestamp(x['gameDate']/1000,KST).date().isoformat()
            except (KeyError,TypeError,ValueError,OverflowError):continue
            indexed[(ts,code,x.get('homeId'),x.get('awayId'),day)].append(x)
    output=[];excluded=[];coverage=defaultdict(Counter)
    for f in fs:
        group=f['league']+':'+f['season'];coverage[group]['source_fixtures']+=1
        def exclude(reason,detail=None):
            coverage[group][reason]+=1;excluded.append({'fixture_id':f['fixture_id'],'league':f['league'],'season':f['season'],'reason':reason,'detail':detail})
        if f['state']!='completed':exclude(f['state']);continue
        coverage[group]['completed']+=1;rounds=mapping.get(f['fixture_id'],[])
        if not rounds:exclude('no_round_in_sale_window');continue
        if any(t not in snaps for t in rounds):exclude('round_response_missing');continue
        matches=[]
        for ts in rounds:
            key=(ts,LEAGUE[f['league']],team_id(f['league'],f['home']),team_id(f['league'],f['away']),f['kickoff'][:10])
            for r in indexed[key]:
                if candidate(f,r):matches.append((ts,r))
        good=[x for x in matches if safe_status(x[1])]
        if not good:
            if matches:exclude('reject_or_missing_status',[{'gmTs':ts,'matchSeq':r['matchSeq'],'gameReject':r.get('gameReject'),'buyReject':r.get('buyReject')} for ts,r in matches])
            else:exclude('no_exact_product_identity_result')
            continue
        # Multiple eligible markets are ambiguous even if one has a better price.
        if len(matches)!=1:exclude('multiple_candidates',[{'gmTs':ts,'matchSeq':r['matchSeq'],'gameReject':r.get('gameReject'),'buyReject':r.get('buyReject')} for ts,r in matches]);continue
        ts,r=unique(good);meta=snaps[ts];stamp=datetime.fromtimestamp(r['gameDate']/1000,KST)
        output.append({'fixture_id':f['fixture_id'],'league':f['league'],'season':f['season'],'official_fixture':f,
          'gmTs':ts,'matchSeq':r['matchSeq'],'betman_league_code':r['leagueCode'],'betman_home_id':r['homeId'],'betman_away_id':r['awayId'],
          'betman_home_name':r['homeName'],'betman_away_name':r['awayName'],'betman_start':stamp.isoformat(),
          'start_difference_seconds':abs((stamp-datetime.fromisoformat(f['kickoff'])).total_seconds()),
          'market':{k:r.get(k) for k in ('betId','betTypId','betNm','handi','gameSubject','winHandi','drawHandi','loseHandi')},
          'prices':{'home':r['winAllot'],'draw':r['drawAllot'],'away':r['loseAllot']},
          'result':{k:r.get(k) for k in ('mchScore','gameResult','gameReject','buyReject')},
          'settlement_scope':'90min_without_extra_time' if f['league']=='J1' else 'final_including_extra_time' if f['league'] in ('KBL','NPB') else 'full_match_sets',
          'draw_void_refund':f['league']=='NPB' and f['score'][0]==f['score'][1],
          'response_file':meta['body_file'],'response_sha256':meta['body_sha256'],'received_at':meta['received_at'],'price_observed_at':None,'execution_price_verified':False})
        coverage[group]['linked']+=1
    if len({(x['gmTs'],x['matchSeq']) for x in output})!=len(output):raise ValueError('duplicate assigned market')
    out=ROOT/'joined-prices.jsonl.gz';raw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in output).encode();out.write_bytes(gzip.compress(raw,mtime=0))
    exc=ROOT/'exclusions.jsonl.gz';excraw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in excluded).encode();exc.write_bytes(gzip.compress(excraw,mtime=0))
    cv={k:dict(v) for k,v in sorted(coverage.items())}
    source=inputs();kbl=json.loads(source['blu:census.json']);idx=json.loads(source['blu:postgame-index.json']);j1=json.loads(source['fblu:selection.json']);npb=json.loads(source['nlu:manifest.json'])
    cv['KBL:2025-26'].update(population_scheduled=len(kbl['regular_season_fixtures']),source_sample_selected=len(kbl['sample_ids']),source_player_validated=idx['sample_validated'],linked_with_player_validated=sum(x['league']=='KBL' and x['official_fixture'].get('player_record_verified') for x in output))
    cv['J1:2025'].update(population_scheduled=len(j1['official_fixture_ids']),source_sample_selected=len(j1['selected_ids']))
    cv['NPB:2026'].update(source_window_census_fixtures=npb['census_fixture_paths'],source_sample_selected=npb['selected_fixtures'],source_census_not_selected=npb['census_fixture_paths']-npb['selected_fixtures'])
    cv['VLEAGUE_W:2025-26']={'status':'awaiting_source','reason':'no fixed committed fixture artifact at scope freeze'};cv['MLB']={'status':'awaiting_source'};cv['NBA']={'status':'awaiting_source'}
    (ROOT/'coverage.json').write_bytes((json.dumps(cv,ensure_ascii=False,indent=2)+'\n').encode())
    cross={'KBL':KBL,'VLEAGUE_M':V,'NPB_official_to_betman':NPB,'J1_official_to_betman':J1};(ROOT/'team-crosswalk.json').write_bytes((json.dumps(cross,ensure_ascii=False,indent=2)+'\n').encode())
    manifest={'status':'ready_for_join_partial_sources','source_fixtures':len(fs),'joined':len(output),'rounds_required':len(plan),'rounds_received':len(snaps),'new_requests':sum(x.get('origin')=='new_request' for x in ledger),'max_new_requests':200,
      'joined_sha256':sha(out.read_bytes()),'joined_uncompressed_sha256':sha(raw),'exclusions_sha256':sha(exc.read_bytes()),'coverage_sha256':sha((ROOT/'coverage.json').read_bytes()),'crosswalk_sha256':sha((ROOT/'team-crosswalk.json').read_bytes()),'sources_sha256':sha((ROOT/'inputs/sources.json').read_bytes()),'ledger_sha256':sha((ROOT/'evidence/attempts.json').read_bytes()),
      'limitation':'retrospective official snapshot; historical observation, T-30 and executable price unverified'}
    (ROOT/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode());print(json.dumps({'joined':len(output),'rounds':len(snaps),'coverage':cv},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
