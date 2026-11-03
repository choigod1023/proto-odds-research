"""Read-only reuse of official Betman caches; do not infer legacy market semantics."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import gzip,hashlib,json,shutil
from pathlib import Path
import collect
ROOT=Path(__file__).resolve().parent
CACHES=[Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence'),Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence')]
KST=timezone(timedelta(hours=9))
def scan():
    idx={(g['gameDate'],g['startTime'][:5],g['homeTeam']['value'],g['awayTeam']['value']):g for g in collect.census()}
    seen=set();candidates=[];sources=[];failures=[]
    target=ROOT/'evidence'/'prices';target.mkdir(parents=True,exist_ok=True)
    for root in CACHES:
        ledger=json.loads((root/'attempts.json').read_text(encoding='utf8'))
        for path in sorted(list(root.glob('detail-2025-*.body.gz'))+list(root.glob('detail-2026-*.body.gz'))):
            raw=gzip.decompress(path.read_bytes());digest=hashlib.sha256(raw).hexdigest()
            records=[x for x in ledger if x.get('body_file','').endswith('/'+path.name) and x.get('http_status')==200 and x.get('body_sha256')==digest]
            if not records:failures.append(str(path));continue
            if digest in seen:continue
            seen.add(digest);x=json.loads(raw); block=x.get('compSchedules') or {};keys=block.get('keys') or [];found=[]
            for values in block.get('datas',[]):
                r=dict(zip(keys,values))
                if r.get('itemCode')!='VL':continue
                try:
                    t=datetime.fromtimestamp(r['gameDate']/1000,KST);key=(t.strftime('%Y-%m-%d'),t.strftime('%H:%M'),str(r.get('homeId')),str(r.get('awayId'))); win=float(r.get('winAllot') or 0); lose=float(r.get('loseAllot') or 0)
                except (TypeError,ValueError,OverflowError):continue
                if key not in idx or not(1<win<1000 and 1<lose<1000):continue
                g=idx[key];typ=r.get('handi')
                if typ not in (0,2,9):continue
                score=f"{g['homeTeam']['score']}:{g['awayTeam']['score']}"
                found.append({'game_id':str(g['number']),'date':key[0],'kickoff_kst':t.isoformat(),'home_id':key[2],'away_id':key[3], 'round':x['gmTs'],'matchSeq':r.get('matchSeq'),'leagueCode':r.get('leagueCode'),'handi_code':typ,'betId':r.get('betId'),'betTypId':r.get('betTypId'),'betNm':r.get('betNm'),'winTxt':r.get('winTxt'),'loseTxt':r.get('loseTxt'),'winAllot':win,'loseAllot':lose,'drawAllot':r.get('drawAllot'),'winHandi':r.get('winHandi'),'loseHandi':r.get('loseHandi'),'mchScore':r.get('mchScore'),'gameResult':r.get('gameResult'),'gameReject':r.get('gameReject'),'buyReject':r.get('buyReject'),'outcome_score_matches_if_code0':r.get('mchScore')==score if typ==0 else None,'source_sha256':digest,'source_received_at':records[-1]['received_at'],'selection_semantics':'unverified_legacy_null_labels','availability':'retrospective_current_receipt_no_pit'})
            if found:
                targetpath=target/(digest+'.body.gz');shutil.copyfile(path,targetpath)
                assert hashlib.sha256(gzip.decompress(targetpath.read_bytes())).hexdigest()==digest
                sources.append({'body_sha256':digest,'file':str(targetpath.relative_to(ROOT)).replace('\\','/'),'original':str(path),'capture':records[-1]})
                candidates.extend(found)
    candidates.sort(key=lambda x:(int(x['game_id']),x['handi_code'],str(x['round']),str(x['matchSeq'])))
    (ROOT/'postgame'/'price-candidates.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in candidates),encoding='utf8',newline='\n')
    summary={'cache_bodies_verified':len(seen),'unverified_body_files':len(failures),'matched_source_bodies':len(sources),'candidate_rows':len(candidates),'candidate_fixtures':len({c['game_id'] for c in candidates}),'rows_by_legacy_handi_code':dict(Counter(str(c['handi_code']) for c in candidates)),'code0_score_matches':sum(c['outcome_score_matches_if_code0'] is True for c in candidates),'code0_score_mismatches':sum(c['outcome_score_matches_if_code0'] is False for c in candidates),'validated_full_market_selection_count':0,'strict_pit_count':0,'reason':'legacy betId/betTypId/betNm and selection labels absent; period/settlement rules not independently verified','failures':failures,'source_manifest':sources}
    (ROOT/'price-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('source_manifest','failures')},ensure_ascii=False))
if __name__=='__main__':scan()
