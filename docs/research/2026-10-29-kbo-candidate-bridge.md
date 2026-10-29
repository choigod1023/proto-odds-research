# KBO 동결 e0 후보 추론 브리지

2026-10-29. 브랜치 `codex/kbo-candidate-inference-worker-20261029`, 시작점 `91d5f7179b26236211cd25b61b7463759e4a3e5b`. 운영 계약과 `2026-10-25-research-scope-correction.md`를 읽고 구현했다. 추천·운영 확률·DB·수집기·부모 runtime·MLB 파일을 수정하지 않는다. 병합·배포·모델 승격을 수행하지 않는다.

## 구현과 실제 입력

`src/kbo_frozen_candidate.py`는 동결된 `e0:residual:0.03`만 추론한다. 학습·재튜닝·평가 성적으로 후보 교체를 하지 않는다. 미래 추론은 2023+2024의 `final_fit`만 사용한다. beta는 `-0.06637396467535583`, scale은 `0.28102288647783685`, 학습 표본은 1,422개다. `selection_fit`은 보존된 2024 검증 원장 재현에만 사용한다.

읽기 전용 원본은 다음 sibling의 실제 파일이다.

`C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/baseball-information-stepchange-20261029/`

- `scripts/kbo_opponent_dynamic_research.py`
- `experiments/baseball-information-stepchange-20261029/kbo/frozen-selection.json`
- 같은 디렉터리의 `games.jsonl.gz`, `features.jsonl.gz`, `daily-state-snapshots.jsonl.gz`, `validation-ledger.jsonl.gz`, `evaluation-ledger.jsonl.gz`, `source-audit.json`, `input-manifest.json`.

원본 9개 파일의 SHA256을 실제 파일과 대조했다. 텍스트는 CRLF를 LF로 정규화하고 gzip은 원본 바이트를 해시한다. 경로·해시는 `data/model_candidates/kbo/input-manifest.json`과 `frozen-e0.json`, 실행 결과는 `reproduction.json`에 보존했다. `archived-reproduction.jsonl.gz`는 두 원장의 해당 후보·기준 확률·선택·가격·점수·정산과 e0 특징만 추린 910개 기록이다. `archived-games.jsonl.gz`는 원본 2,913경기, `archived-e0-snapshots.jsonl.gz`는 e0의 634개 날짜별 prior다. 선수·투수 문서나 나머지 후보를 runtime에 싣지 않는다.

모델 canonical artifact hash:

`bf5dd1f56b29eefabdf4a4a67c6ecb252ba5ffb9f80da9cb10f1e62ec7297df6`

seed canonical hash:

`7a4a25a7e165ffb638292d6517237c4787f01b27f8b9bff99d91178695a513a6`

이 해시는 JSON을 ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False로 직렬화한 SHA256이다. 원본 파일 바이트 해시나 원천 진위 인증과 구분한다.

## 정확한 상태 의미와 seed

league/team/date에 해당하는 날짜별 공통 prior를 쓴다. 모든 팀은 2023-04-01 중립 0에서 출발한다. 날짜 간격 d마다 전체 팀을 `2**(-d/120) * .35**연도차`로 감쇠한다. 홈 예측은 `sigmoid(.12 + r_home - r_away)`이며, 동점 y=.5, 그 외 승리 y=1·패배 y=0이다. 득점차 가중치는 `min(2, log1p(abs(hg-ag))/log(4))`, 동점은 1이다. 각 경기 gradient `.08*w*(y-q)`를 홈에 더하고 원정에서 뺀다. 같은 날짜의 모든 gradient는 **같은 날짜 시작 상태**에서 계산하고, 경기 ID 정렬 후 한 번에 더한다. 더블헤더 중간 결과로 그날 특징을 바꾸지 않는다.

특징 x는 `[r_home-r_away]`이고, 확률은 `sigmoid(logit(reference)+clip(x/scale,-6,6)*beta)`다. 현재/미래 경기 점수는 그 경기 날짜의 prior에 포함하지 않는다. 단순 승률·최근 폼이나 상대 팀을 무시한 갱신으로 대체하지 않았다.

