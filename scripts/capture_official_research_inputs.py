"""Capture up to three KBO fixtures once; detached JSON output, no DB/daemon."""
import argparse
import base64
import binascii
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
        if response.headers.get('Content-Encoding','identity').lower() not in ('','identity'): raise ValueError('encoded_response_not_supported')
        chunks=[]; size=0
        for chunk in response.iter_content(chunk_size=65536):
            if time.monotonic()-start>15: raise ValueError('total_time_limit')
            size+=len(chunk)
            if size>MAX_BODY: raise ValueError('response_size_limit')
            chunks.append(chunk)
        raw=b''.join(chunks); at=datetime.now(timezone.utc).isoformat()
        return raw,{'url':url,'status':response.status_code,'received_at':at,'raw_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'raw_base64':base64.b64encode(raw).decode('ascii')}
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

def valid_response_bytes(entry):
    if not isinstance(entry,dict) or entry.get('status')!=200: return False
    value=entry.get('raw_base64'); size=entry.get('bytes')
    if not isinstance(value,str) or type(size) is not int or not 0<size<=MAX_BODY: return False
    if len(value)>4*((MAX_BODY+2)//3): return False
    try: raw=base64.b64decode(value,validate=True)
    except (binascii.Error,ValueError): return False
    return len(raw)==size and hashlib.sha256(raw).hexdigest()==entry.get('raw_sha256')

def eligible_fact(fact, row, bundle):
    """Check locally recorded evidence; independent external receipt remains unverified."""
    try:
        official=fact['official']; claim=fact['claim']; market=fact['market']
        decision=timestamp(fact['decision_at']); kickoff=timestamp(fact['kickoff_at'])
        if decision > kickoff-timedelta(minutes=30): return False
        if not fact.get('canonical_game_id') or row.get('game_id') != fact['canonical_game_id']: return False
        if not market.get('product_id') or row.get('market_id') != market['product_id']: return False
        if official.get('confirmed') is not True or official.get('identity_checked') is not True: return False
        kind=fact.get('kind'); after=fact.get('after_value')
        if kind=='confirmed_starter_change': valid=isinstance(after,dict) and after.get('status')=='confirmed'
        elif kind=='official_absence': valid=isinstance(after,dict) and after.get('status')=='absent'
        elif kind=='confirmed_lineup_change': valid=isinstance(after,list) and bool(after) and all(isinstance(x,dict) and x.get('status')=='confirmed' for x in after)
        elif kind in {'bullpen_workload_24h','bullpen_workload_48h'}: valid=type(after) is int and after>=0
        else: valid=False
        if not valid: return False
        if fact.get('semantic_sha256') is not None and fact['semantic_sha256']!=semantic_hash(fact): return False
        game_id=str(fact.get('official_game_id') or '')
        valid_source = (fact.get('league')=='KBO' and row.get('league_id')=='kbo' and official.get('url') in {KBO_URL,LINEUP_URL}) or (fact.get('league')=='MLB' and row.get('league_id')=='mlb' and game_id.isdigit() and official.get('url')==f'https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live')
        claim_url=claim.get('url')
        if not valid_source or fact.get('sport') != row.get('sport') or not isinstance(claim_url,str) or not claim_url.startswith('https://'): return False
        if not any(isinstance(e,dict) and e.get('url')==official.get('url') and e.get('raw_sha256')==official.get('raw_sha256') and e.get('received_at')==official.get('received_at') for e in row.get('source_evidence', [])): return False
        if not any(isinstance(e,dict) and valid_response_bytes(e) and e.get('url')==official.get('url') and e.get('raw_sha256')==official.get('raw_sha256') and e.get('received_at')==official.get('received_at') for e in bundle.get('requests', [])): return False
        if not all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in (official.get('raw_sha256'),claim.get('raw_sha256'))): return False
        claim_received=timestamp(claim['received_at'])
        if claim.get('published_at') is not None and timestamp(claim['published_at'])>claim_received: return False
        if timestamp(official['received_at']) > decision or claim_received > decision: return False
        if not timestamp(market['observed_at']) <= timestamp(market['received_at']) <= decision: return False
        if market.get('market')!='win' or market.get('period')!='full' or market.get('line') is not None: return False
        outcomes,odds=market['outcomes'],market['odds']
        if outcomes not in (['home','away'],['home','draw','away']) or not isinstance(odds,dict) or set(odds)!=set(outcomes): return False
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<=1 for x in odds.values()): return False
        return True
    except (ValueError,KeyError,TypeError): return False

FACT_KEYS=('fact_id','canonical_game_id','official_game_id','league','sport','kind','before_value','after_value','units','player_ids','claim','official','decision_at','kickoff_at','market','revision')
EVIDENCE_KEYS={'claim':('url','published_at','received_at','raw_sha256'), 'official':('url','published_at','received_at','raw_sha256','identity_checked','confirmed')}

