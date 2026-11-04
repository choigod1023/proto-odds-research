"""Immutable prospective common-price shadow comparisons, not real wagers.

M0 is computed and frozen first; the later operating snapshot supplies one
shared price frame. Original operating and M0 records are never repriced.
First-common snapshots are distinct from final T-30 operating performance.
"""
from copy import deepcopy
from decimal import Decimal
from datetime import timedelta,timezone
from urllib.parse import urlparse

from research.m1_service.observer import _append,read,canonical,digest,moment

ARMS=('m0_actual','operating_vote','market')


def capture_pair(path,m0_capture,export,now,*,matcher=None,clock=None):
    if matcher is None:
        from research.kbo_forward.operating_vote_bridge import match_operating
        matcher=match_operating
    result=matcher(m0_capture,export,now.isoformat())
    if result.get('status')!='eligible':return {'recorded':False,**result}
    event=m0_capture['input']['event'];kickoff=moment(event['kickoff_at'])
    if (moment(m0_capture['captured_at'])>moment(result['operating_as_of'])
            or moment(result['operating_as_of'])>now or now>kickoff-timedelta(minutes=30)):
        raise ValueError('common shadow is not a prospective pre-T30 capture')
    if result['official_game_id']!=event['official_game_id'] or result['market_id']!=m0_capture['input']['market']['market_id']:
        raise ValueError('bridge fixture/market differs')
    picks=result['three_selections'];prices=result['shared_prices']
    if set(picks)!=set(ARMS) or any(v not in ('home','away') for v in picks.values()) or set(prices)!={'home','away'}:
        raise ValueError('complete three-arm two-way frame required')
    if any(isinstance(v,bool) or not Decimal(str(v)).is_finite() or Decimal(str(v))<=1 for v in prices.values()):
        raise ValueError('invalid shared price')
    key=digest(canonical({'official_game_id':event['official_game_id'],'season':event['season']}))
    record={'type':'paired_capture','schema':'prospective-common-shadow-v1','comparison_key':key,
            'captured_at':now.isoformat(),'event':deepcopy(event),'m0_capture_hash':m0_capture['hash'],
            'operating_record_hash':result['operating_record_hash'],
            'market':{'kind':'full_game_2way','settlement_rule':'draw_void_refund','market_id':result['market_id'],
                      'odds':deepcopy(prices)},'selections':deepcopy(picks),
            'bridge':deepcopy(result),'export_sha256':export.get('export_sha256'),
            'export_path':str(export['export_path']) if export.get('export_path') else None,
            'source_receipt':deepcopy(export.get('source_receipt')),
            'source_receipt_path':str(export['source_receipt_path']) if export.get('source_receipt_path') else None,
            'sample_scope':'first_common_shadow_not_final_operating_performance',
            'final_operating_performance_verified':False,'formal_promotion_eligible':False,
            'paper_wallet_changed':False,'production_policy_changed':False}
    def make(rows):
        if any(r.get('type')=='paired_capture' and r.get('comparison_key')==key for r in rows):return None
        finished=clock() if clock is not None else now
        if finished<now or finished>kickoff-timedelta(minutes=30):raise ValueError('common processing finished after cutoff or clock moved backwards')
        record['captured_at']=finished.isoformat()
        record['timing_scope']='append-decision clock; durable write completion not certified'
        return record
    out=_append(path,make)
    return {'recorded':out is not None,'status':'eligible','comparison_key':key,
            'scope':record['sample_scope']}