`historical_seed()`의 `through_date`는 **2026-10-27**, `total_observations`는 **2,913**이다. 연말 전체 자료라는 뜻은 아니다. 원래 연구의 snapshot은 7/23까지만 있어서, 마지막 점수 날짜까지 원본 상태 갱신만 실행해 종료 seed를 만들었다. 모델을 다시 학습하지 않았다. 팀 canonical 이름은 다음 열 개다.

`KIA, KT, LG, NC, SSG, 두산, 롯데, 삼성, 키움, 한화`

seed는 팀별 평균·경기 수·최근 경기 날짜와 전체 관측 수를 담으며 약 1.3KB다. `seed_observed_at=None`, `historical_availability='unproven'`이다. 부모는 실제로 seed를 읽은 aware 시각을 별도로 전달해야 한다. 이후 판정에서만 쓸 수 있으며 과거 판정에 소급하지 않는다. seed 이전 정정이 있으면 `seed_history_unchanged=False`로 무효화하고, 2023-04-01부터 완전한 결과로 재구성해야 한다. seed에 정정분 gradient만 덧붙이지 않는다.

## 수집 작업 API

모든 함수는 JSON-safe mapping을 반환한다. `status='unavailable'`이면 `reason`을 읽고 확률·선택을 만들지 않는다. 명시적인 인수를 받으며 DB·네트워크·환경 변수·현재 시각을 조회하지 않는다. 모델 JSON과 seed JSON은 모듈 import 시 한 번 읽는다. 반환 객체를 바꿔도 내부 모델이나 seed가 바뀌지 않는다.

```python
normalize_proto_history(rows, *, decision_at, aliases=None)
verify_increment_coverage(
    *, schedule_days, results, coverage_from, complete_through, as_of, source_id
)
build_daily_state(
    *, state_date, as_of, results, source_signature, seed=None
)
build_state(
    *, fixture, decision_at, results, source_signature, seed=None
)
```

`normalize_proto_history`는 실제 `match_results`의 `source/season/round/game_no`, `kickoff/league/home_team/away_team/home_score/away_score/observed_at/result_state`를 받는다. 이 어댑터에서만 문서화된 naive kickoff를 KST로 해석한다. observed_at은 반드시 aware이고 kickoff 이후여야 한다. source는 `proto`, result_state는 `confirmed`여야 한다. 실제 Proto 팀명은 부모가 명시적인 aliases mapping으로 변환한다. 자동 유사 이름 매칭은 하지 않는다.

먼저 판정 전 source-key별 최신 revision을 선택한다. 같은 시각의 충돌, 최신 미확정 결과, 서로 다른 ID의 동일 kickoff·팀 경기 중복은 실패한다. 판정 시각 이상 revision은 무시하므로 미래 정정이 과거 상태를 바꾸지 않는다. 같은 팀의 서로 다른 kickoff 더블헤더는 허용한다. `source='dataset'`의 현재 import 시각을 과거 가용 시각으로 바꾸지 않는다. 부모는 반환된 records 중 각 상태 날짜 이전·seed 이후 구간을 명시적으로 공급한다. 구간 밖 기록을 builder가 조용히 버리지 않는다.

정규화된 결과 record 계약:

```python
{
    'source_fixture_id': stable_crosswalk_id,
    'league': 'KBO', 'date': '2026-10-29',
    'home': 'KIA', 'away': '삼성', 'hg': 4, 'ag': 6,
    'available_at': actual_aware_final_receipt_time,
    'availability_basis': 'official_final_observed_at',
}
```

공식 일정에서 직접 얻은 결과도 이 계약으로 부모가 정규화할 수 있다. 관측 시각은 최종 결과가 실제 담긴 응답의 수신 시각이어야 한다. 경기 시작·날짜·아카이브 스크랩 날짜로 대체하면 안 된다.

coverage helper의 일별 입력은 다음과 같다. 빈 날짜도 games=[]로 **명시**해야 하며 응답 누락을 무경기로 추정하지 않는다.

