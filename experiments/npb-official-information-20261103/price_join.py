"""Read-only matching to archived official Betman price responses, never PIT proof."""
import collections, gzip, hashlib, json, re, shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
EVIDENCES=[('kbo',Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence')),('kleague',Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence'))]
PRICE_IDS={'C1':'t','C2':'c','C3':'s','C4':'db','C5':'d','C6':'g','P1':'l','P2':'m','P3':'e','P4':'b','P5':'f','P6':'h'}
JST=timezone(timedelta(hours=9))

def read_jsonl(p): return [json.loads(x) for x in p.read_text(encoding='utf8').splitlines()]
def match_price(records,census,files):
    fixture_pairs=collections.Counter()
    for day in census:
        for path in day.get('fixture_paths',[]):
            m=re.fullmatch(r'/scores/2026/\d{4}/([a-z]+)-([a-z]+)-\d+/',path)
            if m: fixture_pairs[(day['date'],m[1],m[2])]+=1
    index=collections.defaultdict(list)
    for g in records:
        if g['state']=='completed':
            key=(g['date'],g['home_team_id'],g['away_team_id'])
            if fixture_pairs[key]==1: index[key].append(g)
    quotes=[]; invalid=collections.Counter(); used={}
    for name,body,meta in files:
        if hashlib.sha256(body).hexdigest()!=meta['body_sha256']: raise ValueError('price body hash mismatch')
        data=json.loads(body); comp=data.get('compSchedules') or {}; keys=comp.get('keys',[])
        for raw in comp.get('datas',[]):
            if len(raw)!=len(keys): invalid['row_width_mismatch']+=1; continue
            x=dict(zip(keys,raw))
            if x.get('leagueName')!='NPB': continue
            if (x.get('betId'),x.get('betTypId'),x.get('betNm'),x.get('betTypNm'))!=('2','2','야구 승패','일반 승패'): continue
            if str(x.get('gameReject'))!='0' or str(x.get('buyReject'))!='0' or x.get('live')!='N':
                invalid['source_reject_or_live_flag']+=1;continue
            try:
                scheduled=datetime.fromtimestamp(x['gameDate']/1000,JST)
                home,away=PRICE_IDS[x['homeId']],PRICE_IDS[x['awayId']]
                price_home,price_away=float(x['winAllot']),float(x['loseAllot'])
                if not (price_home>1 and price_away>1 and x.get('winTxt')=='승' and x.get('loseTxt')=='패' and float(x.get('drawAllot') or 0)==0): raise ValueError('incomplete two-way odds')
            except (KeyError,TypeError,ValueError,OverflowError): invalid['invalid_team_time_or_prices']+=1; continue
            matches=index.get((scheduled.date().isoformat(),home,away),[])
            if len(matches)!=1: invalid['no_unique_selected_fixture']+=1; continue
            game=matches[0]; actual=datetime.fromisoformat(game['actual_start_jst']); delta=abs((actual-scheduled).total_seconds())
            if delta>1800: invalid['start_time_difference_over_30min']+=1;continue
            score=game['postgame']['official_final_score']; official_score=f"{score['home']}:{score['away']}"
            source_score=x.get('mchScore')
            if source_score and source_score!=official_score: invalid['source_result_score_mismatch']+=1;continue
            quotes.append({'official_game_id':game['official_game_id'],'date':game['date'],'source_name':name,'source_sha256':meta['body_sha256'],'source_url':meta['url'],'request_params':meta.get('request_json'),'received_at':meta['received_at'],'match_seq':x.get('matchSeq'),'source_scheduled_start':scheduled.isoformat(),'official_actual_start':actual.isoformat(),'start_difference_seconds':delta,'source_home_id':x['homeId'],'source_away_id':x['awayId'],'source_status':{'game_reject':x['gameReject'],'buy_reject':x['buyReject'],'live':x['live'],'proto_status':x.get('protoStatus'),'game_result':x.get('gameResult')},'product':{'bet_id':'2','bet_type_id':'2','name':'야구 승패','type':'일반 승패','line':None,'period':'unverified_from_response'},'full_selections':[{'selection':'home','odds':price_home},{'selection':'away','odds':price_away}],'strict_pit_verified':False,'settlement_rules_verified':False,'source_result_score':source_score,'official_result_score':official_score,'result_score_agrees':source_score==official_score if source_score else None})
            used[name]=meta
    return quotes,invalid,used

def run():
    records=read_jsonl(ROOT/'postgame-records.jsonl'); census=read_jsonl(ROOT/'fixture-census.jsonl')
    files=[]; source_by_name={}; seen=set()
    for label,evidence in EVIDENCES:
        attempts=json.loads((evidence/'attempts.json').read_text(encoding='utf8')); meta_by_name={a['name']:a for a in attempts if a.get('body_file') and a.get('http_status')==200}
        for p in sorted(evidence.glob('detail-2026-*.body.gz')):
            base=p.name.removesuffix('.body.gz'); meta=meta_by_name.get(base)
            if not meta:continue
            body=gzip.decompress(p.read_bytes()); content_hash=hashlib.sha256(body).hexdigest()
            if content_hash!=meta['body_sha256']:raise ValueError('source cache hash mismatch: '+str(p))
            if (base,content_hash) in seen:continue
            seen.add((base,content_hash)); name=base if label=='kbo' else label+'--'+base; source_by_name[name]=p; files.append((name,body,meta))
    quotes,invalid,used=match_price(records,census,files); out=ROOT/'price-raw';out.mkdir(exist_ok=True)
    for name,meta in used.items():
        dest=out/(name+'.body.gz')
        source=source_by_name[name]
        if dest.exists() and gzip.decompress(dest.read_bytes())!=gzip.decompress(source.read_bytes()):raise ValueError('existing cached price source differs')
        if not dest.exists():shutil.copyfile(source,dest)
        (out/(name+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
    (ROOT/'retrospective-price-links.jsonl').write_text(''.join(json.dumps(q,ensure_ascii=False,sort_keys=True)+'\n' for q in quotes),encoding='utf8',newline='\n')
    counts=collections.Counter(str(q['result_score_agrees']) for q in quotes)
    manifest={'source_response_files_examined':len(files),'source_files_with_matching_rows':len(used),'retrospective_quote_rows':len(quotes),'unique_completed_games_with_quote':len({q['official_game_id'] for q in quotes}),'strict_pit_price_games':0,'source_result_score_agreement_rows':dict(counts),'excluded_candidate_reasons':dict(invalid),'period_verified':False,'settlement_verified':False,'scope':'only available 2026 official response cache; current receipt does not establish pregame price'}
    (ROOT/'price-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':run()
