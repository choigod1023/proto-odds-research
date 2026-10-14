# 로컬 모델 토너먼트 사전고정 프로토콜

2026-10-01. 기준 main `97ffc5da`. 이 문서를 커밋한 후 실행한다. 이미 PR256에서 노출된 D1/SP1 자료이므로 **탐색적 후향 nested walk-forward**이며 새 독립 검증이 아니다. 운영 읽기·쓰기·추천·배포·유료 연산 없음. 최종 커밋/push/PR은 주 담당자가 맡는다.

## 자료와 분할

PR256 `scripts/evaluate_draw_calibration_holdout.py`의 감사된 parse/download 로직을 출처를 밝혀 새 스크립트에 복사·적응한다. 이전 커밋을 cherry-pick하거나 실행 시 다른 worktree에 의존하지 않는다. 원천 고정 revision은 `e82cf59161e82e0fbcdc49ad02819e45c821a5c8`, https://github.com/sosthene14/footballdataset/tree/e82cf59161e82e0fbcdc49ad02819e45c821a5c8/datasets 이다. D1/SP1 2019/20~2024/25 12파일, B365H/D/A 비마감 가격만 사용한다. 가격 관측시각·구매 가능성·Proto 가격은 없다. 각 D1 시즌 306/SP1 380경기, FTR/득점/날짜/리그/중복 검증, 가격 누락 제외 수와 원천 SHA256을 저장한다. 누락 가격 경기의 날짜 예산도 유지한다.

- fold 1: 학습 2019/20~2021/22, inner validation 2022/23, outer test 2023/24.
- fold 2: 학습 2019/20~2022/23, inner validation 2023/24, outer test 2024/25.
- 모든 경계는 두 리그를 합친 날짜 최대/최소가 엄격히 분리되어야 한다. 같은 날짜의 결과를 다른 경기 예측에 쓰지 않는다. 시즌 중 online 갱신 없음.
- inner로 모델/정책을 선택하고 선택 ID를 고정한 뒤 학습+inner로 모델 계수만 재학습해 outer에 적용한다. fold 2가 과거가 된 fold 1 시즌을 쓰는 것은 의도한 expanding walk-forward다. 전체 outer 결과로 최종 단일 설정을 다시 고르지 않는다.

## 고정 확률모델 10개

1. multiplicative devig.
2. power devig.
3. Shin devig.
4. Shin log-prob temperature: T∈[0.5,2], 학습 평균 multiclass log loss 최소화.
5–6. Shin log-prob + class bias, L2 λ∈{0.01,0.1}; `softmax(log(p)+b)`, 평균 NLL+λ||b||²/2.
7–8. regularized log-prob linear-softmax calibration, λ∈{0.01,0.1}; `softmax(log(p)+[1,log(p)]W)`, 평균 NLL+λ||W||²/2. identity 주변 residual 정규화이며 논문의 모든 변형을 재현한다는 뜻은 아니다.
9–10. Shin과 λ=.01 위 linear-softmax 보정값의 convex blend, 보정 비중 α∈{0.25,0.5}. 학습 결과로 α를 연속 최적화하지 않는다.

온도 외 학습 optimizer L-BFGS-B, maxiter=300, 실패 시 중단한다. 모델 입력은 당시 배당에서 변환한 확률만이다. 모델은 리그 통합 학습, 결과로 골라 추가 특징/새 모델을 만들지 않는다. 작은 행렬 CPU, BLAS 스레드 1개로 제한한다.

## 고정 정책 24개와 기준선

각 모델에 scope∈{all,favorite,underdog,draw} × per-leg EV threshold∈{0,.02,.05} × pair rank∈{maxprob,ev}를 적용한다. **240개 설정**을 inner/outer 각각 평가한다. fold 수가 달라도 새 설정은 추가하지 않는다.

- all: 배당>1인 H/D/A 전부, 1.5 미만 저배당도 포함.
- favorite: 경기 최저 배당(동률 포함), underdog: 최저 배당보다 엄격히 큰 선택(무승부 포함), draw: D만.
- EV는 `p*quoted_odds-1`, 문턱 비교는 부동소수 오차 허용 1e-12. 알려진 실제 수익이 아니라 모델 값이다.
- 각 경기에서 자격을 충족하는 선택 중 rank 점수가 최대인 하나를 택하고, 날짜·리그마다 상위 서로 다른 두 경기를 조합한다. maxprob 점수는 p, ev 점수는 p*odds; 양수 점수의 두폴 곱 최대화와 같다. 동률은 match_id, H/D/A 순서다. 결과는 선택기에 전달하지 않는다.
- 각 리그·날짜 예산 1단위, 최대 두폴 하나에 1단위 투자. 두 경기가 없으면 현금. 동일 날짜 두 리그는 각 1단위다. 최소 배당/추천 개수를 억지로 채우지 않는다.
- **주 기준선**: Shin/all/maxprob, EV 문턱 없음. 사후 선택하지 않으며 positive-EV 필터 때문에 모두 현금이 되는 기준선을 쓰지 않는다.
- **보조 대리 기준선**: Shin, p≥.60 및 1.5≤odds<2.2, maxprob, EV 문턱 없음. 정확한 archived production 입력이 없으므로 둘 다 운영정책 재현으로 부르지 않는다.

