import collections, gzip, hashlib, json
from datetime import datetime
from pathlib import Path

ROOT = Path('C:/Users/user/Documents')
BASE = ROOT / 'Codex/pregame-compare/experiments/pregame-baseball-20261104'
VOTES = ROOT / 'ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/joined-standard-ot.json'
OUT = Path('experiments/matched-baseball-20261104')
CANDIDATES = VOTES.parent / 'korean/candidates.jsonl'
MODELS = ['kbo-actual-training-2026-M0_actual', 'kbo-actual-training-2026-M2_actual', 'kbo-player-effects-v2-2026-M0', 'kbo-player-effects-v2-2026-M1', 'kbo-player-effects-v2-2026-M2']

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dt(s):
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (AttributeError, ValueError): return None

def pick(p):
    if abs(p - .5) < 1e-12: return None
    return 'home' if p > .5 else 'away'

def settle(selection, winner, prices):
    if selection is None: return None
    return 0 if winner == 'draw' else prices[selection] - 1 if selection == winner else -1

def aggregate(rows):
    out = {}
    for arm in ['market', 'vote'] + MODELS + [m + ':9model1vote' for m in MODELS]:
        selections = [(r['selections'][arm], r['winner'], r['profit'][arm]) for r in rows]
        active = [x for x in selections if x[0] is not None]
        decided = [x for x in active if x[1] != 'draw']
        out[arm] = {'bets':len(active), 'decided':len(decided), 'wins':sum(x[0] == x[1] for x in decided), 'refunds':len(active)-len(decided), 'hit_rate_decided':sum(x[0] == x[1] for x in decided)/len(decided) if decided else None, 'net_units':round(sum(x[2] for x in active),6), 'roi':sum(x[2] for x in active)/len(active) if active else None}
    return out

