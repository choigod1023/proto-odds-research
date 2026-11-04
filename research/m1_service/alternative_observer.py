"""Attach total-price research to an existing capture without additional HTTP.

Only the exact same official response as the frozen moneyline may contribute
offers. This module does not select bets, change forecasts or report model EV
as realized profit. Receipt authentication is the local collector's, not external.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

from research.kbo_forward.prices import DETAIL_URL, LIST_URL
from research.m1_service import alternative_prices
from research.m1_service.low_odds import review
from research.m1_service.observer import _append, canonical, digest, moment


def _body(record, root, now):
    sha = record['sha256']
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('invalid source hash')
    target = root/(sha+'.body')
    if (Path(record['body_file']).resolve()!=target.resolve()
            or not target.resolve().is_relative_to(root)
            or record.get('method')!='POST' or record.get('status')!=200
            or 'json' not in record.get('content_type','').lower()):
        raise ValueError('official price receipt/cache differs')
    raw=target.read_bytes()
    if digest(raw)!=sha or not moment(record['requested_at'])<=moment(record['received_at'])<=now:
        raise ValueError('price source bytes or receipt time differs')
    return raw


def attach(recorder, capture, request_records, now):
    """Record independently validated candidates for all three frozen models.

    The capture must be an unchanged record already present in recorder.path.
    Failure is isolated by the caller from the original collector and capture.
    """
    if capture is None or capture.get('type')!='capture' or not capture['roi_eligible']:
        return {'recorded':False,'reason':'no_priced_capture'}
    from research.m1_service.observer import read
    stored=[r for r in read(recorder.path) if r.get('hash')==capture.get('hash')]
    if len(stored)!=1 or stored[0]!=capture:
        raise ValueError('candidate review requires unchanged stored capture')
    payload=capture['input']; source=payload['evidence']['odds']; event=payload['event']
    if (now<moment(capture['captured_at']) or now>moment(event['kickoff_at'])-timedelta(minutes=30)
            or now-moment(source['received_at'])>timedelta(minutes=10)):
        raise ValueError('alternative review after cutoff or stale quote')
    root=Path(recorder.price_root).resolve(); records=list(request_records)
    successful=[r for r in records if r.get('url')==DETAIL_URL and r.get('status')==200 and not r.get('error_type') and 'json' in r.get('content_type','').lower()]
    for rec in records:
        if rec.get('url')!=DETAIL_URL or rec in successful: continue
        if not any(r.get('params')==rec.get('params') for r in successful):
            raise ValueError('unresolved_price_round_request')
    listing=[r for r in records if r.get('url')==LIST_URL and r.get('sha256')==source['round_listing_sha256']]
    details=[r for r in records if r.get('url')==DETAIL_URL and r.get('sha256')==source['raw_sha256']]
    if len(listing)!=1 or len(details)!=1:
        raise ValueError('same capture official price responses required')
    listing_raw=_body(listing[0],root,now); raw=_body(details[0],root,now)
    part=payload['market']['market_id'].split(':')
    if (len(part)!=3 or details[0].get('params',{}).get('gmId')!='G101'
            or str(details[0].get('params',{}).get('gmTs'))!=part[1]
            or details[0].get('params',{}).get('gameYear')!=str(event['season'])
            or moment(details[0]['received_at'])!=moment(source['received_at'])):
        raise ValueError('capture price round or receipt differs')
    rounds=[r for r in json.loads(listing_raw)['protoGames'] if r.get('gmId')=='G101' and str(r.get('gmTs'))==part[1]]
    if len(rounds)!=1 or rounds[0].get('gmOsidTsYear')!=event['season']:
        raise ValueError('unique matching listed round required')
    if moment(listing[0]['received_at'])>moment(details[0]['requested_at']):
        raise ValueError('detail predates listing')
    selected_evidence={'uri':DETAIL_URL,'sha256':source['raw_sha256'],'received_at':source['received_at'],
                       'round_listing_sha256':source['round_listing_sha256']}
    offers, excluded=alternative_prices.parse_detail(raw,rounds[0],[event],source['received_at'],selected_evidence,now.isoformat())
    # Inspect all collected rounds before accepting a unique fixture product.
    # Even a competing round that is unusable withholds candidates conservatively.
    matches=0
    for rec in successful:
        if rec is details[0]: continue
        other=json.loads(_body(rec,root,now)); terms=other['compSchedules']
        for item in terms['datas']:
            r=dict(zip(terms['keys'],item))
            if (r.get('leagueCode')=='BS001' and r.get('homeId')==event['home_id']
                    and r.get('awayId')==event['away_id']
                    and alternative_prices._millis(r['gameDate'])==moment(event['kickoff_at'])
                    and tuple(r.get(k) for k in ('betId','betTypId','betNm','betTypNm'))==alternative_prices.TOTAL):
                matches+=1
    if matches:
        offers=[];excluded.append({'reason':'multiple_round_candidates','count':matches})
    original=[{'offer_id':payload['market']['market_id']+':'+side,'fixture_id':payload['row']['fixture_id'],
               'period':'full_game','kind':payload['market']['kind'],'selection':side,
               'settlement_rule':payload['market']['settlement_rule'],'odds':odds}
              for side,odds in payload['market']['odds'].items()]
    for offer in offers:
        offer['official_game_id']=offer['fixture_id'];offer['fixture_id']=payload['row']['fixture_id']
    reviews={name:review(f,original+offers,payload['market']['market_id']+':'+capture['choices'][name])
             for name,f in capture['models'].items() if capture['choices'][name] is not None}
    content={'capture_hash':capture['hash'],'source_sha256':source['raw_sha256'],
             'offers':offers,'excluded':excluded,'reviews':reviews}
    key=digest(canonical(content))
    record={'type':'alternative_review','schema':'kbo-total-research-v1','review_key':key,
            **deepcopy(content),'automatic_replacement':None,
            'realized_roi':None,'production_policy_changed':False,'paper_wallet_changed':False,
            'timing_scope':'decision before append; durable write completion before cutoff not certified'}
    def make(rows):
        if any(r.get('type')=='alternative_review' and r.get('capture_hash')==capture['hash'] for r in rows): return None
        # Check after probability work and ledger reading/lock acquisition.
        completed=recorder.clock()
        closes=[moment(payload['market'][k]) for k in ('close_at','round_close_at')]
        closes += [moment(o[k]) for o in offers for k in ('close_at','round_close_at')]
        if (completed<now or completed>moment(event['kickoff_at'])-timedelta(minutes=30)
                or completed-moment(source['received_at'])>timedelta(minutes=10)
                or any(completed>=t for t in closes)):
            raise ValueError('alternative processing finished after cutoff or quote closure')
        record['reviewed_at']=completed.isoformat()
        return record
    written=_append(recorder.path,make)
    return {'recorded':written is not None,'offer_count':len(offers),'excluded':excluded}
