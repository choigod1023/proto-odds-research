"""Inspect an already saved odds artifact. No network, scheduler, or production writes."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

MARKETS={'승패':('home','away'),'승무패':('home','draw','away')}

def stamp(value):
    if not isinstance(value,str): raise ValueError('timestamp_required')
    try: dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc: raise ValueError('invalid_timestamp') from exc
    if dt.utcoffset() is None: raise ValueError('timezone_required')
    return dt.astimezone(timezone.utc)

def safe_poll_decision(*, authorized, now, last_attempt=None, last_status=None, minimum_interval=60):
    """Pure policy only: caller must independently verify permission and run scheduling."""
    clock=stamp(now)
    if authorized is not True:return {'allow':False,'reason':'source_permission_unverified'}
    if type(minimum_interval) is not int or minimum_interval<60:raise ValueError('minimum_interval_below_floor')
    if last_status in ('forbidden','unauthorized','rate_limited'):return {'allow':False,'reason':'manual_review_required'}
    if last_attempt is not None:
        last=stamp(last_attempt)
        if last>clock:return {'allow':False,'reason':'future_last_attempt'}
        delay=max(minimum_interval,300) if last_status in ('timeout','error') else minimum_interval
        if clock<last+timedelta(seconds=delay):return {'allow':False,'reason':'backoff'}
    return {'allow':True,'reason':'eligible_for_operator_controlled_attempt'}

def inspect_snapshot(raw, *, local_read_at, max_age_seconds=300):
    """Validate named win markets against the same snapshot's full price vector.

    No inference of provider receipt, issued year, player identity, tradability, or pregame status.
    """
    if not isinstance(raw,bytes) or not raw:raise ValueError('missing_artifact')
    if type(max_age_seconds) is not int or max_age_seconds<=0:raise ValueError('invalid_age_limit')
    try: data=json.loads(raw)
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:raise ValueError('invalid_json') from exc
    if not isinstance(data,dict) or not isinstance(data.get('markets'),dict) or not isinstance(data.get('odds'),dict):raise ValueError('invalid_snapshot')
    generated=stamp(data.get('generated_at'));read_at=stamp(local_read_at)
    if generated>read_at:raise ValueError('future_snapshot')
    age=(read_at-generated).total_seconds();stale=age>max_age_seconds
    rows=[];excluded=Counter();counts=Counter()
    for round_id, records in data['markets'].items():
        if not isinstance(records,dict) or not isinstance(data['odds'].get(round_id),dict):raise ValueError('invalid_round')
        for game_no,m in records.items():
            if not isinstance(m,dict):excluded['invalid_market_record']+=1;continue
            market=m.get('market'); sides=MARKETS.get(market)
            if sides is None:excluded['other_market']+=1;continue
            if not str(round_id).isdigit() or not str(game_no).isdigit() or str(m.get('game_no'))!=str(game_no):excluded['invalid_product_coordinates']+=1;continue
            if m.get('label') not in ('',None) or type(m.get('n_way')) is not int or m['n_way']!=len(sides):excluded['wrong_market_shape']+=1;continue
            odds=m.get('odds'); saved=data['odds'][round_id].get(game_no)
            if not isinstance(odds,list) or len(odds)!=len(sides) or odds!=saved or any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in odds):excluded['incomplete_or_conflicting_prices']+=1;continue
            if any(not isinstance(m.get(k),str) or not m[k].strip() for k in ('sport','league','home','away','date')) or m['home']==m['away']:
                excluded['missing_fixture_identity']+=1;continue
            rows.append({'round':str(round_id),'game_no':str(game_no),'league':m['league'],'sport':m['sport'],
                         'home':m['home'],'away':m['away'],'source_date_text':m['date'],'market':market,
                         'period':None,'line':None,'sides':list(sides),'odds':odds,
                         'official_game_id':None,'kickoff_at':None,'provider_quote_at':None,
                         'provider_received_at':None,'quote_eligible':False,'unavailable_reason':'source_timestamp_and_identity_not_certified'})
            counts[m['league']]+=1
    return {'schema':'local-odds-inspection-v1','source_kind':'saved_repository_artifact','source_sha256':hashlib.sha256(raw).hexdigest(),
            'source_raw_http_available':False,'snapshot_generated_at':generated.isoformat(),'local_read_at':read_at.isoformat(),
            'snapshot_age_seconds':age,'configured_max_age_seconds':max_age_seconds,'stale':stale,
            'official_product_id_verified':False,'live_eligible':False,'records':rows,'accepted_by_league':dict(counts),'excluded':dict(excluded)}

def protect_previous(previous, raw, *, local_read_at, max_age_seconds=300):
    """Return prior data untouched on error or older generations; separately report status."""
    try: candidate=inspect_snapshot(raw,local_read_at=local_read_at,max_age_seconds=max_age_seconds)
    except (ValueError,TypeError,KeyError) as exc:return previous,{'updated':False,'reason':str(exc)}
    if candidate['stale']:return previous,{'updated':False,'reason':'stale_snapshot'}
    if previous is not None:
        try:
            if stamp(candidate['snapshot_generated_at'])<stamp(previous['snapshot_generated_at']):return previous,{'updated':False,'reason':'older_snapshot'}
            if stamp(candidate['snapshot_generated_at'])==stamp(previous['snapshot_generated_at']) and candidate['source_sha256']!=previous.get('source_sha256'):return previous,{'updated':False,'reason':'same_time_conflicting_snapshot'}
        except (KeyError,ValueError,TypeError):return previous,{'updated':False,'reason':'invalid_previous_snapshot'}
    return candidate,{'updated':True,'reason':'stale_inspection' if candidate['stale'] else 'inspection_only'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--max-age',type=int,default=300);args=p.parse_args()
    if args.out.exists():raise SystemExit('output_exists')
    raw=args.snapshot.read_bytes();now=datetime.now(timezone.utc).isoformat(); result=inspect_snapshot(raw,local_read_at=now,max_age_seconds=args.max_age)
    summary={k:v for k,v in result.items() if k!='records'}; summary['record_count']=len(result['records']);summary['source_path']=str(args.snapshot)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'records':len(result['records']),'stale':result['stale'],'live_eligible':result['live_eligible'],'by_league':result['accepted_by_league']},ensure_ascii=True))
if __name__=='__main__':main()
