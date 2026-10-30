# MLB 동결 후보: 무제한 ROI 검증 및 부모 하니스 입력

310경기에서 원본 배당·저장 확률·픽·결과를 연결해 계산했다. 기존 MLB 보고치는 월 손실 제한 없이 매 경기 1만 원 단폴을 산 결과다. 본 작업에서는 월 −50,000원 제한, day+1/day+2 정산 민감도, 필터 학습, bootstrap을 실행하지 않았다. 그 비교는 부모 하니스가 담당한다. 운영 코드·원천 자료·DB를 수정하지 않았고 커밋·푸시·PR·배포를 하지 않았다.

## 부모 하니스용 입력

- 기본 입력: [records.json](records.json), JSON 최상위 list, 4,572행.
- `cohort`는 정확히 `old116/evaluation`(1,856행)과 `external194/evaluation`(2,716행) 두 개다. 기간 전후 구분은 `period`에만 있다. 같은 코호트·모델은 전체 기간을 한 번에 시간순으로 재생해야 한다.
- old116: 116경기 × 16개 모델/참조. external194: 194경기 × 14개 모델/참조. 코호트 간 경기 중복 0, `(cohort, model, event_id)` 중복 0. 전체와 분할 기간을 중복된 행으로 넣지 않았다.
- 각 행은 `cohort, league, model, event_id, kickoff, pick, odds, net_per_unit, source_file, provenance_limits`를 포함한다. 결과는 home_score/away_score/hit/refund로도 보존했다. 소수 배당은 숫자이며 환불이면 net_per_unit=0이다. 실제 환불 경기는 0건이다.
- 모델과 시장 참조는 저장된 홈 확률과 선택 결과 확률 `probability`를 보존했다. `tipster_vote`는 확률로 오해하지 않도록 `probability`를 생략하고 원래 `vote_share_home, vote_share_selected, voters, vote_counts`를 보존했다.
- `kickoff`는 원본의 timezone 있는 ISO 시각이다. 원본 분할 기준 `date`는 한국 날짜이며 `source_calendar_date`에 별도로 보존했다. 예를 들어 UTC 2026-08-09 저녁 경기는 한국 날짜 2026-08-10 구간에 속할 수 있다.
- 실제 정산 시각과 배당 수신 증거가 없어서 `settled_at, available_at`은 전 행에서 생략했다. historical timecode 및 quote_decision_at을 실제 수신 시각으로 바꾸지 않았다.
- 코호트별 보조 파일은 [records-old116.json](records-old116.json), [records-external194.json](records-external194.json)이다. 기본 records.json과 동시에 합쳐 읽으면 중복되므로 하나의 입력 방식을 택한다.

## 모델 정의와 기간

- `candidate/linear:l2=10`: 2025 선택 검증에서 사전에 정한 logloss → Brier → 이름 순서로 선택한 주 후보. HIT head도 같은 후보여서 중복 행으로 내보내지 않았다. 선수 이전 시즌 EB prior, 발표된 타순 9명, probable starter의 시즌 통계로 23개 홈−원정 차이 특성을 만든 동결 로지스틱 모델이다. 학습 구간으로 표준화하고 z를 ±6, logit을 ±40으로 제한한다. 배당을 확률 계산에 넣지 않으며 홈 확률 ≥0.5면 홈을 고른다.
- 전체 후보 12개는 linear/matchup/hinge × L2 0.01/0.1/1/10이다. 위 주 후보 외 11개는 `exploratory_unselected_do_not_promote`로 표시했다. 평가 결과로 우승 후보를 다시 고르지 않았다. 모든 후보 확률을 저장 계수로 다시 검산했다.
- `market_only`: 원래 저장된 시장 홈 확률. 내보낸 배당으로 새 확률을 만들어 원본을 덮어쓰지 않았다.
- `research_market90_vote10`: old116만 존재. 시장 확률 90% + 저장된 픽스터 투표 비율 10%. 실제 운영 모델이라는 뜻이 아니다.
- `research_market81_vote9_learned10`: old116의 고정 보조 혼합. 기존 시장90:투표10을 90%, 동결 주 후보를 10% 사용하므로 실제 비중은 시장81:투표9:학습10이다.
- `market90_learned10`: external194의 고정 보조 혼합. 시장90:학습10이며 픽스터 입력이 없다. external baseline은 market_only와 같아서 별도 중복 모델로 내보내지 않았다.
- `tipster_vote`: old116의 저장 투표 방향. 투표 비율은 보정된 승률이 아니며, 본 작업에서 작성·게시 시각을 별도로 입증하지 못했다. 외부 194경기에 가상의 픽스터를 추가하지 않았다.

