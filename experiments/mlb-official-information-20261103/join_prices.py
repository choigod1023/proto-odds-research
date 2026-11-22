import collections,gzip,hashlib,json,pathlib,shutil
import collect
P=pathlib.Path(__file__).resolve().parent; D=P/'evidence'/'betman-cache';D.mkdir(exist_ok=True)
S=[pathlib.Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence'),pathlib.Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence')]
TEAM={
'Arizona Diamondbacks':('AZ','애리조나다이아몬드백스'),'Athletics':('OA','애슬레틱스'),'Atlanta Braves':('AT','애틀랜타브레이브스'),'Baltimore Orioles':('BA','볼티모어오리올스'),'Boston Red Sox':('BO','보스턴레드삭스'),'Chicago Cubs':('CC','시카고컵스'),'Chicago White Sox':('CW','시카고화이트삭스'),'Cincinnati Reds':('CI','신시내티레즈'),'Cleveland Guardians':('CL','클리블랜드가디언스'),'Colorado Rockies':('CO','콜로라도로키스'),'Detroit Tigers':('DE','디트로이트타이거즈'),'Houston Astros':('HO','휴스턴애스트로스'),'Kansas City Royals':('KC','캔자스시티로얄스'),'Los Angeles Angels':('AN','LA에인절스'),'Los Angeles Dodgers':('LA','LA다저스'),'Miami Marlins':('FL','마이애미말린스'),'Milwaukee Brewers':('MI','밀워키브루어스'),'Minnesota Twins':('MN','미네소타트윈스'),'New York Mets':('NM','뉴욕메츠'),'New York Yankees':('NY','뉴욕양키스'),'Philadelphia Phillies':('PH','필라델피아필리스'),'Pittsburgh Pirates':('PI','피츠버그파이어리츠'),'San Diego Padres':('SD','샌디에이고파드리스'),'San Francisco Giants':('SF','샌프란시스코자이언츠'),'Seattle Mariners':('SE','시애틀매리너스'),'St. Louis Cardinals':('SL','세인트루이스카디널스'),'Tampa Bay Rays':('TB','탬파베이레이스'),'Texas Rangers':('TE','텍사스레인저스'),'Toronto Blue Jays':('TO','토론토블루제이스'),'Washington Nationals':('MO','워싱턴내셔널스')}
def main():
    by=collections.defaultdict(list); refs={}; rawseen=set(); errors=[]
    for ix,root in enumerate(S):
        records=json.loads((root/'attempts.json').read_text(encoding='utf8'))
        for rec in records:
            if not rec.get('name','').startswith('detail-2026-') or rec.get('http_status')!=200:continue
            path=root/pathlib.Path(rec['body_file']).name
            if not path.exists():errors.append(rec['name']+':missing');continue
            compressed=path.read_bytes();raw=gzip.decompress(compressed); sha=hashlib.sha256(raw).hexdigest()
            if sha!=rec.get('body_sha256'):raise ValueError('price cache hash mismatch '+str(path))
            if sha in rawseen:continue
            rawseen.add(sha);(D/(sha+'.body.gz')).write_bytes(compressed)
            refs[sha]={'source_name':rec['name'],'request_json':rec['request_json'],'received_at':rec['received_at'],'url':rec['url'],'http_status':rec['http_status'],'uncompressed_sha256':sha,'compressed_sha256':hashlib.sha256(compressed).hexdigest()}
            doc=json.loads(raw); ts=rec['request_json']['gmTs']
            if str(doc.get('gmTs'))!=str(ts):raise ValueError('round mismatch')
            table=doc.get('compSchedules',{})
            for vals in table.get('datas',[]):
                row=dict(zip(table.get('keys',[]),vals))
                if row.get('leagueCode')=='BS002' and row.get('betId')=='2' and row.get('betTypId')=='2' and row.get('betNm')=='야구 승패':by[row.get('gameDate')].append((ts,sha,row))
    schedule=json.loads(gzip.decompress((collect.E/'schedule-2026.json.gz').read_bytes())); chosen=collect.selected(collect.games(schedule)); out=[]; counts=collections.Counter()
    for g in chosen:
        counts['selected']+=1; ms=int(collect.stamp(g['gameDate']).timestamp()*1000); candidates=[]
        for ts,sha,r in by[ms]:
            if any(g['teams'][s]['team']['name'] not in TEAM for s in ('home','away')):continue
            if any((r.get(s+'Id'),str(r.get(s+'Name') or '').replace(' ','')) != TEAM[g['teams'][s]['team']['name']] for s in ('home','away')):continue
            if r.get('handi')!=21 or r.get('gameSubject') not in (None,'') or any(r.get(k) not in (0,0.0,None) for k in ('winHandi','drawHandi','loseHandi','drawAllot')):continue
            if r.get('gameReject')!='0' or r.get('buyReject')!='0':continue
            try:
                hp=float(r['winAllot']);ap=float(r['loseAllot']);hs=int(g['teams']['home']['score']);ass=int(g['teams']['away']['score'])
                if not (1<hp<1000 and 1<ap<1000):continue
            except (TypeError,ValueError,KeyError):continue
            candidates.append((ts,sha,r,hp,ap))
        uniq={(x[0],x[2]['matchSeq'],x[3],x[4]) for x in candidates}
        if not uniq:counts['no_exact_verified_match']+=1;continue
        if len(uniq)!=1:counts['ambiguous_multiple_markets_or_prices']+=1;continue
        ts,seq,hp,ap=next(iter(uniq))
        # Only after unique selection, compare result; no score-driven selection.
        expected='0' if hs>ass else '2' if hs<ass else None
        if expected is None or any(x[2].get('mchScore')!=f'{hs}:{ass}' or x[2].get('gameResult')!=expected for x in candidates):
            counts['score_or_settlement_mismatch']+=1;continue
        sources=sorted({x[1] for x in candidates});counts['matched']+=1
        out.append({'gamePk':g['gamePk'],'gameDate':g['gameDate'],'gmId':'G101','gmTs':ts,'matchSeq':seq,'teams':{side:{'mlb_team_id':g['teams'][side]['team']['id'],'mlb_name':g['teams'][side]['team']['name'],'betman_id':candidates[0][2][side+'Id'],'betman_name':candidates[0][2][side+'Name']} for side in ('home','away')},'market':{'betId':'2','betTypId':'2','betNm':'야구 승패','handi_code':21,'drawAllot':0},'home_odds':hp,'away_odds':ap,'postgame':{'official_score':[hs,ass]},'response_sha256s':sources,'price_observed_at':None,'received_at':[refs[x]['received_at'] for x in sources],'availability':'retrospective_current_receipt_only','executable_price_verified':False})
    for name,obj in [('price-join.json',{'coverage':dict(counts),'records':out,'sources':refs,'errors':errors,'qualification':'Betman Korean baseball win/loss market; retrospective current quote only. Exact pregame quote, historical execution and payout timing unverified.'})]:
        (P/name).write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'coverage':dict(counts),'sources':len(refs),'missing_cache_files':errors},ensure_ascii=False))
if __name__=='__main__':main()
