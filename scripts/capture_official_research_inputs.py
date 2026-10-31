"""Capture up to three KBO fixtures once; detached JSON output, no DB/daemon."""
import argparse
import copy
import importlib
import math
import re
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from official_fact_capture import KBO_URL, normalize_kbo_schedule, timestamp, semantic_hash

LINEUP_URL='https://www.koreabaseball.com/ws/Schedule.asmx/GetLineUpAnalysis'
MAX_BODY=2*1024*1024

def fetch(session,url,data):
    start=time.monotonic(); response=None
    try:
        response=session.post(url,data=data,timeout=15,allow_redirects=False,stream=True)
        if response.status_code!=200: raise ValueError('unexpected_http_status_'+str(response.status_code))
        chunks=[]; size=0
        for chunk in response.iter_content(chunk_size=65536):
            if time.monotonic()-start>15: raise ValueError('total_time_limit')
            size+=len(chunk)
            if size>MAX_BODY: raise ValueError('response_size_limit')
            chunks.append(chunk)
        raw=b''.join(chunks); at=datetime.now(timezone.utc).isoformat()
        return raw,{'url':url,'status':response.status_code,'received_at':at,'raw_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    finally:
        if response is not None: response.close()

def capture(day,session):
    ledger=[]; missing=[]; facts=[]; games=[]
    data={'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day}
    try:
        raw,entry=fetch(session,KBO_URL,data); ledger.append(entry)
        parsed=normalize_kbo_schedule(raw,expected_date=day,received_at=entry['received_at'])
    except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
        return {'schema':'official-fact-capture-v1','games':[],'facts':[],'missing':[{'kind':'calendar','reason':type(exc).__name__+': '+str(exc)[:100]}],'requests':ledger,'request_attempts':1}
    games=sorted(parsed['games'],key=lambda g:g['kickoff_at'])[:3]; ids={g['official_game_id'] for g in games}
    facts=[f for f in parsed['facts'] if f['official_game_id'] in ids]
    missing=[m for m in parsed['missing'] if m['official_game_id'] in ids]
    if not games: missing.append({'kind':'upcoming_fixture','reason':'no_scheduled_game_before_t30_in_calendar'})
    attempts=1
    for game in games:
        attempts+=1
        try:
            raw,entry=fetch(session,LINEUP_URL,game['lineup_request']); ledger.append(entry); body=json.loads(raw)
            flag=body[0][0].get('LINEUP_CK') if isinstance(body,list) and body and body[0] and isinstance(body[0][0],dict) else None
            reason='not_announced' if flag is False else 'announced_player_ids_not_independently_validated' if flag is True else 'unrecognized_response'
            missing.append({'official_game_id':game['official_game_id'],'kind':'confirmed_batting_order','reason':reason,'source_url':LINEUP_URL,'raw_sha256':entry['raw_sha256'],'received_at':entry['received_at']})
        except (requests.RequestException,ValueError,KeyError,IndexError,TypeError) as exc:
            missing.append({'official_game_id':game['official_game_id'],'kind':'confirmed_batting_order','reason':type(exc).__name__})
            break  # Stop on the first failed request, including rate limiting.
    for game in games: game.pop('lineup_request',None)
    return {'schema':'official-fact-capture-v1','games':games,'facts':facts,'missing':missing,'requests':ledger,'request_attempts':attempts}

def eligible_fact(fact, row):
    """Conservative boundary check; dispatcher still owns full validation."""
    try:
        official=fact['official']; claim=fact['claim']; market=fact['market']
        decision=timestamp(fact['decision_at']); kickoff=timestamp(fact['kickoff_at'])
        if decision > kickoff-timedelta(minutes=30): return False
        if not fact.get('canonical_game_id') or row.get('game_id') != fact['canonical_game_id']: return False
        if not market.get('product_id') or row.get('market_id') != market['product_id']: return False
        if official.get('confirmed') is not True or official.get('identity_checked') is not True or official.get('external_receipt_verified') is not True: return False
        if not isinstance(fact.get('after_value'),dict) or fact['after_value'].get('announcement_status')!='confirmed': return False
        if fact.get('kind') not in {'confirmed_starting_pitcher','announced_batting_order','confirmed_lineup_change','official_absence'}: return False
        if fact.get('semantic_sha256')!=semantic_hash(fact): return False
        game_id=str(fact.get('official_game_id') or '')
        valid_source = (fact.get('league')=='KBO' and row.get('league_id')=='kbo' and official.get('url') in {KBO_URL,LINEUP_URL}) or (fact.get('league')=='MLB' and row.get('league_id')=='mlb' and game_id.isdigit() and official.get('url')==f'https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live')
        claim_url=claim.get('url')
        if not valid_source or fact.get('sport') != row.get('sport') or not isinstance(claim_url,str) or not claim_url.startswith('https://'): return False
        if not any(isinstance(e,dict) and e.get('url')==official.get('url') and e.get('raw_sha256')==official.get('raw_sha256') and e.get('received_at')==official.get('received_at') for e in row.get('source_evidence', [])): return False
        if not all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in (official.get('raw_sha256'),claim.get('raw_sha256'))): return False
        if timestamp(claim['published_at']) > decision: return False
        if timestamp(official['received_at']) > decision or timestamp(claim['received_at']) > decision: return False
        if timestamp(market['received_at']) > decision or timestamp(market['observed_at']) > decision: return False
        if not market.get('market') or not market.get('period'): return False
        outcomes,odds=market['outcomes'],market['odds']
        if not isinstance(outcomes,list) or not isinstance(odds,list) or len(outcomes)<2 or len(outcomes)!=len(odds): return False
        if len(set(outcomes))!=len(outcomes) or any(not isinstance(x,str) or not x for x in outcomes): return False
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<=1 for x in odds): return False
        return True
    except (ValueError,KeyError,TypeError): return False

