"""Bounded, cached retrieval of 2025 BetClan search candidate pages.

One request per page, no retries; stop on HTTP 403/429. Receipt time is recorded
only after the entire response body is read. Cached raw bodies are immutable.
"""
import base64
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OUT = Path('experiments/matched-baseball-20261104/recovery-2025')
PAGES = [
 ('178033937','NC-Dinos-vs-Kia-Tigers'),
 ('178033958','Kiwoom-Heroes-vs-Samsung-Lions'),
 ('178033952','Hanwha-Eagles-vs-KT-Wiz'),
 ('178033947','Doosan-Bears-vs-SSG-Landers'),
 ('178033942','Lotte-Giants-vs-LG-Twins'),
]

def digest(body): return hashlib.sha256(body).hexdigest()
def url_for(page_id, slug):
    return f'https://www.betclan.com/baseball/predictionsdetails/{page_id}/{slug}-prediction-h2h-tip-and-match-preview/'

def fetch_one(page_id, slug):
    url=url_for(page_id,slug)
    meta=OUT/f'{page_id}.receipt.json'; raw=OUT/f'{page_id}.body.gz'
    if meta.exists() or raw.exists():
        if not (meta.exists() and raw.exists()): raise ValueError('incomplete cache; refusing overwrite')
        receipt=json.loads(meta.read_text(encoding='utf8'))
        body=gzip.decompress(raw.read_bytes())
        if receipt['url'] != url or digest(body)!=receipt['body_sha256']: raise ValueError('cached receipt mismatch')
        return receipt
    req=Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; research evidence collector)'})
    try:
        try:
            with urlopen(req,timeout=15) as response:
                status=response.status; body=response.read(); final_url=response.geturl(); content_type=response.headers.get('Content-Type')
        except HTTPError as e:
            status=e.code; body=e.read(); final_url=e.geturl(); content_type=e.headers.get('Content-Type'); e.close()
        completed=datetime.now(timezone.utc).isoformat()
    except (URLError,TimeoutError,OSError) as e:
        return {'url':url,'error':str(e),'body_completed_at':None,'http_status':None}
    receipt={'url':url,'final_url':final_url,'http_status':status,'body_completed_at':completed,'timestamp_basis':'http_response_body_completed','body_sha256':digest(body),'body_bytes':len(body),'content_type':content_type,'capture_scope':'local receipt; not evidence of historical pregame publication or receipt'}
    raw.write_bytes(gzip.compress(body,mtime=0));meta.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return receipt