학습은 2024-03-20T10:05:00+00:00~2024-09-29T19:10:00+00:00의 296경기다. 선택 검증은 2025-03-18T10:10:00+00:00~2025-09-30T22:08:00+00:00의 300경기다. 선택 검증에서 주 후보는 171/300 적중, logloss 0.6883193976835899, Brier 0.2475865203791246이었다. 두 구간에는 이 원천에서 저장된 배당쌍이 없어 수익률을 만들지 않았다. 300경기 × 12개 저장 확률은 [selection-probabilities-no-prices.json](selection-probabilities-no-prices.json)에 분리했다.

2025 평가에는 2024년 296경기 fit을, 2026 평가에는 2024+2025의 596경기 fit을 쓴다. 학습/선택/평가 경기 ID는 겹치지 않는다. 그러나 사전 문서가 이미 검토한 자료의 후향 연구라고 명시하고 execution-notes에도 평가를 본 뒤 구현 정정이 있었다고 기록하므로, 엄격하게 미관측인 평가 표본은 0경기다. 전후 구간을 새로운 holdout으로 부르지 않는다.

## 제한 없는 실측 결과

원화 정산은 승리 시 Decimal(10000)×(배당−1)을 ROUND_HALF_UP으로 정수화하고 패배 −10,000원, 동점 0원이다. ROI는 총 순손익/총 투입금이며 모든 경기를 매수한다. 다음 숫자는 개별 단폴 결과이며 2폴·주간 슬롯·다른 리그 병렬 포트폴리오 ROI가 아니다.

### old116

106개 시작 시각, 116경기. 시작 범위 2025-11-01T00:00:00+00:00~2026-10-24T00:00:00+00:00.

- `candidate/linear:l2=10`: 52/116 적중, -283,000원, ROI -24.396552%.
- `market_only`: 63/116 적중, -167,400원, ROI -14.431034%.
- `research_market90_vote10`: 63/116 적중, -154,800원, ROI -13.344828%.
- `research_market81_vote9_learned10`: 63/116 적중, -154,800원, ROI -13.344828%.
- `tipster_vote`: 61/116 적중, -113,200원, ROI -9.758621%.

- 설명용 `before_2026-07-01`: 84경기, 주 후보 ROI -18.154762%, 기준선 -4.678571%. 기본 입력 cohort와 예산을 이 경계에서 나누지 않는다.
- 설명용 `from_2026-07-01`: 32경기, 주 후보 ROI -40.781250%, 기준선 -36.093750%. 기본 입력 cohort와 예산을 이 경계에서 나누지 않는다.

### external194

143개 시작 시각, 194경기. 시작 범위 2026-07-29T16:10:00+00:00~2026-08-23T00:40:00+00:00.

- `candidate/linear:l2=10`: 109/194 적중, -136,000원, ROI -7.010309%.
- `market_only`: 109/194 적중, -254,700원, ROI -13.128866%.
- `market90_learned10`: 111/194 적중, -219,100원, ROI -11.293814%.

- 설명용 `before_2026-08-10`: 77경기, 주 후보 ROI +1.519481%, 기준선 -14.584416%. 기본 입력 cohort와 예산을 이 경계에서 나누지 않는다.
- 설명용 `from_2026-08-10`: 117경기, 주 후보 ROI -12.623932%, 기준선 -12.170940%. 기본 입력 cohort와 예산을 이 경계에서 나누지 않는다.

external은 원래 230경기를 요청했고, snapshot/cutoff/start 시각 검사에서 탈락한 36경기를 제외한 194경기만 사용했다. 36경기에 baseline을 대입한 full-fallback230 수치를 채택 194경기 결과와 섞지 않았다. 제외 ID·사유는 sources-and-models.json에 있다.

## 원인 해석과 반증 가능한 후속 연구

주 후보는 old116의 116경기와 external194의 194경기에서 모두 홈을 골랐다. 따라서 이 표본에서 주 후보의 픽·정산은 홈 고정과 완전히 같다. 외부 표본에서 기준선보다 +118,700원 나았지만 old116에서는 연구9:1보다 −128,200원 나빴다. 선수 특성에 따른 선택의 우월성이나 안정적인 수익 개선으로 해석할 수 없다. 외부 후기 117경기에서는 후보 −12.6239%, 시장 −12.1709%로 상대 우위도 사라졌다.

월 제한을 없애면 손실 후 생략됐을 경기를 다시 매수하므로 ROI가 달라질 수 있다. 같은 원래 픽 집합과 단위 투자금이라면 전체 ROI는 제한으로 매수한 집합과 생략 집합 ROI의 투자금 가중 평균이다. 따라서 제한 없는 ROI가 더 높으려면 생략된 집합 ROI가 제한하에서 매수한 집합보다 높아야 한다. 이 조건이 MLB에서 실제 충족되는지는 부모의 정산 지연 민감도와 월 제한 결과가 필요하다. 손실 제한 해제 자체가 확률이나 기대값을 높인다는 증거는 없다.