```python
{
    'date': '2026-10-29', 'league': 'KBO',
    'source': 'kbo_official_calendar', 'complete': True,
    'observed_at': actual_aware_calendar_receipt_time,
    'source_sha256': sha256_of_raw_official_response,
    'games': [{
        'source_fixture_id': stable_crosswalk_id,
        'home': 'KIA', 'away': '삼성',
        'status': 'final', 'hg': 4, 'ag': 6,
    }],
}
```

status는 `final/cancelled/postponed`만 허용한다. final에는 공식 일정의 hg/ag도 필요하다. coverage helper는 모든 날짜의 완전한 일별 manifest, 고유 ID, 팀·날짜·점수 일치, 종료 경기별 결과 존재, 예정/진행 경기 없음, 취소·연기 경기의 결과 부재, 결과가 일정 밖에 추가되지 않았음을 검사한다. 공식/Proto ID crosswalk는 부모가 유일하게 연결해야 한다. 이 함수가 팀 조합으로 ID를 추정하지 않는다. 관측된 완전한 일정에서 모든 경기가 종료·취소·연기 상태라면 해당 날짜의 자정 전에도 coverage를 인정한다.

성공 시 `source_signature`와 보존할 `coverage_evidence`를 반환한다. signature 필드는 `source_id`, `records_hash`, `coverage_from`, `complete_through`, `coverage_verified=True`, `coverage_available_at`, `coverage_evidence_hash`다. digest는 내용 결합·변경 감지용이며 원천의 진위를 독립적으로 인증하지 않는다. 부모가 실제 전체 응답과 parser 완전성을 확인할 책임을 진다. 공급한 complete=True가 거짓인지 순수 함수만으로 알아낼 수 없으므로 이 한계를 반환값에도 표시한다.

seed 사용 시 signature에 `seed_hash=SEED_HASH`, 실제 `seed_observed_at`, `seed_history_unchanged=True`를 추가한다. 첫 증분 `coverage_from='2026-10-28'`이고 complete_through는 대상 경기 날짜의 정확한 전날이어야 한다. 완전성·원문·crosswalk·정정 검증을 하지 않았다면 coverage_verified를 채우지 않고 unavailable을 유지한다. seed 없이 재구성할 때 coverage_from은 `2023-04-01`이다.

`build_daily_state`는 결과·seed·coverage 관측이 as_of 이하인지 검사하고 가장 늦은 입력 시각을 보존한다. as_of 날짜는 state_date 이전 또는 같은 날짜여야 한다. 예컨대 10/28 무경기와 10/29 모든 최종 결과가 10/29 22:55에 알려졌다면, state_date=10/30의 prior를 미리 계산할 수 있다. 아직 알 수 없는 날짜의 coverage나 현재 날짜 결과를 그 날짜 prior에 넣는 것은 허용하지 않는다.

입력은 결과 12,000개, 일정 3,660일, 일별 일정 20경기로 제한한다. 상한을 넘으면 일부만 사용하지 않고 unavailable이다. 수집 작업에서 날짜별 상태를 한 번 계산해 캐시한다. 부모의 기존 player_info 작업에서 실행하고 HTTP 요청별 이력 조회를 추가하지 않는다.

## 경기별 runtime API

```python
state_for_fixture(*, daily_state, fixture, decision_at)
infer_candidate(*, fixture, decision_at, state, reference, offers)
```

fixture는 `league/date/home/away`를 담고 infer_candidate에는 aware kickoff도 필요하다. daily_state의 state_date와 fixture.date는 **정확히 같아야** 한다. 판정은 kickoff보다 이전이고 날짜는 fixture.date 이전 또는 같은 날짜다. daily cache available_at은 판정 이하, 결과·seed·coverage의 가장 늦은 입력 가용 시각은 판정보다 **엄격히 이전**이어야 한다. 같은 target date 캐시로 오늘 저녁에 내일 경기를 판정할 수 있지만 내일 prior를 오늘 경기로 재사용할 수 없다.

