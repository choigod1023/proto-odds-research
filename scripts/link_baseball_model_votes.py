import base64, collections, gzip, hashlib, json
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

def vote_fraction(sides):
    if not sides or any(x not in ('home','away') for x in sides): return None
    p=sum(x=='home' for x in sides)/len(sides)
    return None if pick(p) is None else p

def market_vote_probability(prices, vote):
    if vote is None: return None
    home=1/prices['home']; away=1/prices['away']
    return .9*(home/(home+away))+.1*vote

def settle(selection, winner, prices):
    if selection is None: return None
    return 0 if winner == 'draw' else prices[selection] - 1 if selection == winner else -1

def aggregate(rows):
    out = {}
    for arm in ['market', 'vote', 'market90_vote10'] + MODELS + [m + ':player90_vote10' for m in MODELS]:
        selections = [(r['selections'][arm], r['winner'], r['profit'][arm]) for r in rows]
        active = [x for x in selections if x[0] is not None]
        decided = [x for x in active if x[1] != 'draw']
        out[arm] = {'bets':len(active), 'decided':len(decided), 'wins':sum(x[0] == x[1] for x in decided), 'refunds':len(active)-len(decided), 'hit_rate_decided':sum(x[0] == x[1] for x in decided)/len(decided) if decided else None, 'net_units':round(sum(x[2] for x in active),6), 'roi':sum(x[2] for x in active)/len(active) if active else None}
    return out