아래는 아직 실행하지 않은 가설이며, 현 평가 결과로 규칙·계수·후보를 다시 고르자는 뜻이 아니다. 부모가 학습 구간에서만 정의하고 새 미관측 기간으로 검증해야 한다.

1. 홈 편향 검증: intercept/home-only 기준선과 주 후보의 픽 및 proper scoring을 새 기간에 함께 고정 비교한다. 현재 표본의 픽 차이는 0건이므로 향후에도 선수 모델이 홈 고정 대비 logloss·ROI 개선을 보이지 않으면 선수 정보의 추가 가치를 지지하지 않는다.
2. 가격을 반영한 선택: 현재 p≥0.5 규칙은 배당별 손익분기 확률 1/odds를 고려하지 않는다. 주 후보가 스스로 계산한 p×odds−1조차 0 이하인 픽이 old116 85/116건, external194 148/194건이다. EV 기준과 결측 처리를 학습 구간에서만 고정하고 미래의 거래 수·ROI·보정 오차로 검증한다. 현 평가의 EV 양수 부분집합 수익을 보고 임계값을 선택하지 않는다.
3. 배당·시점 수집 가설: 두 채택 코호트의 평균 overround는 14.5475%, 13.6403%다. 이는 실제 수수료나 손실의 인과 분해가 아니다. 미래에는 같은 픽·경기에 대해 실제 관측한 실행 가능 배당을 저장하고, 사전에 정한 가격 품질 조건이 표본 탈락만 늘리는지 혹은 순수익을 개선하는지 비교한다. 과거 quote 시각을 실제 수신 증거로 소급하지 않는다.
4. MLB 정보 추가 가설: 확정된 투수 손잡이/타자 좌우 상대성, 경기 전 확인한 불펜 사용량을 추가한 후보를 기존 동결 후보·홈 고정과 비교한다. 현재 후보는 이 정보를 검증된 형태로 모두 사용한 모델이 아니다. 구간 선택을 평가 결과로 바꾸지 말고 새 기간 logloss/Brier와 수익률이 함께 개선되는지를 판정한다.

## 검증·재실행·출처

실행 스크립트는 [normalize_mlb.py](normalize_mlb.py)이며 `python -B -X utf8 <절대경로>/normalize_mlb.py`로 오프라인 재실행할 수 있다. 네트워크를 차단하고 재학습하지 않는다. 출력은 이 MLB 폴더에만 쓴다.

310경기 원시 축약 입력에서 주 후보를 재추론했고 확률 오차 최대 1.1102230246251565e-16, 특성 오차 최대 1.6653345369377348e-16이었다. 저장 12개 후보의 평가 확률 3,720건과 선택 확률 3,600건을 동결 계수로 검산했다. 원본 정산 1,860건은 픽·적중·환불·원화 손익이 완전히 일치했다. 결과 필드 변형 310건에도 추론은 변하지 않았다. 입력 파일의 실행 전후 SHA256이 같았다. 수치·검증 로그는 [uncapped-summary.json](uncapped-summary.json), [validation.json](validation.json), 출처 해시·선정 지표·모델 정의는 [sources-and-models.json](sources-and-models.json)에 있다.

정확한 읽기 전용 출처 경로:

- 동결 재현 원시 입력: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\mlb\parity-fixtures.jsonl.gz`
- 모델 및 prior: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\mlb\model.json`, `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\mlb\priors-2024.json`, `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\mlb\priors-2025.json`
- 전체 원래 모델별 확률·픽·가격·점수·정산: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\predictions.jsonl`
- old116 원래 시장/투표 확률·투표·가격·결과: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\sources\mlb-evaluation.jsonl`
- external230 원래 시장 확률·가격·시작 시각·결과: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\sources\mlb-external-features.jsonl`
- 학습·선택·old116 역사 스냅샷: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\sources\mlb-snapshots.jsonl.gz`
- external230 수집·탈락 증거: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\external-snapshots.jsonl.gz`
- 전체 특성: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\inputs.jsonl.gz`
- 선정 및 계수: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\frozen-selection.json`
- 선택 기간 확률: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\validation-predictions.jsonl`
- 원래 결과: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\results.json`
- 사전 규칙 및 평가 노출 기록: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\PROTOCOL.md`, `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\mlb\execution-notes.json`
- 구현 정의: `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\scripts\mlb_lineup_nonlinear.py`

기본 records.json을 현재 검사로 다시 읽어 필수 필드, 두 cohort, 시간순 정렬, 중복 없음, 숫자 배당·손익, 310경기 연결을 확인했다. 저장 시장 확률은 원래 값을 보존했으며 실행 당시 구매 가능성·가격 수신 시각·실제 정산 시각·독립적인 전향 수익을 입증하지 않는다.