def contains_result(value):
    forbidden={'result','results','outcome','outcome_index','score','home_score','away_score','winner','label','settlement'}
    if isinstance(value,dict): return any(str(k).lower() in forbidden or contains_result(v) for k,v in value.items())
    if isinstance(value,list): return any(contains_result(v) for v in value)
    return False

def run_capture_pipeline(bundle, *, adapter, build_manifest, raw_path, inputs_path, manifest_path):
    """Run injected real adapter/dispatcher; reject unsafe ready claims, save detached files."""
    paths=[Path(raw_path),Path(inputs_path),Path(manifest_path)]
    if len({p.resolve() for p in paths})!=3 or any(p.exists() for p in paths):
        raise ValueError('output_paths_must_be_distinct_and_new')
    facts=bundle.get('facts')
    if not isinstance(facts,list): raise ValueError('invalid_bundle')
    paths[0].parent.mkdir(parents=True,exist_ok=True)
    paths[0].write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    rows=adapter(copy.deepcopy(facts))
    if not isinstance(rows,list): raise ValueError('adapter_must_return_list')
    allowed={'kleague1','kleague2','kbo','npb','mlb','kbl','nba','vleague_m','vleague_w','football_other'}
    for row in rows:
        if not isinstance(row,dict) or row.get('schema_version')!='fact-delta-inputs-v1' or row.get('league_id') not in allowed: raise ValueError('invalid_adapter_row')
        if row.get('status') not in {'ready','baseline_only','rejected'} or type(row.get('research_eligible')) is not bool: raise ValueError('invalid_adapter_status')
        if not all(k in row for k in ('sport','game_id','market_id','input_id')) or not row.get('input_id'): raise ValueError('missing_adapter_identity')
        if not isinstance(row.get('features'),dict) or not isinstance(row.get('source_evidence'),list) or not isinstance(row.get('reasons'),list): raise ValueError('invalid_adapter_fields')
        if contains_result(row['features']): raise ValueError('target_result_in_features')
        if row['research_eligible'] or row['status']=='ready':
            if row['status']!='ready' or not row['research_eligible'] or not row['source_evidence'] or not any(eligible_fact(f,row) for f in facts): raise ValueError('unsupported_ready_input')
    manifest=build_manifest(copy.deepcopy(rows))
    for path,value in zip(paths[1:],(rows,manifest)):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return {'raw_path':str(paths[0]),'inputs_path':str(paths[1]),'manifest_path':str(paths[2]),'inputs':len(rows)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True,help='KST YYYYMMDD');p.add_argument('--out',type=Path,required=True,help='new raw bundle path');p.add_argument('--adapter-out',type=Path);p.add_argument('--manifest-out',type=Path);p.add_argument('--adapter-module');p.add_argument('--adapter-function');args=p.parse_args()
    if args.out.exists(): raise SystemExit('Output already exists; choose a new path')
    if len(args.date)!=8 or not args.date.isdigit(): raise SystemExit('Invalid date')
    options=(args.adapter_out,args.manifest_out,args.adapter_module,args.adapter_function)
    if any(options) and not all(options): raise SystemExit('Provide --adapter-out, --manifest-out, --adapter-module and --adapter-function together')
    adapter=dispatcher=None
    if all(options):
        output_paths=[args.out,args.adapter_out,args.manifest_out]
        if len({x.resolve() for x in output_paths})!=3 or any(x.exists() for x in output_paths): raise SystemExit('Output paths must be distinct and new; no request made')
        try:
            adapter=getattr(importlib.import_module(args.adapter_module),args.adapter_function)
            dispatcher=importlib.import_module('research_dispatch').build_manifest
        except (ImportError,AttributeError) as exc: raise SystemExit('Adapter or dispatcher unavailable; no request made: '+str(exc)) from exc
    with requests.Session() as session:
        session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx','Accept':'application/json'})
        bundle=capture(args.date,session)
    if adapter:
        run_capture_pipeline(bundle,adapter=adapter,build_manifest=dispatcher,raw_path=args.out,inputs_path=args.adapter_out,manifest_path=args.manifest_out)
    else:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'games':len(bundle['games']),'facts':len(bundle['facts']),'missing':len(bundle['missing']),'requests':bundle['request_attempts']}))
if __name__=='__main__': main()