대상 두 팀 각각 현재 연도에 실제 경기 이력이 있어야 하고, 마지막 경기부터 target date까지 30일 이하여야 한다. 전년도 상태만 감쇠해서 현재 상태로 표시하지 않는다. 30일은 성적에 맞춰 선택한 모델 파라미터가 아니라 보수적인 사용 차단 규칙이다. 시즌 시작·긴 휴식 구간은 자료가 부족하면 unavailable이다. 경기별 처리는 열 팀 상태를 검증·복사하는 정도이며 파일·DB·전체 이력을 읽지 않는다.

reference 계약:

```python
{
    'recipe': 'fixed91_same_electorate',
    'home_vote_share': 0.0, 'voters': 1,
    'observed_at': actual_aware_electorate_receipt_time,
    'snapshot_id': same_snapshot_id,
}
```

offers는 home/away 두 개이며 각각 `side/selection_id/offer_id/odds/market_probability/observed_at/snapshot_id/market='승패'/line=None/label=''`다. 실제 저장 roster에서 부모가 매핑한다. ID·snapshot 일치, 완전한 단일 시장, 유한 가격 >1, 시장 확률 합 허용 오차 .0002, 관측 시각을 검사한다. reference는 같은 시점 `0.9*home_market_probability + 0.1*home_vote_share`다. 무투표·recipe 누락을 시장 fallback으로 채우지 않는다. 유효 electorate의 작성자 중복·시각·T-30 자격은 부모/바깥 원장이 검증해야 한다. 현재 운영 투표 정책과 이 연구용 offset은 별개다.

반환값의 `home_probability`는 홈 확률이고 `probability`는 선택된 방향의 확률이다. p>=.5면 홈이다. `side/selection_id/offer_id/odds/input_hash/state_hash/artifact_hash/fit`을 반환한다. 모든 반환값은 `affects_selection=False`, `affects_probability=False`, `promoted=False`다. digest는 인증 서명이 아니며, 캐시를 고친 뒤 임의로 재해시하는 호출자의 조작을 막는 보안 경계로 주장하지 않는다. 새 정정이 수집되면 부모가 해당 캐시를 교체·무효화해야 한다.

## 실제 재현 결과와 금전적 한계

`infer_archived(feature, *, source_signature, partition)`는 사전 계산된 e0 특징과 명시적 archive source hash 묶음을 사용한다. 항상 `capture_kind='retrospective_archive_reproduction'`, `contemporary_availability_proven=False`다. 이 API 결과를 미래 state로 쓸 수 없다. 현재 경기 점수·승자를 바꿔도 추론 결과는 바뀌지 않는다.

과거 날짜만 쓰는 연구 규칙은 브리지에서 **`historical_prior_day_midnight_kst_assumption`**으로 표시한다. 원본에는 실제 available_at이나 자정 관측 증거가 없다. 이 이름은 전날 결과를 다음 날부터 사용할 수 있었다고 놓는 회고 가정이며, 자정에 실제 최종 결과를 수신했다는 뜻이 아니다. 새 official observation 경로에는 이 가정을 허용하지 않는다.

- 2024 validation 712개: 원래 `selection_fit` 사용. 확률 최대 절대 오차 `1.6653345369377348e-16`, 선택·원 단위 손익 712개 전부 일치. 417승, 순손익 **-708,100원**, ROI **-9.9452%**, 일 마감 최대 낙폭 **727,000원**. 시장 기준은 411승, -815,800원, -11.4579%, 830,900원이었다.
- 2026 evaluation 198개: `final_fit` 사용. 확률 최대 절대 오차 `1.1102230246251565e-16`, 선택·원 단위 손익 198개 전부 일치. 119승, 순손익 **-144,500원**, ROI **-7.2980%**, 일 마감 최대 낙폭 **166,700원**.
- 동일 평가의 기존 연구 9:1 기준: 119승, **-146,600원**, ROI **-7.4040%**, 낙폭 **157,700원**. 후보는 손실이 2,100원 작았으나 낙폭은 9,000원 커졌다. 시장 기준은 117승, -208,400원, -10.5253%, 247,500원이다.
- 7월 전 152개 후보 손익은 -99,800원, 7월 이후 46개는 -44,700원이다. 이후 구간의 기존 연구 9:1은 -41,700원, 시장은 +5,100원이었다. 이 구간에서 후보가 더 나았다거나 미래 흑자를 입증했다고 주장하지 않는다.

