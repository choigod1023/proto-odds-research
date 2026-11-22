import collections, datetime as dt, gzip, hashlib, json, re, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCES=[Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence'),Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence')]
NAMES={'06':'수원KT소닉붐','10':'울산현대모비스피버스','16':'원주DB프로미','35':'서울삼성썬더스','50':'창원LG세이커스','55':'서울SK나이츠','60':'부산KCC이지스','64':'대구한국가스공사페가수스','66':'고양소노스카이거너스','70':'안양정관장레드부스터스'}
KST=dt.timezone(dt.timedelta(hours=9))
def norm(s):return re.sub(r'\s+','',s or '')
def market_win(r):
    modern=(r.get('betId')=='3' and r.get('betTypId')=='2' and r.get('betNm')=='농구 승패' and r.get('winTxt')=='승' and r.get('loseTxt')=='패')
    legacy=(r.get('betId') is None and r.get('betTypId') is None and r.get('handi')==0 and all(float(r.get(k) or 0)==0 for k in ('winHandi','drawHandi','loseHandi')))
    try: return (modern or legacy) and float(r['winAllot'])>1 and float(r['loseAllot'])>1 and float(r.get('drawAllot') or 0)==0
    except (TypeError,ValueError,KeyError):return False

def main():
    c=json.loads((ROOT/'census.json').read_text(encoding='utf8')); fs={x['gmkey']:x for x in c['regular_season_fixtures'] if x['gmkey'] in c['sample_ids']}
    datekeys={(x['gameDate'],x['gameStart'],x['tcodeH'],x['tcodeA']):k for k,x in fs.items()}
    candidates=collections.defaultdict(dict); seen_bodies=set(); valid_sources=0
    for src in SOURCES:
        ledger=json.loads((src/'attempts.json').read_text(encoding='utf8'))
        entries={x['name']:x for x in ledger if x.get('http_status')==200 and str(x.get('name','')).startswith('detail-202')}
        for label,meta in entries.items():
            bodypath=src/Path(meta['body_file']).name
            if not bodypath.exists():continue
            raw=gzip.decompress(bodypath.read_bytes()); digest=hashlib.sha256(raw).hexdigest()
            if digest!=meta['body_sha256']:raise ValueError('source SHA mismatch '+str(bodypath))
            if digest in seen_bodies:continue
            seen_bodies.add(digest); valid_sources+=1
            data=json.loads(raw); cs=data.get('compSchedules',{}); keys=cs.get('keys',[])
            for arr in cs.get('datas',[]):
                r=dict(zip(keys,arr))
                if r.get('leagueCode')!='BK001' or not market_win(r):continue
                try: stamp=dt.datetime.fromtimestamp(r['gameDate']/1000,KST); key=(stamp.strftime('%Y%m%d'),stamp.strftime('%H%M'),str(r['homeId']),str(r['awayId']))
                except (TypeError,ValueError,KeyError):continue
                g=datekeys.get(key)
                if not g:continue
                if norm(r.get('homeName'))!=NAMES.get(key[2]) or norm(r.get('awayName'))!=NAMES.get(key[3]):continue
                ident=(str(data.get('gmTs')),str(r.get('matchSeq')))
                candidates[g][ident]=(r,meta,bodypath,digest)
    original=json.loads((ROOT/'evidence/postgame-schedule.body').read_bytes()); scores={x['gmkey']:x for x in original if x.get('glkey')=='S47G01'}
    out=[]; excl=collections.Counter(); price_dir=ROOT/'evidence'/'odds';price_dir.mkdir(exist_ok=True)
    for g in c['sample_ids']:
        options=candidates[g]
        if len(options)!=1:
            excl['no_exact_offer_in_available_rounds' if not options else 'multiple_round_or_offer_candidates']+=1;continue
        r,meta,path,digest=next(iter(options.values())); s=scores[g]
        if str(r.get('buyReject'))!='0' or str(r.get('gameReject'))!='0':excl['purchase_or_game_restriction']+=1;continue
        if r.get('mchScore')!=f"{s['scoreH']}:{s['scoreA']}":excl['score_mismatch']+=1;continue
        result='0' if s['scoreH']>s['scoreA'] else '2' if s['scoreA']>s['scoreH'] else '1'
        if str(r.get('gameResult'))!=result:excl['settlement_mismatch']+=1;continue
        dest=price_dir/(digest+'.body.gz')
        if not dest.exists():shutil.copyfile(path,dest)
        out.append({'gmkey':g,'product':'full_game_win_2way','home_id':s['tcodeH'],'away_id':s['tcodeA'],'home_odds':r['winAllot'],'away_odds':r['loseAllot'],'round':meta['name'],'match_seq':r['matchSeq'],'betman_source_sha256':digest,'source_file':'evidence/odds/'+dest.name,'request':meta.get('request_json'),'source_url':meta.get('url'),'http_status':meta.get('http_status'),'compressed_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'received_at':meta.get('received_at'),'price_observed_at':None,'execution_price_verified':False,'market_code':{'handi':r['handi'],'betId':r['betId'],'betTypId':r['betTypId']},'result_validation':'postgame_source_compared'})
    obj={'selected_games':len(fs),'joined_win':len(out),'excluded':dict(excl),'unique_cached_round_bodies_scanned':valid_sources,'source_scope':'two provided caches only; no claim that all historical rounds are present','strict_pit_prices':0,'other_markets':'not linked: classification and per-product exception rules need independent verification','rows':out}
    (ROOT/'price-join.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps({k:v for k,v in obj.items() if k!='rows'},ensure_ascii=False))
if __name__=='__main__':main()