def run():
    votes=json.loads(VOTES.read_text(encoding='utf8'))
    overlap=json.loads((BASE/'overlap.json').read_text(encoding='utf8'))
    with gzip.open(BASE/'model-predictions.jsonl.gz','rt',encoding='utf8') as f: predictions=[json.loads(s) for s in f]
    prices={r['fixture_id'][:-4]:r for r in overlap['2026']['rows']}
    models=collections.defaultdict(dict)
    for p in predictions:
        if p['payload']['model_id'] in MODELS:
            models[p['payload']['source_fixture_id']][p['payload']['model_id']]=p
    grouped=collections.defaultdict(list); reasons=collections.Counter(); source_rows=[]
    def reject(v, reason):
        reasons[reason]+=1
        source_rows.append({'reason':reason,'official_id':v.get('official_id'),'source_url':v.get('source_url'),'source_sha256':v.get('source_sha256'),'author':v.get('author'),'date':v.get('match_date'),'market':v.get('market'),'side':v.get('side')})
    for v in votes:
        if v.get('league') != 'KBO': continue
        reasons['kbo_source_rows']+=1
        if v.get('market') != 'moneyline': reject(v, 'not_moneyline'); continue
        fid=v.get('official_id')
        if not fid or not v.get('explicit_official_id') or v.get('result_evidence')!='official_exact_fixture': reject(v, 'no_exact_official_id_result'); continue
        if fid not in prices: reject(v, 'no_exact_price_fixture'); continue
        if set(models.get(fid,{})) != set(MODELS): reject(v, 'missing_common_model_revision'); continue
        pr=prices[fid]; pv=v.get('proto_odds')
        if pv is None or len(pv)!=2 or pv != [pr['prices']['home'],pr['prices']['away']]: reject(v, 'missing_or_different_price'); continue
        if v.get('period')!='full_game_including_overtime' or v.get('side') not in ('home','away') or v.get('within_article_conflicting_directions'): reject(v, 'market_or_selection_ambiguous'); continue
        if not v.get('author') or not v.get('source_sha256'): reject(v, 'missing_author_or_raw_sha'); continue
        score='draw' if v['home_score']==v['away_score'] else 'home' if v['home_score']>v['away_score'] else 'away'
        if score!=pr['winner']: reject(v, 'result_conflict'); continue
        kickoff=next(iter(models[fid].values()))['kickoff']; claim=dt(v.get('published_at_claim')); ko=dt(kickoff)
        if not claim or not ko or claim>=ko: reject(v, 'no_pre_kickoff_publication_claim'); continue
        grouped[fid].append(v); reasons['eligible_claim_rows_before_author_dedup']+=1
    rows=[]
    for fid, group in sorted(grouped.items()):
        by_author=collections.defaultdict(list)
        for v in group: by_author[(v['source'].lower(),v['author'].strip().lower())].append(v)
        retained=[]
        for author, entries in by_author.items():
            directions={x['side'] for x in entries}
            if len(directions)!=1: reasons['conflicting_author_excluded']+=1; continue
            retained.append(sorted(entries,key=lambda x:(x['published_at_claim'],x['source_url']))[0]); reasons['duplicate_author_rows']+=len(entries)-1
        if not retained: reasons['no_unambiguous_authors']+=1; continue
        vote=sum(v['side']=='home' for v in retained)/len(retained)
        if pick(vote) is None: reasons['tied_vote_games_excluded']+=1; continue
        pr=prices[fid]; probs={m:models[fid][m]['payload']['probabilities']['home'] for m in MODELS}; selections={'market':pick(1/pr['prices']['home']/(1/pr['prices']['home']+1/pr['prices']['away'])),'vote':pick(vote)}
        for m,p in probs.items(): selections[m]=pick(p); selections[m+':9model1vote']=pick(.9*p+.1*vote)
        kickoff=next(iter(models[fid].values()))['kickoff']
        row={'official_id':fid,'fixture_id':pr['fixture_id'],'kickoff':kickoff,'market_id':pr['market_id'],'prices':pr['prices'],'price_time_verified':False,'winner':pr['winner'],'official_result_url':retained[0]['result_source_url'],'scores':[retained[0]['home_score'],retained[0]['away_score']],'votes':[{'source':v['source'],'author':v['author'],'url':v['source_url'],'sha256':v['source_sha256'],'published_at_claim':v['published_at_claim'],'observed_at':v['observed_at'],'side':v['side'],'excerpt':v['evidence_excerpt'],'modified_at':'unknown','body_completed_at':'unknown','result_leakage_audit':'unknown; historical revision not preserved'} for v in retained],'vote_home_fraction':vote,'model_home_probabilities':probs,'model_artifact_sha256':{m:models[fid][m]['body_sha256'] for m in MODELS},'selections':selections,'profit':{a:settle(s,pr['winner'],pr['prices']) for a,s in selections.items()},'evidence_class':'source_publication_claim_plus_retrospective_lineup_oracle','strict_eligible':False,'strict_failure_reasons':['model_and_lineup_not_proven_received_pregame','original_vote_first_observed_after_game_or_unknown','price_receipt_time_unverified','publication_modification_history_unknown']}
        rows.append(row)
    cohorts={'all':aggregate(rows)}
    for month in sorted({r['kickoff'][:7] for r in rows}): cohorts[month]=aggregate([r for r in rows if r['kickoff'].startswith(month)])
    summary={'input_sha256':{str(p):sha(p) for p in [VOTES,BASE/'overlap.json',BASE/'model-predictions.jsonl.gz',BASE/'model-manifest.json',CANDIDATES]},'dependency':'#567 f76a7d3f read-only artifacts; not proof of point-in-time availability','counts':dict(reasons),'korean_candidate_kbo_rows':sum(json.loads(s).get('league')=='KBO' for s in CANDIDATES.read_text(encoding='utf8').splitlines()),'retained_games_first_observed_before_kickoff':sum(any(dt(v['observed_at']) and dt(v['observed_at']) < dt(r['kickoff']) for v in r['votes']) for r in rows),'single_author_games':sum(len(r['votes'])==1 for r in rows),'m0_disagreement_games':sum(r['selections'][MODELS[0]]!=r['selections'][MODELS[2]] for r in rows),'independent_games':len(rows),'price_rows':len({r['market_id'] for r in rows}),'model_revision_rows':len(rows)*len(MODELS),'strict_common_games':0,'cohorts':cohorts,'rules':'Exact official game ID and identical archived home/away prices; full-game moneyline only; one vote per source+author, conflicting authors excluded; tied vote excludes game; model/market/hybrid ties no bet; draws refund; 1 unit single bets; 9:1 means 0.9 model home probability + 0.1 author vote home fraction. ROI includes refunds in stake denominator; hit rate excludes refunds. Source-claim and retrospective lineup oracle only, not blind or real-time.'}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'excluded-source-rows.json').write_text(json.dumps(source_rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# KBO 원문 투표·선수모델 교집합 회고 진단','',f"독립 경기 {len(rows)}개, 가격행 {summary['price_rows']}개, 모델 revision-경기 {summary['model_revision_rows']}개. 엄격 사전 공통 경기 0개.",'','2026 실제 게시 주장과 사후 확정 명단 oracle을 결합한 진단이다. 원문 최초 관측·수정 이력, 선수명단 수신 및 배당 수신 시각이 경기 전임을 입증하지 못한다. 이미 본 기간이며 블라인드 검증이 아니다. 2025 원문 KBO 행은 이 입력에 없다. 단위 단폴 수익이며 월 손실 제한 원장이 아니다.','',summary['rules'],'','| 전략 | 베팅 N | 적중/확정 N | 적중률 | ROI | 순손익 단위 |','|---|---:|---:|---:|---:|---:|']
    for arm,v in cohorts['all'].items(): lines.append(f"| {arm} | {v['bets']} | {v['wins']}/{v['decided']} | {v['hit_rate_decided']:.2%} | {v['roi']:.2%} | {v['net_units']:.2f} |" if v['bets'] and v['decided'] else f'| {arm} | 0 | 0/0 | - | - | 0 |')
    lines+=['','원문 한국 후보 전체 294행이 joined 장부 294행과 일치한다. 197경기 중 190경기는 작성자 1명이며 진정한 다수 작성자 표결로 해석할 수 없다. 최초 관측이 경기 전인 원문은 0경기이고 수정·결과 누출 여부는 확인되지 않는다. 서로 다른 두 M0 선택은 16경기에서 다르다. 월별 동일 교집합과 사유별 행 수는 summary.json, 경기별 입력·선택·가격·결과·손익은 rows.json에 보존했다. actual-training M0와 projected-training M0는 별도 고정 아티팩트다. actual-training M1은 원본에 없다. 가격은 원문에 실린 당시 가격이 아니라 두 장부가 같은 회고 보관 가격임을 확인한 것이다. 출처의 게시 날짜는 사전 수신이나 수정 이력의 증거가 아니다.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps({'counts':summary['counts'],'games':len(rows),'overall':cohorts['all']},ensure_ascii=False,indent=2))

if __name__=='__main__': run()