허용 오차는 atol=1e-12, rtol=0이다. 원본 train/validation/evaluation 특징 1,620개도 실제 Python 재생으로 확인했다(최대 오차 `1.249000902703301e-16`). e0 snapshot 634개·팀 평균 6,340개 최대 오차는 `1.1102230246251565e-16`, 경기 수·최근 관측일·전체 관측 수도 일치한다. 종료 seed를 원본 순차 상태와 대조했다.

정산은 원래 연구의 전체 매수·단폴 1만원·월 손실제한 없음이다. 무승부는 환불하고 ROI 분모에는 전체 매수액을 넣는다. 두 원장에는 환불 경기가 없지만 동점 상태 갱신은 별도로 검증한다. 목표는 손실·낙폭을 함께 비교하는 것이며 흑자·적중 증가를 완료 조건으로 만들지 않았다. **실제 9:1 운영 원장을 재현한 것이 아니며**, 2폴·월 5만원 손실제한·실제 주문 실행을 재현하지 않았다. evaluation은 이미 반복 연구에 쓰인 자료이고, 당시 배당 구매 가능 시각·투표 가용 시각·결과 가용 시각의 동시 증거는 없다.

## 검증과 남은 연결 조건

```powershell
python -m pytest tests/test_kbo_frozen_candidate.py tests/test_operating_model_capture.py tests/test_operating_model_comparison.py tests/test_operating_ledger_inputs.py -q -p no:cacheprovider
```

최종 관련 회귀 실행은 **176 passed, 1 skipped**다. skipped는 실제 운영 export를 공급해야 실행하는 기존 선택 검사다. 새 KBO 테스트는 모두 로컬에 포함된 아카이브나 명시적인 합성 시각을 사용한다. 공식 결과의 실제 공개 시각을 지어내지 않았다. 부모가 보고한 10/29 KIA4–삼성6 형태를 회귀에 넣었으나 테스트의 수신 시각은 합성이고, 이 worker가 실서버 official 응답이나 새 원장 기록을 확인한 것은 아니다. build/UI/실서버 검증은 부모 통합 범위이며 이 worker의 테스트 성공으로 대체하지 않는다.

확인한 경계는 source-key 정정·동시각 충돌·최신 미확정·중복 실경기·naiveKST 변환, 명시적 무경기 날짜·누락 일정·진행 경기·점수 불일치·취소 경기, 결과/seed/coverage 시각 경계, 상대 강도·득점차·동점·공통 날짜 prior·더블헤더·연도 감쇠, cache 날짜·시각·변조·전년도/오래된 상태, 같은 snapshot offer·투표·가격 검사다. 미래 날짜 prior를 자정 전 생성해 판정하고 오늘 경기로 오용하면 차단하는 회귀도 통과했다.

runtime에는 slim 모델·seed만 import한다. 전체 아카이브 gzip은 검증 전용이다. 100회 합성 seed+1결과 추론의 tracemalloc peak와 실제 실행 환경은 `reproduction.json`에 보존한다. 과거 전체 이력 scan은 수집 작업의 재구성 경로이며 매 경기 실행할 작업이 아니다.

남은 부모 연결 조건은 실제 공식 일정의 전체 응답·관측 시각·raw hash 보존, Proto/official 게임 ID와 팀명 crosswalk, seed 최초 관측 시각 보존과 seed 이전 정정 감지, 날짜별 캐시 갱신·무효화, 같은 snapshot electorate/offer와 T-30 감사, 새 비교 기록의 전향 검증이다. 이 증거가 없으면 `history_completeness_unproven`, `missing_schedule_day:*`, `seed_correction_requires_full_rebuild`, `input_not_available_before_decision`, `missing_current_season_team_history`, `stale_team_state` 등의 unavailable을 보존한다. 현재 로컬 재현 성공만으로 운영 수집·신규 기록·배포 완료를 주장하지 않는다.
