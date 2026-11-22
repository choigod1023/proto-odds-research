"""Collect up to three specified source candidates with claimed author names, stopping on 403/429."""
import base64,gzip,hashlib,json,re
from html.parser import HTMLParser
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

OUT=Path('experiments/matched-baseball-20261104/recovery-2025/human')
URLS=[('allstv24','https://allstv24.com/sports_analysis/15421?page=829'),('kboinsight','https://kboinsight.tistory.com/33'),('bepick','https://bepick.net/analysis/view/648739')]
def sha(x):return hashlib.sha256(x).hexdigest()
def fetch(key,url):
    raw=OUT/f'{key}.body.gz';meta=OUT/f'{key}.receipt.json'
    if meta.exists() and not raw.exists():
        r=json.loads(meta.read_text(encoding='utf8'))
        if r.get('http_status') is not None or r.get('url') != url: raise ValueError('incomplete cache')
        return r
    if raw.exists() or meta.exists():
        if not raw.exists() or not meta.exists():raise ValueError('incomplete cache')
        r=json.loads(meta.read_text(encoding='utf8'));b=gzip.decompress(raw.read_bytes())
        if sha(b)!=r['body_sha256'] or r['url']!=url:raise ValueError('cache mismatch')
        return r
    try:
        try:
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; research evidence collector)'}),timeout=15) as res:
                status=res.status; b=res.read();final=res.geturl();ctype=res.headers.get('Content-Type')
        except HTTPError as e:
            status=e.code;b=e.read();final=e.geturl();ctype=e.headers.get('Content-Type');e.close()
        completed=datetime.now(timezone.utc).isoformat()
    except (URLError,TimeoutError,OSError) as e:
        r={'url':url,'http_status':None,'error':str(e),'body_completed_at':None}
        meta.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return r
    r={'url':url,'final_url':final,'http_status':status,'body_completed_at':completed,'timestamp_basis':'http_response_body_completed','body_sha256':sha(b),'body_bytes':len(b),'content_type':ctype,'evidence_scope':'current local receipt; no historical publication or receipt proof'}
    raw.write_bytes(gzip.compress(b,mtime=0));meta.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return r

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.meta={};self.parts=[];self.links=[];self.depth=0;self.skip=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta':
            key=a.get('property') or a.get('name')
            if key and a.get('content'):self.meta[key]=a['content']
        if tag=='a' and a.get('href'):self.links.append(a['href'])
        if self.depth and tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'): self.depth+=1
        elif not self.depth and tag=='div' and 'tt_article_useless_p_margin' in a.get('class','').split():self.depth=1
        if self.depth and tag in ('script','style'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'): return
        if self.depth:
            if tag in ('script','style') and self.skip:self.skip-=1
            self.depth-=1
    def handle_data(self,data):
        if self.depth and not self.skip:self.parts.append(data)

def audit_join():
    meta=json.loads((OUT/'kboinsight.receipt.json').read_text(encoding='utf8'))
    if meta.get('http_status')!=200:return None
    raw=gzip.decompress((OUT/'kboinsight.body.gz').read_bytes())
    if sha(raw)!=meta['body_sha256']:raise ValueError('article hash mismatch')
    page=ArticleParser();page.feed(raw.decode('utf8'))
    text=' '.join(' '.join(page.parts).split())
    fixture='20250719LTLG02025';official='20250719LTLG0'
    source_key=any(f'/game/{fixture}/preview' in x for x in page.links) or f'/game/{fixture}/preview' in text
    conclusion=re.search(r'결론\s*:\s*(LG\s*승)\s*,\s*(8\.5\s*오버)',text)
    if not source_key or not conclusion or page.meta.get('og.article.author')!='KBOINSIGHT':raise ValueError('article identity or independent win pick not found')
    published=page.meta.get('article:published_time');modified=page.meta.get('article:modified_time')
    if not published:raise ValueError('publication claim absent')
    root=Path('C:/Users/user/Documents/Codex')
    directory=root/'kbt25/experiments/kbo-actual-lineups-2025-20261103'
    lineups=directory/'actual-lineups-2025.jsonl.gz';evidence=directory/'raw-evidence.jsonl.gz'
    with gzip.open(lineups,'rt',encoding='utf8') as f: matches=[json.loads(l) for l in f if fixture in l and json.loads(l).get('fixture_id')==fixture]
    if len(matches)!=1 or matches[0]['official_game_id']!=official or matches[0]['fixture_match_basis']!='exact_official_game_id':raise ValueError('official source ID bridge absent')
    schedulemeta=matches[0]['schedule_evidence']; h=schedulemeta['sha256']
    with gzip.open(evidence,'rt',encoding='utf8') as f: rawrows=[json.loads(l) for l in f if h in l and json.loads(l).get('sha256')==h]
    if len(rawrows)!=1:raise ValueError('official raw record missing')
    official_bytes=base64.b64decode(rawrows[0]['body_base64'],validate=True)
    if sha(official_bytes)!=h:raise ValueError('official schedule hash mismatch')
    games=[g for g in json.loads(official_bytes)['game'] if g.get('G_ID')==official]
    if len(games)!=1:raise ValueError('official fixture not unique')
    g=games[0]
    if (g['G_DT'],g['AWAY_ID'],g['HOME_ID'],g['GAME_STATE_SC'])!=('20250719','LT','LG','3'):raise ValueError('official fixture discrepancy')
    kickoff=f"2025-07-19T{g['G_TM']}:00+09:00"
    if datetime.fromisoformat(published)>=datetime.fromisoformat(kickoff):raise ValueError('publication claim not before kickoff')
    prices=root/'pregame-baseball/experiments/kbo-m0-followup-20261103/data/prices-2025.jsonl.gz'
    with gzip.open(prices,'rt',encoding='utf8') as f: price=[json.loads(l) for l in f if fixture in l and json.loads(l).get('fixture_id')==fixture]
    if len(price)!=1 or price[0]['result_evidence']['official_game_id']!=official:raise ValueError('price bridge absent')
    pr=price[0]['price_source']; odds=pr['prices']
    modelpath=root/'kbtmodel/experiments/kbo-actual-training-20261103/artifacts/development.json';model=json.loads(modelpath.read_text(encoding='utf8'))
    manifestpath=root/'pregame-compare/experiments/pregame-baseball-20261104/model-manifest.json'
    manifest=json.loads(manifestpath.read_text(encoding='utf8'))
    model_manifest={x['model_id'].split('-')[-1]:x for x in manifest['models'] if x['model_id'] in ('kbo-actual-training-2025-M0_actual','kbo-actual-training-2025-M2_actual')}
    if set(model_manifest)!=set(('M0_actual','M2_actual')):raise ValueError('model manifest missing')
    probs={}
    for name in ('M0_actual','M2_actual'):
        arr=[x for x in model['predictions']['actual'][name] if x['fixture_id']==fixture]
        if len(arr)!=1:raise ValueError('model fixture missing or duplicate')
        probs[name]=arr[0]['probabilities_2way']
    winner='home' if int(g['B_SCORE_CN'])>int(g['T_SCORE_CN']) else 'away' if int(g['T_SCORE_CN'])>int(g['B_SCORE_CN']) else 'draw'
    if winner!=price[0]['winner']:raise ValueError('official winner differs from price ledger')
    modelpick={m:'home' if p['home']>p['away'] else 'away' if p['away']>p['home'] else None for m,p in probs.items()}
    inv=(1/odds['home'])/((1/odds['home'])+(1/odds['away']))
    selections={'named_author_source':'home','market':'home' if inv>.5 else 'away' if inv<.5 else None,'market90_source10_multiplicative':'home' if .9*inv+.1>.5 else 'away' if .9*inv+.1<.5 else None,**modelpick}
    profit={k:None if v is None else 0 if winner=='draw' else odds[v]-1 if v==winner else -1 for k,v in selections.items()}
    rec={'fixture_id':fixture,'official_game_id':official,'kickoff_kst':kickoff,'official_schedule_time_basis':'retrospective official schedule, historical receipt unproven','home':'LG','away':'롯데','home_score':int(g['B_SCORE_CN']),'away_score':int(g['T_SCORE_CN']),'official_winner':winner,'source_url':meta['url'],'source_author_claim':page.meta['og.article.author'],'source_author_identity_verification':'handle on page only; human identity not independently verified','source_fixture_evidence':f'https://m.sports.naver.com/game/{fixture}/preview','source_publication_claim':published,'source_modification_claim':modified,'source_observed_body_completed_at':meta['body_completed_at'],'source_body_sha256':meta['body_sha256'],'source_full_conclusion_excerpt':conclusion.group(0),'source_win_pick_excerpt':conclusion.group(1),'separate_total_pick_excluded':conclusion.group(2),'source_pick_period_assumption':'plain team win interpreted full game; overtime not explicitly established','source_timing_class':'historical publication claim only, first observed postgame; not strict','result_leakage_audit':'unknown; current page cannot establish historical text or modification history','official_schedule_sha256':h,'official_schedule_received_at':schedulemeta['received_at'],'market_id':pr['market_id'],'prices':odds,'price_source':pr,'model_probabilities':probs,'model_identity':{m:{'model_id':model_manifest[m]['model_id'],'coefficient_sha256':model_manifest[m]['coefficient_sha256'],'artifact_sha256':model_manifest[m]['source_artifact_sha256']} for m in probs},'selections':selections,'profit_units':profit,'strict_eligible':False,'strict_failures':['no pregame original receipt','model lineup retrospective oracle','historical price receipt not proven','source revision history unproven']}
    summary={'matched_games':1,'distinct_claimed_author_handles':1,'human_identity_independently_verified':False,'strict_games':0,'matched_price_rows':1,'model_revision_rows':2,'metrics':{k:{'bets':1 if v is not None else 0,'wins':1 if v==winner and winner!='draw' else 0,'hit_rate_decided':1 if v==winner and winner!='draw' else 0 if v is not None and winner!='draw' else None,'net_units':profit[k],'roi':profit[k]} for k,v in selections.items()},'input_sha256':{str(p):sha(p.read_bytes()) for p in (lineups,evidence,prices,modelpath,manifestpath)},'interpretation':'Retrospective historical publication claim and postgame lineup oracle, not a blind test or strict pregame; N=1.'}
    (OUT/'official-20250719.body.gz').write_bytes(gzip.compress(official_bytes,mtime=0))
    receipt={k:v for k,v in rawrows[0].items() if k!='body_base64'}
    (OUT/'official-20250719.receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'joined-2025.json').write_text(json.dumps({'summary':summary,'rows':[rec]},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    report=['# 2025 KBO 작성자명이 표시된 원문: 회고 연결','', '2025-07-19 롯데–LG 1경기. 작성자명 KBOINSIGHT가 표시된 원문은 `결론: LG 승, 8.5 오버`를 별도 승리·합계 선택으로 제시한다. 이 비교에는 LG 승만 사용하고 합계 선택은 사용하지 않았다. 원문에 표기된 네이버 경기 키를 기존 정확 공식 ID 연결과 대조했고 공식 일정 원문을 SHA 검증했다. 일반 승리 픽을 전체 경기로 가정하며 연장 포함 여부는 원문에서 증명되지 않는다.','',f"공식 결과 롯데 {g['T_SCORE_CN']}–LG {g['B_SCORE_CN']}, 원정 승. 같은 보관 가격 홈 {odds['home']}, 원정 {odds['away']}. 원문·시장·시장90/원문10·M0_actual·M2_actual 모두 홈을 선택해 각각 0/1, ROI -100%, 순손익 -1단위다. 작성자명은 페이지의 표시이며 독립적인 신원 또는 사람 여부 확인은 없다. 단일 사례이므로 성과 일반화는 불가하다.",'',f"게시 시각 주장 {published}, 수정 시각 주장 {modified}, 첫 본문 수신 {meta['body_completed_at']}. 게시·수정 주장은 실제 사전 수신 증명이 아니며 당시 본문·결과 누출 여부는 알 수 없다. 명단은 사후 oracle, 배당의 실제 당시 수신도 확인되지 않아 strict 표본은 0이다. 다른 후보 2개는 연결이 끊겨 원문을 받지 못했다.",'', '입력 SHA, 원문 발췌, 공식 ID·경기 시각·가격·선택·정산은 joined-2025.json과 원문·영수증에 보존했다. 기존 2026년 103경기는 건드리지 않았다.','']
    (OUT/'REPORT.md').write_text('\n'.join(report),encoding='utf8')
    return summary

def run():
    OUT.mkdir(parents=True,exist_ok=True);records=[]
    for key,url in URLS:
        r=fetch(key,url);records.append({'key':key,**r})
        if r.get('http_status') in (403,429):break
    summary={'max_new_http_requests':len(URLS),'request_or_cached_count':len(records),'records':records,'stopped_on_restriction':bool(records and records[-1].get('http_status') in (403,429)),'unattempted':len(URLS)-len(records)}
    (OUT/'retrieval-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    if (OUT/'kboinsight.receipt.json').exists() and json.loads((OUT/'kboinsight.receipt.json').read_text(encoding='utf8')).get('http_status')==200:summary['joined']=audit_join()
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