def audit_official_schedule():
    """Audit the archived official schedule and the available exact-ID ledger.

    This does not match any URL to a fixture: a 404 body has no page date,
    timezone, fixture identity, winner section, or FAQ to validate.
    """
    root=Path('C:/Users/user/Documents/Codex')
    base=root/'kbt25/experiments/kbo-actual-lineups-2025-20261103'
    rawfile=base/'raw-evidence.jsonl.gz'
    expected='21c23896293e923d216f305504acb0979b7f91cbf47797fe4d6bfc4de0b5c6d6'
    archive=[]
    with gzip.open(rawfile,'rt',encoding='utf8') as f:
        for line in f:
            x=json.loads(line)
            if x.get('sha256')==expected: archive.append(x)
    if len(archive)!=1: raise ValueError('official schedule response is not unique')
    record=archive[0];body=base64.b64decode(record['body_base64'],validate=True)
    if digest(body)!=expected: raise ValueError('official schedule hash mismatch')
    games=json.loads(body)['game']
    overlap=root/'pregame-compare/experiments/pregame-baseball-20261104/overlap.json'
    rows=json.loads(overlap.read_text(encoding='utf8'))['2025']['rows']
    priceindex={r['fixture_id']:r for r in rows}
    predictions=root/'kbtmodel/experiments/kbo-actual-training-20261103/artifacts/development.json'
    artifact=json.loads(predictions.read_text(encoding='utf8'))
    model_ids=('M0_actual','M2_actual')
    models={m:{r['fixture_id']:r for r in artifact['predictions']['actual'][m]} for m in model_ids}
    schedule=[]; completed=[]
    for g in games:
        gid=g['G_ID'];fid=gid+'2025';pr=priceindex.get(fid)
        item={'official_game_id':gid,'date_kst':g['G_DT'],'scheduled_time_kst':g.get('G_TM'),'away_team_id':g['AWAY_ID'],'home_team_id':g['HOME_ID'],'away_team':g['AWAY_NM'],'home_team':g['HOME_NM'],'official_status':g['CANCEL_SC_NM'],'official_game_state':g['GAME_STATE_SC'],'away_score':g['T_SCORE_CN'],'home_score':g['B_SCORE_CN'],'publisher_page_fixture_identity':'unverified; response 404','publisher_page_timezone':'unavailable','publisher_winner':'unavailable','publisher_faq_contradiction':'not inspectable'}
        schedule.append(item)
        if not pr or any(fid not in models[m] for m in model_ids): continue
        winner='home' if int(g['B_SCORE_CN'])>int(g['T_SCORE_CN']) else 'away' if int(g['B_SCORE_CN'])<int(g['T_SCORE_CN']) else 'draw'
        if g['GAME_STATE_SC']!='3' or pr['winner']!=winner: raise ValueError('official result mismatch')
        probs={m:models[m][fid]['probabilities_2way'] for m in model_ids}
        picks={m:('home' if v['home']>v['away'] else 'away' if v['away']>v['home'] else None) for m,v in probs.items()}
        picks['market']='home' if pr['prices']['home']<pr['prices']['away'] else 'away' if pr['prices']['away']<pr['prices']['home'] else None
        profit={k:(None if v is None else 0 if winner=='draw' else pr['prices'][v]-1 if v==winner else -1) for k,v in picks.items()}
        completed.append({'fixture_id':fid,'official_game_id':gid,'scheduled_time_kst':g['G_TM'],'away_score':int(g['T_SCORE_CN']),'home_score':int(g['B_SCORE_CN']),'official_winner':winner,'market_id':pr['market_id'],'prices':pr['prices'],'model_probabilities':probs,'selections':picks,'profit_units':profit,'publisher_prediction':None,'comparison_scope':'official game + historical archived price + retrospective model diagnostic only; no publisher fixture link'})
    audit={'official_schedule_source_sha256':expected,'official_schedule_received_at_recorded':record['received_at'],'official_schedule_receipt_basis':'legacy; response-body completion basis unknown','official_date':'2025-07-18 KST','official_games':len(schedule),'completed_with_exact_id_price_and_both_models':len(completed),'publisher_page_official_identity_proven':0,'full_joined_publisher_games':0,'blockers':['All five publisher URLs returned HTTP 404 and identical error bodies.','No publisher winner section, source fixture date/time/timezone, or FAQ available from captured bodies.','URL team order is not evidence of home or away or date.','Official source/lineup and price archives were observed retrospectively; receipt time of historical price unknown.'],'input_sha256':{str(p):digest(p.read_bytes()) for p in (rawfile,overlap,predictions)},'schedule':schedule,'partial_model_price_result_rows':completed}
    (OUT/'official-schedule.body.gz').write_bytes(gzip.compress(body,mtime=0))
    meta={k:v for k,v in record.items() if k!='body_base64'}
    (OUT/'official-schedule.receipt.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'official-2025-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# 2025 KBO BetClan 원문 복구 시도','', '검색 후보 5개 URL은 모두 HTTP 404였다. 실제 본문·원문 승자 섹션·페이지의 경기 날짜와 시간대·FAQ를 확인할 수 없어 게시 예측과 공식 경기의 동일성을 입증하지 못했다. 사이트 예측은 사람 투표로 취급하지 않는다. 2025 외부 예측 + 같은 공식 경기·가격 + M0_actual/M2_actual 완전 교집합은 0경기이며 적중률과 ROI는 계산하지 않는다.','', '기존 공식 일정 원문 SHA를 검증해 2025-07-18 KST 5경기 중 3경기 취소와 2경기 종료를 확인했다. 종료 2경기는 기존 보관 가격·두 모델과 공식 ID로 연결되지만, 해당 페이지 URL이 이 경기들을 지칭한다는 증거가 없으므로 아래는 외부 예측 비교가 아닌 부분 진단이다. 가격의 당시 수신과 명단의 사전 수신은 입증되지 않았다.','', '| 공식 경기 ID | 원정 점수 | 홈 점수 | 모델 M0/M2 선택 | 시장 선택 |','|---|---:|---:|---|---|']
    for r in completed: lines.append(f"| {r['official_game_id']} | {r['away_score']} | {r['home_score']} | {r['selections']['M0_actual']}/{r['selections']['M2_actual']} | {r['selections']['market']} |")
    lines+=['','한화 주소의 최초 오타 Hanhwa 요청 404도 별도 이름으로 보존했고, 정확한 Hanwha 주소는 별도 1회 요청해 404를 확인했다. 실제 BetClan 요청은 총 6회다. HTTP 본문, 응답 완료 시각·SHA, 공식 일정 원문 및 원래 수신 메타데이터는 이 디렉터리에 있다. HTTP 실패를 우회하거나 누락된 예측을 생성하지 않았다. 기존 2026년 103경기 코호트는 변경하지 않았다.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf8')
    return audit

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    receipts=[]
    for page_id,slug in PAGES:
        r=fetch_one(page_id,slug);receipts.append(r)
        if r.get('http_status') in (403,429) or r.get('http_status') is None: break
    summary={'requested_or_cached':len(receipts),'http_statuses':[r.get('http_status') for r in receipts],'stopped_on_restriction':bool(receipts and receipts[-1].get('http_status') in (403,429)),'not_attempted':len(PAGES)-len(receipts),'receipts':receipts}
    (OUT/'retrieval-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    audit=audit_official_schedule()
    summary['official_games']=audit['official_games']
    summary['official_price_model_games']=audit['completed_with_exact_id_price_and_both_models']
    summary['publisher_joined_games']=audit['full_joined_publisher_games']
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': run()