## inner 선택과 외부 평가

정책 winner: inner 조합≥20개인 설정 중 **전체 리그-날짜 예산당 순손익 최대**, 동률은 해당 모델의 inner log loss, 설정 ID 순. 자격 설정이 없으면 주 기준선. 20개 문턱은 희소 한 번 적중만으로 채택하는 위험을 줄이기 위한 고정 규칙이다. 수익을 보장하지 않는다.

별도 확률 winner: 모델 10개 중 inner 평균 multiclass log loss 최소, 동률 ID 순. 정책 winner와 확률 winner를 혼동하지 않는다. 두 선택 함수에는 inner 요약만 전달한다. outer 240개 설정을 전부 기록하지만 outer 상위권으로 새 winner를 정하지 않는다.

저장할 항목:

- 모든 설정의 inner/outer 건수·조합·적중·coverage·현금·투자·순손익·ROI·예산당 수익 및 선택 해시.
- 모든 모델의 inner/outer multiclass log loss, Brier, argmax 적중률. single-pick 결과는 경기당 argmax 하나/경기당 1단위 가상 투자로 별도 표기한다. 두폴 예산과 합산하지 않는다. 평균배당·저배당 선택 비율 포함.
- fold별 선택 ID와 inner 근거, 학습/재학습 계수, 날짜 범위, outer 승자/두 기준선의 날짜별 선택과 누적 곡선.
- primary inference: nested로 선택된 정책의 주 기준선 대비 **paired budget-return delta**, ISO 연도·주 두 리그 공동 cluster bootstrap, seed20261001, 5000회, 95% percentile CI. 현금 날짜도 포함. 같은 주를 짝지어 표집한 ROI 차이/적중률 차이 및 각 정책 CI도 저장한다. 0투자 bootstrap 표본은 해당 비율에서 제외하고 수를 기록한다.
- 확률 winner의 baseline 대비 log-loss/Brier/argmax 적중률 차이에도 paired 주 cluster CI. fold별 및 합친 outer를 보고한다. 단일픽 수익은 부차 기술통계다.
- 주간 클러스터링은 경기 독립성·주 사이 독립성·모델 추정 불확실성을 입증/해결하지 않는다. 두폴 확률 곱은 가정이다.
- 각 trial outer CI나 White Reality Check는 구현하지 않는다. 240개 비교와 이미 노출된 자료라는 한계를 명시하며 ‘유의한 최고 모델’로 주장하지 않는다.

정산은 B365 원배당 곱이 주 지표. Proto rounding 시나리오는 이번 범위에서 생략한다. 모델 추가/outer 결과 기반 grid 변경 없음. 한 번의 bounded 토너먼트를 수행하며 구현 버그 수정 재실행은 공개한다.

## 검증·산출물·제외

소유 파일은 `scripts/local_model_tournament.py`, `tests/test_local_model_tournament.py`, 이 프로토콜, `docs/research/2026-10-01-local-tournament-results.json`, `docs/research/2026-10-01-local-tournament-report.md`뿐이다. 합성 회귀 테스트: 날짜/분할 누출, outer 결과를 바꿔도 inner winner/fit 불변, 확률 정규화, 미래·당일 결과 없이 선택, cash 같은 예산, 서로 다른 경기 두폴, 저배당 포함, gate 경계, 주 공동 cluster, 순손익 집계. 실행 시간·환경·코드/자료 해시와 optimizer 상태를 남긴다. 운영 전체 테스트/배포/프로덕션 작업 없음.

Elo/result 모델, Dixon–Coles 득점모델, xG·라인업·부상·라이브·딥러닝은 이번 bounded odds-only 비교에서 제외한다. Elo/득점 모델은 가능하지만 별도 지연 특징·득점분포 검증이 필요해 범위를 늘리지 않는다. xG/라인업은 시점이 확인된 원천이 없다. ‘모든 방법을 비교했다’고 주장하지 않는다.

## 문헌 근거의 범위

사용자가 확인해 전달한 일차 문헌이다. 방법 선택의 근거이며 축구/Proto 수익 보장이 아니다.

- 시험: [Clarke et al. 2017](https://www.sciencepg.com/article/10.11648/10026106), power/multiplicative/Shin devig.
- 시험: [Guo et al. 2017](https://proceedings.mlr.press/v70/guo17a.html), temperature. 원 논문의 신경망 보정 근거를 배당확률에 탐색적으로 적용.
- 시험: [Kull et al. 2019](https://papers.neurips.cc/paper_files/paper/2019/hash/8ca01ea920679a0fe3728441494041b9-Abstract.html), regularized log-prob softmax calibration. 여기서는 identity residual L2 구현.
- 보류: [Hvattum & Arntzen 2010](https://www.sciencedirect.com/science/article/pii/S0169207009001708), Elo 축구 모형.
- 보류: [Dixon & Coles 1997](https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/1467-9876.00065), 득점 기반 모형.
- 해석 제한: [White 2000](https://doi.org/10.1111/1468-0262.00152), data snooping. Reality Check 구현 또는 그에 따른 유의성 검증은 하지 않는다.