def settle_pairs(path,m0_records,now):
    # The parent model ledger supplies already-validated official results; verify
    # its canonical official body and identity again instead of copying profits.
    latest={r['capture_hash']:r for r in m0_records if r.get('type')=='settlement'}
    output=[]
    for c in [r for r in read(path) if r.get('type')=='paired_capture']:
        parent=latest.get(c['m0_capture_hash'])
        if parent is None:continue
        ev=parent['evidence'];uri=urlparse(ev['uri']);event=c['event'];status=parent['economics']['status']
        earliest=c['captured_at'] if status=='cancelled' else event['kickoff_at']
        if (uri.scheme!='https' or uri.hostname not in ('www.koreabaseball.com','koreabaseball.com')
                or digest(canonical(ev['body']))!=ev['sha256']
                or not moment(earliest)<=moment(ev['received_at'])<=now):
            raise ValueError('common official result source/time differs')
        games=[g for g in ev['body']['game'] if g.get('G_ID')==event['official_game_id']]
        if len(games)!=1:raise ValueError('common official result not unique')
        g=games[0]
        if (str(g['HOME_ID'])!=event['home_id'] or str(g['AWAY_ID'])!=event['away_id']
                or str(g['SEASON_ID'])!=str(event['season'])
                or g['G_DT']!=moment(event['kickoff_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d')):
            raise ValueError('common official result identity differs')
        if status=='cancelled':
            if str(g['CANCEL_SC_ID'])=='0' or '취소' not in str(g.get('CANCEL_SC_NM','')):raise ValueError('unverified cancellation')
            h=a=None;winner=None
        elif status=='final':
            if str(g['GAME_STATE_SC'])!='3' or str(g['GAME_RESULT_CK'])!='1' or str(g['CANCEL_SC_ID'])!='0':raise ValueError('result is not official final')
            h,a=int(g['B_SCORE_CN']),int(g['T_SCORE_CN'])
            if min(h,a)<0 or (h,a)!=(parent['economics']['home_score'],parent['economics']['away_score']):raise ValueError('common score differs')
            winner='home' if h>a else 'away' if a>h else 'draw'
        else:raise ValueError('unsupported result')
        profits={}
        for arm,pick in c['selections'].items():
            refund=status=='cancelled' or winner=='draw'
            net=Decimal(0) if refund else Decimal(str(c['market']['odds'][pick]))-1 if pick==winner else Decimal(-1)
            profits[arm]={'selection':pick,'net_units':str(net),'refund':refund,'hit':None if refund else pick==winner}
        economics={'status':status,'winner':winner,'home_score':h,'away_score':a,'profits':profits}
        def make(rows):
            prior=next((r for r in reversed(rows) if r.get('type')=='paired_settlement' and r['comparison_key']==c['comparison_key']),None)
            if prior and moment(parent['evidence']['received_at'])<moment(prior['evidence']['received_at']):raise ValueError('common correction moved backwards')
            if prior and prior['economics']==economics:return None
            if prior and moment(parent['evidence']['received_at'])==moment(prior['evidence']['received_at']):raise ValueError('conflicting common result at same receipt')
            return {'type':'paired_settlement','schema':'prospective-common-shadow-v1','comparison_key':c['comparison_key'],
                    'capture_hash':c['hash'],'parent_m0_settlement_hash':parent['hash'],'recorded_at':now.isoformat(),
                    'economics':economics,'evidence':deepcopy(ev),'supersedes_hash':prior['hash'] if prior else None,
                    'sample_scope':c['sample_scope'],'final_operating_performance_verified':False,
                    'formal_promotion_eligible':False,'paper_wallet_changed':False}
        out=_append(path,make)
        if out:output.append(out)
    return output


def summary(path):
    rows=read(path);caps={r['comparison_key']:r for r in rows if r.get('type')=='paired_capture'}
    settled={r['comparison_key']:r for r in rows if r.get('type')=='paired_settlement'}
    methods={}
    for arm in ARMS:
        profits=[r['economics']['profits'][arm] for r in settled.values()];net=sum((Decimal(p['net_units']) for p in profits),Decimal(0))
        decided=[p for p in profits if not p['refund']]
        methods[arm]={'bets':len(profits),'decided':len(decided),'hits':sum(p['hit'] for p in decided),
                      'hit_rate':sum(p['hit'] for p in decided)/len(decided) if decided else None,
                      'net_units':str(net),'roi':float(net/len(profits)) if profits else None}
    return {'schema':'prospective-common-shadow-v1','captured':len(caps),'settled':len(settled),
            'methods':methods,'sample_scope':'first_common_shadow_not_final_operating_performance',
            'final_operating_performance_verified':False,'formal_promotion_eligible':False,
            'production_policy_changed':False,'paper_wallet_changed':False}


def reconcile(recorder,state,now,*,source_fn=None):
    """Existing Python cycle only; no source request before a real M0 capture."""
    from pathlib import Path
    state=Path(state);path=state/'common-ledger.jsonl'
    model_rows=read(recorder.path);current=read(path)
    used={(r['event']['season'],r['event']['official_game_id']) for r in current if r.get('type')=='paired_capture'}
    pending=[r for r in model_rows if r.get('type')=='capture' and r['roi_eligible']
             and (r['input']['event']['season'],r['input']['event']['official_game_id']) not in used
             and now<=moment(r['input']['event']['kickoff_at'])-timedelta(minutes=30)]
    notes=[]
    if pending:
        try:
            from research.kbo_forward import operating_vote_bridge as bridge
            config={'export_path':state/'operating-export.json','price_archive_root':state/'operating-price-evidence',
                    'm0_archive_root':recorder.root,'m0_price_archive_root':recorder.price_root}
            events=[{**r['input']['event'],'market_id':r['input']['market']['market_id']} for r in pending[:5]]
            if source_fn is not None:
                exported=source_fn(config,events,now)
            elif hasattr(bridge,'refresh_operating_export'):
                exported=bridge.refresh_operating_export(config,events,now)
            else:
                exported=bridge.load_operating_export(config,[e['official_game_id'] for e in events],now.isoformat())
            for cap in pending[:5]:
                notes.append(capture_pair(path,cap,exported,recorder.clock(),clock=recorder.clock))
        except Exception as exc:
            notes.append({'recorded':False,'status':'source_unavailable','reason':str(exc)[:200]})
    settle_pairs(path,model_rows,now)
    return {**summary(path),'pending_eligible_m0_captures':len(pending),'bridge_attempts':notes}
