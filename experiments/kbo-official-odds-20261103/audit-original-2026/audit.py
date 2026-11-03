"""Read-only correspondence audit: never choose or replace either price."""
import gzip
import json
from collections import Counter
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path
from collect_audit import ROOT,sha

KST=timezone(timedelta(hours=9))
TEAM={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}

def classify(target,records,ts):
    try:match_seq=int(ts)
    except (TypeError,ValueError):return 'identity_mismatch','invalid_match_seq',None
    matches=[r for r in records if str(r.get('matchSeq','')).isdigit() and int(r['matchSeq'])==match_seq]
    if not matches:return 'not_found','match_seq_missing',None
    if len(matches)!=1:return 'identity_mismatch','duplicate_match_seq',None
    r=matches[0]
    if r.get('leagueCode')!='BS001' or r.get('homeId')!=TEAM.get(target['home_id']) or r.get('awayId')!=TEAM.get(target['away_id']):
        return 'identity_mismatch','league_or_team',r
    try: start=datetime.fromtimestamp(r['gameDate']/1000,KST)
    except (KeyError,ValueError,TypeError,OverflowError):return 'identity_mismatch','invalid_game_date',r
    if not target.get('kickoff_at') or start!=datetime.fromisoformat(target['kickoff_at']):return 'identity_mismatch','kickoff',r
    modern=r.get('betId')=='2' and r.get('betTypId')=='2'
    legacy=r.get('betId') is None and r.get('betTypId') is None and r.get('handi')==0
    if not (modern or legacy) or r.get('drawAllot') not in (0,0.0) or r.get('gameSubject') not in (None,''):
        return 'product_mismatch','not_full_game_2way',r
    if r.get('gameReject')!='0':return 'settlement_or_sales_status','game_rejected',r
    hg,ag=target['score']; code='0' if hg>ag else '2' if hg<ag else '4'
    if r.get('mchScore')!=f'{hg}:{ag}' or r.get('gameResult')!=code:
        return 'result_mismatch','score_or_result_code',r
    try:
        current=(Decimal(str(r['winAllot'])),Decimal(str(r['loseAllot'])))
        original=(Decimal(str(target['original_prices']['home'])),Decimal(str(target['original_prices']['away'])))
        if any(not p.is_finite() or p<=1 for p in current+original):return 'settlement_or_sales_status','invalid_or_refund_price',r
    except (KeyError,InvalidOperation):return 'settlement_or_sales_status','missing_price',r
    return ('equal' if current==original else 'changed'),None,r

def main():
    targets=[json.loads(x) for x in gzip.decompress((ROOT/'audit-targets.jsonl.gz').read_bytes()).splitlines()]
    ledger=json.loads((ROOT/'request-ledger.json').read_bytes());snapshots={}
    for r in ledger:
        if r.get('http_status')!=200:continue
        raw=gzip.decompress((ROOT/r['body_file']).read_bytes())
        if sha(raw)!=r['body_sha256']:raise ValueError('response hash mismatch')
        data=json.loads(raw);cs=data['compSchedules'];snapshots[r['name']]=(r,data,[dict(zip(cs['keys'],row)) for row in cs['datas']])
    output=[]
    for t in targets:
        _,year,round_no,number,kind=t['market_id'].split(':')
        if year!='2026' or kind!='full_game_2way':raise ValueError('unexpected input market')
        name=f'detail-{year}-{int(round_no)}';snap=snapshots.get(name)
        base=t|{'status':None,'reason':None,'official_prices':None,'official_result':None,'official_market':None,
                'official_received_at':None,'official_price_observed_at':None,'response_sha256':None,'response_file':None}
        if snap is None:base.update(status='not_found',reason='round_response_missing')
        else:
            rec,data,rows=snap;expected=int(f'{int(year)%100:02d}{int(round_no):04d}')
            if data.get('gmTs')!=expected:raise ValueError('incorrect official round '+name)
            status,reason,r=classify(t,rows,number);base.update(status=status,reason=reason,official_received_at=rec['received_at'],response_sha256=rec['body_sha256'],response_file=rec['body_file'])
            if r is not None:
                base.update(official_prices={'home':r.get('winAllot'),'away':r.get('loseAllot')},
                  official_result={'score':r.get('mchScore'),'gameResult':r.get('gameResult'),'gameReject':r.get('gameReject'),'buyReject':r.get('buyReject')},
                  official_market={k:r.get(k) for k in ('matchSeq','leagueCode','homeId','awayId','gameDate','betId','betTypId','handi','drawAllot','gameSubject')})
        output.append(base)
    if len(output)!=354 or len({x['fixture_id'] for x in output})!=354:raise ValueError('target must be 354 unique fixtures')
    raw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in output).encode('utf8');out=ROOT/'audit-original-2026-prices.jsonl.gz';out.write_bytes(gzip.compress(raw,mtime=0))
    counts=dict(Counter(x['status'] for x in output));manifest={'status':'complete' if len(snapshots)==38 else 'incomplete_missing_official_response',
      'test_fixture_count':726,'original_price_target_count':354,'official_rounds_required':38,'official_rounds_received':len(snapshots),
      'new_requests':sum(x.get('origin')=='new_request' for x in ledger),'request_cap':30,'counts':counts,
      'official_selection_restriction_count':sum(x['official_result'] is not None and x['official_result']['buyReject']!='0' for x in output),
      'original_observation_time_verified':False,'official_price_observation_time_verified':False,
      'sources_sha256':sha((ROOT/'audit-sources.json').read_bytes()),'targets_sha256':sha((ROOT/'audit-targets.jsonl.gz').read_bytes()),
      'test_ids_sha256':sha((ROOT/'test-fixture-ids.json.gz').read_bytes()),'ledger_sha256':sha((ROOT/'request-ledger.json').read_bytes()),
      'audit_gzip_sha256':sha(out.read_bytes()),'audit_uncompressed_sha256':sha(raw),
      'limitations':'Official closed-round fields observed now; changed values may reflect revisions or source errors, not verified T-30 prices.'}
    (ROOT/'audit-manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