def adapt_with_context(facts, *, to_adapter_input, context):
    """Call the actual adapter contract using caller-supplied context, never synthesize data."""
    if not isinstance(context,dict) or any(k not in context for k in ('registry','baseline','market','input_id')):
        raise ValueError('adapter_context_missing')
    registry=context['registry']
    if not isinstance(registry,dict) or not isinstance(registry.get('product'),dict): raise ValueError('invalid_adapter_registry')
    target_game=registry.get('canonical_game_id');target_product=registry['product'].get('product_id')
    compact=[]
    for original in facts:
        if not isinstance(original,dict): raise ValueError('invalid_fact')
        game_id=original.get('canonical_game_id'); product=(original.get('market') or {}).get('product_id')
        if (game_id is not None and game_id!=target_game) or (product is not None and product!=target_product): raise ValueError('mixed_context_facts')
        item={k:copy.deepcopy(original[k]) for k in FACT_KEYS if k in original}
        for name,keys in EVIDENCE_KEYS.items():
            if isinstance(item.get(name),dict): item[name]={k:item[name][k] for k in keys if k in item[name]}
        compact.append(item)
    result=to_adapter_input(compact,context['registry'],context['baseline'],context['market'],input_id=context['input_id'],workloads=context.get('workloads'))
    return [result]

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
    def fact_key(fact):
        market=fact.get('market') if isinstance(fact,dict) else None
        return (fact.get('canonical_game_id'),market.get('product_id')) if isinstance(market,dict) else (None,None)
    def row_key(row): return (row.get('game_id'),row.get('market_id'))
    known_row_keys={row_key(row) for row in rows if isinstance(row,dict) and row.get('game_id') and row.get('market_id')}
    unmatched=sum(1 for fact in facts if fact_key(fact) not in known_row_keys)
    if unmatched and any(isinstance(row,dict) and (row.get('status')=='ready' or row.get('research_eligible') is True) for row in rows): raise ValueError('unmapped_facts_in_ready_batch')
    allowed={'kleague1','kleague2','kbo','npb','mlb','kbl','nba','vleague_m','vleague_w','football_other'}
    for row in rows:
        if not isinstance(row,dict) or row.get('schema_version')!='fact-delta-inputs-v1' or row.get('league_id') not in allowed: raise ValueError('invalid_adapter_row')
        if row.get('status') not in {'ready','baseline_only','rejected'} or type(row.get('research_eligible')) is not bool: raise ValueError('invalid_adapter_status')
        if not all(k in row for k in ('sport','game_id','market_id','input_id')) or not row.get('input_id'): raise ValueError('missing_adapter_identity')
        if not isinstance(row.get('features'),dict) or not isinstance(row.get('source_evidence'),list) or not isinstance(row.get('reasons'),list): raise ValueError('invalid_adapter_fields')
        if contains_result(row['features']): raise ValueError('target_result_in_features')
        if row['research_eligible'] or row['status']=='ready':
            matching=[fact for fact in facts if fact_key(fact)==row_key(row)]
            if row['status']!='ready' or not row['research_eligible'] or not row['source_evidence'] or not matching or not all(eligible_fact(f,row,bundle) for f in matching): raise ValueError('unsupported_ready_input')
    manifest=build_manifest(copy.deepcopy(rows))
    for path,value in zip(paths[1:],(rows,manifest)):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return {'raw_path':str(paths[0]),'inputs_path':str(paths[1]),'manifest_path':str(paths[2]),'inputs':len(rows),'unmatched_fact_count':unmatched}

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True,help='KST YYYYMMDD');p.add_argument('--out',type=Path,required=True,help='new raw bundle path');p.add_argument('--adapter-out',type=Path);p.add_argument('--manifest-out',type=Path);p.add_argument('--adapter-module');p.add_argument('--adapter-function');p.add_argument('--adapter-context',type=Path);args=p.parse_args()
    if args.out.exists(): raise SystemExit('Output already exists; choose a new path')
    if len(args.date)!=8 or not args.date.isdigit(): raise SystemExit('Invalid date')
    options=(args.adapter_out,args.manifest_out,args.adapter_module,args.adapter_function,args.adapter_context)
    if any(options) and not all(options): raise SystemExit('Provide --adapter-out, --manifest-out, --adapter-module, --adapter-function and --adapter-context together')
    adapter=dispatcher=None
    if all(options):
        output_paths=[args.out,args.adapter_out,args.manifest_out]
        if len({x.resolve() for x in output_paths})!=3 or any(x.exists() for x in output_paths): raise SystemExit('Output paths must be distinct and new; no request made')
        try:
            adapter_function=getattr(importlib.import_module(args.adapter_module),args.adapter_function)
            context=json.loads(args.adapter_context.read_text(encoding='utf8'))
            if not isinstance(context,dict) or any(k not in context for k in ('registry','baseline','market','input_id')): raise ValueError('adapter_context_missing')
            adapter=lambda facts:adapt_with_context(facts,to_adapter_input=adapter_function,context=context)
            dispatcher=importlib.import_module('research_dispatch').build_manifest
        except (ImportError,AttributeError,ValueError,OSError) as exc: raise SystemExit('Adapter, context or dispatcher unavailable; no request made: '+str(exc)) from exc
    with requests.Session() as session:
        session.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx','Accept':'application/json','Accept-Encoding':'identity'})
        bundle=capture(args.date,session)
    pipeline=None
    if adapter:
        pipeline=run_capture_pipeline(bundle,adapter=adapter,build_manifest=dispatcher,raw_path=args.out,inputs_path=args.adapter_out,manifest_path=args.manifest_out)
    else:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'games':len(bundle['games']),'facts':len(bundle['facts']),'missing':len(bundle['missing']),'requests':bundle['request_attempts'],'unmatched_fact_count':pipeline['unmatched_fact_count'] if pipeline else None}))
if __name__=='__main__': main()

