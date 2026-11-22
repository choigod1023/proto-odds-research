from __future__ import annotations
import collections,datetime as dt,gzip,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'price-evidence'
# Official J.League club slug -> exact Betman club id and Korean name.
TEAMS={'gosaka':('128','감바오사카'),'cosaka':('133','세레소오사카'),'kobe':('136','비셀고베'),'urawa':('122','우라와레드다이아몬즈'),'shonan':('130','쇼난벨마레'),'kashima':('120','가시마앤틀러스'),'yokohamafc':('296','요코하마FC'),'okayama':('30314','파지아노오카야마'),'tokyov':('123','도쿄베르디'),'hiroshima':('129','산프레체히로시마'),'yokohamafm':('124','요코하마F마리노스'),'ftokyo':('270','FC도쿄'),'nagoya':('127','나고야그램퍼스'),'shimizu':('126','시미즈에스펄스'),'machida':('30532','FC마치다젤비아'),'niigata':('193','알비렉스니가타'),'kawasakif':('86','가와사키프론탈레'),'kyoto':('134','교토상가FC'),'kashiwa':('132','가시와레이솔'),'fukuoka':('135','아비스파후쿠오카')}
KST=dt.timezone(dt.timedelta(hours=9))
def market(r):
    modern=r.get('betId')=='1' and r.get('betTypId')=='1' and r.get('betNm')=='축구 승무패'
    legacy=r.get('betId') is None and r.get('betTypId') is None
    if not (modern or legacy) or r.get('handi')!=0 or r.get('gameSubject') not in (None,''):return False
    if any(r.get(k) not in (0,0.0,None) for k in ('winHandi','drawHandi','loseHandi')):return False
    if r.get('gameReject')!='0' or r.get('buyReject')!='0':return False
    try:return all(1<float(r[x])<1000 for x in ('winAllot','drawAllot','loseAllot'))
    except (TypeError,ValueError,KeyError):return False

def main():
    lineups=[json.loads(x) for x in gzip.decompress((ROOT/'lineups.jsonl.gz').read_bytes()).splitlines()]
    labels={x['fixture_id']:x for x in [json.loads(x) for x in gzip.decompress((ROOT/'postgame-labels.jsonl.gz').read_bytes()).splitlines()]}
    attempts=json.loads((SOURCE/'attempts.json').read_text(encoding='utf-8'))
    indexed=[]; source_hashes={}; filecount=0
    for path in sorted(SOURCE.glob('detail-2025-*.body.gz')):
        raw=gzip.decompress(path.read_bytes()); sha=hashlib.sha256(raw).hexdigest()
        rec=next((x for x in attempts if Path(x.get('body_file','')).name==path.name and x.get('http_status')==200),None)
        if rec is None or rec.get('body_sha256')!=sha:raise ValueError('unverified Betman source '+path.name)
        data=json.loads(raw);cs=data['compSchedules'];filecount+=1;source_hashes[path.name]=sha
        for arr in cs['datas']:
            r=dict(zip(cs['keys'],arr))
            if r.get('leagueCode')=='SC007' and market(r):indexed.append((data.get('gmTs'),path.name,rec,r))
    out=[]; exclusions=[]
    for fixture in lineups:
        fid=fixture['fixture_id'];clubs=fixture['clubs'];score=labels[fid]['score_reported']
        if not score or any(c['id'] not in TEAMS for c in clubs):exclusions.append({'fixture_id':fid,'reason':'score_or_team_crosswalk_missing'});continue
        m=re.search(r'(\d{4})/(\d{1,2})/(\d{1,2}).*?(\d{1,2}):(\d{2})',fixture['kickoff_display_actual'])
        if not m:exclusions.append({'fixture_id':fid,'reason':'kickoff_unparsed'});continue
        kickoff=dt.datetime(*map(int,m.groups()),tzinfo=KST); candidates=[]
        for ts,filename,rec,r in indexed:
            if any((str(r.get(s+'Id')),r.get(s+'Name'))!=TEAMS[c['id']] for s,c in zip(('home','away'),clubs)):continue
            try: bet=dt.datetime.fromtimestamp(r['gameDate']/1000,KST)
            except (TypeError,ValueError,OverflowError):continue
            if bet.date()!=kickoff.date() or abs((bet-kickoff).total_seconds())>900:continue
            outcome='0' if score[0]>score[1] else '1' if score[0]==score[1] else '2'
            if r.get('mchScore')!=f'{score[0]}:{score[1]}' or r.get('gameResult')!=outcome:continue
            candidates.append((ts,filename,rec,r,bet))
        if len(candidates)!=1:exclusions.append({'fixture_id':fid,'reason':'no_exact_valid_price_in_available_rounds' if not candidates else 'ambiguous_multiple_offers','candidate_count':len(candidates)});continue
        ts,filename,rec,r,bet=candidates[0]
        out.append({'fixture_id':fid,'market':'90-minute 1X2; verify settlement rules','team_ids':[c['id'] for c in clubs],'betman_round':ts,'matchSeq':r.get('matchSeq'),'prices':[r['winAllot'],r['drawAllot'],r['loseAllot']],'betman_kickoff':bet.isoformat(),'jleague_actual_kickoff':kickoff.isoformat(),'postgame_score':score,'result_code':r['gameResult'],'price_received_at_utc':rec['received_at'],'price_observed_at':None,'strict_pit':False,'price_source':filename,'price_source_sha256':source_hashes[filename]})
    keys=[(x['betman_round'],x['matchSeq']) for x in out]
    if len(set(keys))!=len(keys):raise ValueError('duplicate assigned price product')
    for fn,items in [('price-joins.jsonl.gz',out),('price-exclusions.jsonl.gz',exclusions)]:
        payload=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in items).encode()
        (ROOT/fn).write_bytes(gzip.compress(payload,mtime=0))
    (ROOT/'price-source-hashes.json').write_text(json.dumps(source_hashes,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('lineups',len(lineups),'price joined',len(out),'excluded',len(exclusions),'cached 2025 rounds',filecount,'reasons',collections.Counter(x['reason'] for x in exclusions))
if __name__=='__main__':main()