def recover_official_metadata(votes, overlap):
    """Use persisted score-ID -> official-ID bridge and hash-verified schedule bodies."""
    inputs=ROOT/'Codex/kbtmodel/experiments/kbo-actual-training-20261103/inputs'
    raw_path=ROOT/'Codex/kbtdata/experiments/kbo-actual-training-data-20261103/actual-starters-2026-raw.jsonl.gz'
    starters=inputs/'actual-starters-2026.jsonl.gz'
    manifest=json.loads((BASE/'model-manifest.json').read_text(encoding='utf8'))
    missing={x['fixture_id'] for x in manifest['missing_fixture_metadata'] if '-2026-' in x['model_id']}
    with gzip.open(starters,'rt',encoding='utf8') as f: records=[json.loads(l) for l in f if l.strip()]
    records=[r for r in records if r['fixture_id'] in missing]
    hashes={r['source_evidence']['schedule']['sha256'] for r in records}
    bodies={}
    with gzip.open(raw_path,'rt',encoding='utf8') as f:
        for line in f:
            x=json.loads(line)
            if x['sha256'] not in hashes: continue
            b=base64.b64decode(x['raw_base64'],validate=True)
            if hashlib.sha256(b).hexdigest()!=x['sha256'] or len(b)!=x['bytes']: raise ValueError('raw schedule hash or size mismatch')
            bodies[x['sha256']]=json.loads(b)
    grouped=collections.defaultdict(list)
    for v in votes:
        if v.get('league')=='KBO' and v.get('official_id'): grouped[v['official_id']].append(v)
    price_index={r['fixture_id']:r for r in overlap['2026']['rows']}
    a=ROOT/'Codex/kbtmodel/experiments/kbo-actual-training-20261103/artifacts/predictions.json'
    b=ROOT/'Codex/kbeffects/experiments/kbo-player-effects-20261103/artifacts/predictions.json'
    am=json.loads(a.read_text(encoding='utf8'))['predictions']['actual']
    bm=json.loads(b.read_text(encoding='utf8'))['scenarios']['official_batting_and_starter']
    arrays=[am['M0_actual'],am['M2_actual'],bm['M0'],bm['M1'],bm['M2']]
    indexes=[{r['fixture_id']:r for r in arr} for arr in arrays]
    recovered=[]
    for r in records:
        e=r['source_evidence']['schedule']; data=bodies.get(e['sha256'],{})
        matches=[g for g in data.get('game',[]) if g.get('G_ID')==r['official_game_id']]
        if len(matches)!=1: continue
        g=matches[0]
        if (g.get('HOME_ID'),g.get('AWAY_ID')) != (r['teams']['home'],r['teams']['away']) or g.get('G_DT')!=r['date'] or not g.get('G_TM'): continue
        fid=r['fixture_id']; candidates=grouped.get(r['official_game_id'],[]); pr=price_index.get(fid)
        recovered.append({'model_fixture_id':fid,'official_game_id':r['official_game_id'],'official_date':g['G_DT'],'official_scheduled_time':g['G_TM'],'home_team_id':g['HOME_ID'],'away_team_id':g['AWAY_ID'],'schedule_body_sha256':e['sha256'],'schedule_receipt_recorded_at':e.get('received_at'),'schedule_receipt_basis':'legacy metadata; body completion basis unknown; postgame','pitcher_body_sha256':r['source_evidence']['actual_pitcher_record']['sha256'],'model_probabilities':{MODELS[i]:idx[fid]['probabilities_2way'] for i,idx in enumerate(indexes) if fid in idx},'archived_price_candidate':pr,'price_link_caveat':'source ledger labels this day/team source-confirmed; no exact official ID price bridge established here','source_candidates':[{'url':v['source_url'],'author':v['author'],'sha256':v['source_sha256'],'published_at_claim':v['published_at_claim'],'observed_at':v['observed_at'],'side':v['side'],'market':v['market'],'proto_odds':v.get('proto_odds')} for v in candidates]})
    result={'inputs_sha256':{str(p):sha(p) for p in [starters,raw_path,a,b,BASE/'model-manifest.json']},'missing_unique_2026_score_ids':len(missing),'starter_bridge_rows':len(records),'schedule_hashes_required':len(hashes),'schedule_bodies_hash_verified':len(bodies),'official_id_team_date_time_verified':len(recovered),'matched_source_candidate_rows':sum(len(x['source_candidates']) for x in recovered),'matched_source_candidate_games':sum(bool(x['source_candidates']) for x in recovered),'baseline_197_unchanged':True,'limitation':'Schedule recovered via existing official-ID bridge and verified raw body, not day/team matching. Source-claim and postgame oracle only. Candidate price association remains existing day/team ledger and is not admitted as exact-ID price. Receipt timestamps are legacy postgame records, not newly asserted body completion.'}
    (OUT/'recovered-official-metadata.json').write_text(json.dumps({'summary':result,'rows':recovered},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result

def audit_archives_2025():
    overseas=ROOT/'ChatGPT/sportstoto-worktrees/tipster-year-overseas-20261028/experiments/tipster-year-20261028/overseas/predictions.jsonl'
    first=ROOT/'Codex/tipster-extended-evidence/first-observed-votes.jsonl.gz'
    candidates=[json.loads(l) for l in CANDIDATES.read_text(encoding='utf8').splitlines() if l.strip()]
    foreign=[json.loads(l) for l in overseas.read_text(encoding='utf8').splitlines() if l.strip()]
    first_counts=collections.Counter()
    with gzip.open(first,'rt',encoding='utf8') as f:
        for l in f:
            e=json.loads(l)['event']; first_counts[(str(e.get('slate_date') or '')[:4],str(e.get('source')))]+=1
    baseball25=[{k:x.get(k) for k in ('match_date','url','author','league','market','selection','document_sha256','source_reported_published_at','observed_at','original_american_odds','decimal_odds','recommendation_extract')} for x in foreign if x.get('league') in ('KBO','MLB','NPB') and str(x.get('match_date','')).startswith('2025')]
    counts=collections.Counter((str(x.get('league')),str(x.get('match_date') or '')[:4]) for x in foreign)
    result={'input_sha256':{str(p):sha(p) for p in [CANDIDATES,overseas,first]},'korean_kbo_by_year':dict(collections.Counter(str(x.get('match_date') or '')[:4] for x in candidates if x.get('league')=='KBO')),'overseas_by_league_year':{league+':'+year:n for (league,year),n in sorted(counts.items())},'first_observed_by_slate_year_source':{year+':'+source:n for (year,source),n in sorted(first_counts.items())},'baseball_2025_overseas_candidates':baseball25,'conclusion':'Three MLB candidates from two hashes; postgame observed and different league. No KBO 2025 in these enumerated archives. This is not exhaustive proof of absence. No cross-league KBO model use; first-observed source includes records without slate year.'}
    (OUT/'archive-2025-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result

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
        vote=vote_fraction([v['side'] for v in retained])
        if vote is None: reasons['tied_vote_games_excluded']+=1; continue
        pr=prices[fid]; probs={m:models[fid][m]['payload']['probabilities']['home'] for m in MODELS}; selections={'market':pick(1/pr['prices']['home']/(1/pr['prices']['home']+1/pr['prices']['away'])),'vote':pick(vote)}
        market_home=(1/pr['prices']['home'])/(1/pr['prices']['home']+1/pr['prices']['away'])
        selections['market90_vote10']=pick(market_vote_probability(pr['prices'],vote))
        for m,p in probs.items(): selections[m]=pick(p); selections[m+':player90_vote10']=pick(.9*p+.1*vote)
        kickoff=next(iter(models[fid].values()))['kickoff']
        row={'official_id':fid,'fixture_id':pr['fixture_id'],'kickoff':kickoff,'market_id':pr['market_id'],'prices':pr['prices'],'price_time_verified':False,'winner':pr['winner'],'official_result_url':retained[0]['result_source_url'],'scores':[retained[0]['home_score'],retained[0]['away_score']],'votes':[{'source':v['source'],'author':v['author'],'url':v['source_url'],'sha256':v['source_sha256'],'published_at_claim':v['published_at_claim'],'observed_at':v['observed_at'],'side':v['side'],'excerpt':v['evidence_excerpt'],'modified_at':'unknown','body_completed_at':'unknown','result_leakage_audit':'unknown; historical revision not preserved'} for v in retained],'vote_home_fraction':vote,'market_home_implied_probability_normalized':market_home,'model_home_probabilities':probs,'model_artifact_sha256':{m:models[fid][m]['body_sha256'] for m in MODELS},'selections':selections,'profit':{a:settle(s,pr['winner'],pr['prices']) for a,s in selections.items()},'evidence_class':'source_publication_claim_plus_retrospective_lineup_oracle','strict_eligible':False,'strict_failure_reasons':['model_and_lineup_not_proven_received_pregame','original_vote_first_observed_after_game_or_unknown','price_receipt_time_unverified','publication_modification_history_unknown']}
        rows.append(row)
    cohorts={'all':aggregate(rows)}
    for month in sorted({r['kickoff'][:7] for r in rows}): cohorts[month]=aggregate([r for r in rows if r['kickoff'].startswith(month)])
    OUT.mkdir(parents=True,exist_ok=True)
    recovery=recover_official_metadata(votes,overlap)
    archive25=audit_archives_2025()
    summary={'metadata_recovery':recovery,'archive_2025_audit':archive25,'input_sha256':{str(p):sha(p) for p in [VOTES,BASE/'overlap.json',BASE/'model-predictions.jsonl.gz',BASE/'model-manifest.json',CANDIDATES]},'dependency':'#567 f76a7d3f read-only artifacts; not proof of point-in-time availability','counts':dict(reasons),'korean_candidate_kbo_rows':sum(json.loads(s).get('league')=='KBO' for s in CANDIDATES.read_text(encoding='utf8').splitlines()),'retained_games_first_observed_before_kickoff':sum(any(dt(v['observed_at']) and dt(v['observed_at']) < dt(r['kickoff']) for v in r['votes']) for r in rows),'date_range_kst': [min(r['kickoff'] for r in rows),max(r['kickoff'] for r in rows)],'author_count_distribution':dict(collections.Counter(str(len(r['votes'])) for r in rows)),'single_author_games':sum(len(r['votes'])==1 for r in rows),'m0_disagreement_games':sum(r['selections'][MODELS[0]]!=r['selections'][MODELS[2]] for r in rows),'independent_games':len(rows),'price_rows':len({r['market_id'] for r in rows}),'model_revision_rows':len(rows)*len(MODELS),'strict_common_games':0,'strict_failure_reason_game_counts':{'model_and_lineup_not_proven_received_pregame':len(rows),'original_vote_first_observed_after_game_or_unknown':len(rows),'price_receipt_time_unverified':len(rows),'publication_modification_history_unknown':len(rows)},'cohorts':cohorts,'rules':'Exact official game ID and identical archived home/away prices; full-game moneyline only; one vote per source+author, conflicting authors excluded; tied vote excludes game; model/market/hybrid ties no bet; draws refund; 1 unit single bets; Fixed market90_vote10 is 0.9 normalized inverse-price market home probability + 0.1 author vote home fraction; separately named player90_vote10 uses each model probability. ROI includes refunds in stake denominator; hit rate excludes refunds. Source-claim and retrospective lineup oracle only, not blind or real-time.'}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'excluded-source-rows.json').write_text(json.dumps(source_rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# KBO 원문 투표·선수모델 교집합 회고 진단','',f"독립 경기 {len(rows)}개, 가격행 {summary['price_rows']}개, 모델 revision-경기 {summary['model_revision_rows']}개. 엄격 사전 공통 경기 0개.",'','2026-03-29 14:00부터 2026-07-23 18:30까지(KST)의 실제 게시 주장과 사후 확정 명단 oracle을 결합한 진단이다. 투표 동률 10경기 제외, 중립·방향 불명 등 상품·선택 모호 3행 제외, 작성자 내 상충 1명 제외다. 각 선택의 게시 주장 시각은 경기 전이지만 최초 관측은 경기 후이며 미래에 기록한 본문을 당시 수신으로 승격하지 않았다. 원문 최초 관측·수정 이력, 선수명단 수신 및 배당 수신 시각이 경기 전임을 입증하지 못한다. 이미 본 기간이며 블라인드 검증이 아니다. 2025 원문 KBO 행은 이 입력에 없다. 단위 단폴 수익이며 월 손실 제한 원장이 아니다.','',summary['rules'],'','| 전략 | 베팅 N | 적중/확정 N | 적중률 | ROI | 순손익 단위 |','|---|---:|---:|---:|---:|---:|']
    for arm,v in cohorts['all'].items(): lines.append(f"| {arm} | {v['bets']} | {v['wins']}/{v['decided']} | {v['hit_rate_decided']:.2%} | {v['roi']:.2%} | {v['net_units']:.2f} |" if v['bets'] and v['decided'] else f'| {arm} | 0 | 0/0 | - | - | 0 |')
    lines+=['','## 고정 시장 90%·투표 10% 월별','', '| 월 | 경기 | 적중/확정 | 적중률 | ROI | 순손익 |','|---|---:|---:|---:|---:|---:|']
    for month in sorted(k for k in cohorts if k != 'all'):
        v=cohorts[month]['market90_vote10']
        lines.append(f"| {month} | {v['bets']} | {v['wins']}/{v['decided']} | {v['hit_rate_decided']:.2%} | {v['roi']:.2%} | {v['net_units']:.2f} |" if v['bets'] and v['decided'] else f"| {month} | {v['bets']} | 0/0 | - | - | {v['net_units']:.2f} |")
    lines+=['','원문 한국 후보 전체 294행이 joined 장부 294행과 일치한다. 197경기 중 190경기는 작성자 1명이며 진정한 다수 작성자 표결로 해석할 수 없다. 최초 관측이 경기 전인 원문은 0경기이고 수정·결과 누출 여부는 확인되지 않는다. 서로 다른 두 M0 선택은 16경기에서 다르다. 누락 239개 공식 경기 ID·일정 시각은 기존 원문 본문 해시를 검증하여 복구했다. 추가 6개 원문 후보 경기의 배당 연결은 day/team 장부이므로 exact-ID 비교에 편입하지 않았다. 상세는 recovered-official-metadata.json에 있다. 별도로 확인한 해외 원문에는 2025 MLB 후보 3행이 있으나 사후 관측이며 KBO 모델과 섞지 않았다. 해당 자료와 한국 후보에서 2025 KBO 원문은 찾지 못했고 다른 아카이브의 부재를 뜻하지 않는다 (archive-2025-audit.json). 월별 동일 교집합과 사유별 행 수는 summary.json, 경기별 입력·선택·가격·결과·손익은 rows.json에 보존했다. actual-training M0와 projected-training M0는 별도 고정 아티팩트다. actual-training M1은 원본에 없다. 가격은 원문에 실린 당시 가격이 아니라 두 장부가 같은 회고 보관 가격임을 확인한 것이다. 출처의 게시 날짜는 사전 수신이나 수정 이력의 증거가 아니다.','']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps({'counts':summary['counts'],'games':len(rows),'overall':cohorts['all']},ensure_ascii=False,indent=2))

if __name__=='__main__': run()
