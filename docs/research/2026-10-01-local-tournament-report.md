# 배당 기반 로컬 모델 토너먼트 결과

240개 고정 설정을 비교했지만, inner에서 선택한 방법이 다음 시즌의 확률 정확도나 수익을 개선한다는 근거는 얻지 못했다. 합친 outer 평가에서 최고확률 기준선의 두폴 ROI는 −8.6633%, inner 선택 정책은 −8.5043%였다. 선택 정책은 투자 횟수가 적어 전체 예산 손실은 작았지만, 짝지은 예산당 수익 차이의 95% 구간은 0을 포함했다. 운영 변경 근거로 쓰지 않는다.

## 범위와 사전고정

프로토콜 커밋 `a270079e`, 기준 main `97ffc5da`, 브랜치 `codex/local-model-tournament-20261001`. [프로토콜](2026-10-01-local-tournament-protocol.md)을 먼저 커밋한 뒤 실행했다. 실행 도중 모델·grid·분할을 변경하지 않았다. 이미 PR256에서 사용한 D1/SP1 자료이므로 **탐색적 후향 nested walk-forward**이며 새로운 독립 확인이 아니다.

실행은 2026-10-01, 다운로드·학습·960개 trial 요약·bootstrap 계산을 포함해 로컬 5.4215초였다. Python 3.14.3, NumPy 2.5.1, SciPy 1.18.0, BLAS/OMP 스레드 1개, 다운로드 최대 4개 병렬, 유료 연산 없음. 고정 설정은 240개이며 2 folds × inner/outer의 반복 평가를 합친 실행 수가 960개다. 기준선과 확률 지표 계산은 별도다. 결과를 보고 설정을 추가하거나 최적 설정을 다시 고르지 않았다.

## 원천과 운영자료 구분

[고정 원천 미러](https://github.com/sosthene14/footballdataset/tree/e82cf59161e82e0fbcdc49ad02819e45c821a5c8/datasets)의 D1/SP1 2019/20~2024/25 12파일, 4,116경기다. PR256의 감사된 parse/download 코드를 출처를 밝혀 새 스크립트에 복사·적응했으며 이전 커밋을 cherry-pick하지 않았다. 실행 시 다른 worktree에 의존하지 않는다. 각 시즌 D1 306/SP1 380경기, 배당 누락 0건. 12개 URL·SHA256·행 수·누락 수는 PR256 저장 결과와 모두 일치했다.

사용 가격은 B365H/D/A다. [원 제공자의 설명](https://football-data.co.uk/data.php)에 따라 비마감 가격으로 취급한다. 실제 관측시각·같은 날짜의 모든 가격을 동시에 구매할 수 있었는지·Proto 구매 가격은 확인할 수 없다. 원배당 곱으로 가상 정산했고 Proto rounding 시나리오는 이번 실험에서 생략했다. 날짜는 원천 날짜이며 KST 발매 회차를 재현하지 않는다.

주 담당자가 별도로 확인한 로컬 운영자료 감사 결과:

- `data/raw/prediction_ledger/pregame.jsonl`: 14,150,261 bytes, SHA256 `77c224f03c28217f552cb98b96a02e350a1e0e79d12a7145a81a3296a693d4e0`.
- prediction revision 1,326행이지만 고유 경기는 **207개**다. 독립 경기 1,326개가 아니다.
- captured와 observed가 모두 T−30 이전인 조건은 1,299행/206경기, 늦은 revision은 27행, 결과 필드는 0개다.
- 관측 기간 2026-08-28~09-01. 버전은 shin-market-anchor-v1 1,316행, internal-context-blend-v2 10행.
- 추적 파일 목록에서 별도 settlement/result ledger를 찾지 못했다. `data/q0_result.txt`는 과거 배당 검증의 집계 보고서로, 해당 원장 revision별 공식 정산 연결 자료는 아니다. 이는 제한된 로컬 감사이며 운영 전체 원장이 없다고 단정하는 근거는 아니다.

감사 재현 명령은 주 담당자 소유 스크립트 `python -B scripts/audit_local_prediction_inputs.py data/raw/prediction_ledger/pregame.jsonl`이다. 이 토너먼트는 해당 원장이나 운영 DB를 읽지 않았다. 정확한 당시 정책 입력과 결과 연결이 없으므로 **현재 운영정책의 ROI 실험이 아닌 외부 대리실험**이다. 보조 비교는 ‘과거 60% 구간 대리 기준’이며 현재 정책을 재현했다고 부르지 않는다. 현재 정책의 1.50 선호·저배당 fallback 등과도 구분한다.

## 비교한 방법과 평가 절차

확률모델은 multiplicative, power, Shin, 학습 온도 보정, class bias L2 .01/.1, log-prob linear-softmax residual 보정 L2 .01/.1, 그 보정과 Shin의 25%/50% blend, 총 10개다. 모델 계수는 과거 자료로만 학습했다. 모든 optimizer가 수렴했다.

선택은 all/favorite/underdog/draw × 개별 EV 문턱 0/.02/.05 × 두폴 maxprob/EV 순위, 총 24개다. all에는 1.5 미만 저배당이 들어간다. 정배는 최저 배당(동률 포함), 역배는 그보다 높은 배당이며 무승부도 포함한다. 리그·날짜마다 예산 1단위, 서로 다른 경기 두 개의 조합 최대 하나, 자격 경기 부족 시 현금이다.

주 기준선은 Shin/all/maxprob, EV 문턱 없음으로 고정했다. 과거 60% 구간 대리 기준은 p≥.60, 1.5≤odds<2.2, maxprob, EV 문턱 없음이다. 둘 다 사후 선택한 기준선이 아니다.

- Fold 1: 학습 2,058경기(2019-08-16~2022-05-22), inner 686경기(2022-08-05~2023-06-04), outer 686경기(2023-08-11~2024-05-26).
- Fold 2: 학습 2,744경기(2019-08-16~2023-06-04), inner 686경기(2023-08-11~2024-05-26), outer 686경기(2024-08-15~2025-05-25).

두 리그를 함께 묶어 날짜 경계가 엄격히 분리되는지 확인했다. 같은 날짜의 결과로 다른 경기를 보정하지 않고, 시즌 중 갱신도 없다. inner에서 조합 20개 이상인 설정 중 예산당 순손익 최대를 정책 winner로 고정한 후, 학습+inner로 모델 계수만 재학습해 outer에 적용했다. 별도로 inner log loss 최소 모델을 확률 winner로 선택했다. outer 결과를 선택 함수에 전달하지 않았다. Fold 1의 outer 시즌이 Fold 2에서 과거 inner가 되는 것은 의도된 expanding 절차다.

## 두폴 정책의 outer 성과

합친 평가는 1,372경기, 482 리그-날짜 예산, 73 ISO 주다.

- 주 기준선: 306조합/115적중, 적중률 37.5817%, coverage 63.4855%, 현금 176단위, 순손익 −26.5098, ROI −8.6633%, 예산당 −5.5000%. 저배당(<1.5) 선택 232개가 두폴 legs에 포함됐다.
- inner 선택 정책: 115조합/7적중, 적중률 6.0870%, coverage 23.8589%, 현금 367단위, 순손익 −9.7800, ROI −8.5043%, 예산당 −2.0290%. 저배당 선택 0개.
- 과거 60% 구간 대리 기준: 12조합/3적중, 적중률 25.0%, coverage 2.4896%, 현금 470단위, 순손익 −4.8225, ROI −40.1875%, 예산당 −1.0005%. 매우 적은 투자로 예산 손실이 작다는 것을 우수한 ROI로 해석하면 안 된다.

주 기준선 대비 inner 선택 정책의 **paired 예산당 차이 +3.4709%p, 95% CI [−16.5059, +26.0642]%p**. ROI 차이는 +0.1590%p [−66.5888, +83.0277]. 적중률 차이는 −31.4947%p [−39.3801, −23.2382]로, 수익을 목표로 선택한 고배당 조합은 기준선보다 적중률이 크게 낮았다. 서로 다른 적중률·배당·투자 횟수를 구분해야 한다.

개별 ROI 95% CI는 기준선 [−23.8504%, +6.5102%], 선택 정책 [−69.5596%, +70.0020%]. 각 5,000개 bootstrap 표본이 유효했다. 두 리그 같은 ISO 주를 함께 표집했고 현금 날짜도 유지했다. ROI 분모는 투자액, 예산 수익 분모는 전체 날짜 예산이다.

Fold별 선택과 변동:

- 2023/24 정책: `linear_0.1/underdog/0.00/ev`. inner 자격 설정 36개 중 선택, inner 123조합/9적중, 예산당 +17.0742%. 재학습 후 outer는 18조합/2적중에 순손익 +27.8, ROI +154.4444%, coverage 7.4380%. 기준선은 155조합/60적중, 순손익 −15.0971, ROI −9.7401%. paired 예산당 차이 +17.7261%p [−9.3963, +52.2047]. 큰 적중 두 번과 적은 투자에 의존한 결과다.
- 2024/25 정책: `linear_0.01/draw/0.00/ev`. inner 자격 설정 12개 중 선택, inner 65조합/9적중, 예산당 +49.1240%. outer에서는 97조합/5적중, 순손익 −37.58, ROI −38.7423%, coverage 40.4167%. 기준선은 151조합/55적중, 순손익 −11.4127, ROI −7.5581%. paired 예산당 차이 −10.9030%p [−35.9010, +17.8254]. 높은 inner 수익이 다음 시즌에 유지되지 않았다.

재학습으로 확률과 문턱 통과 여부가 달라지므로 inner와 outer 조합 수가 크게 달라질 수 있다. 사후에 outer 수를 채우거나 문턱을 조절하지 않았다.

## 확률 정확도와 단일픽은 별도 평가

inner 확률 winner는 Fold 1 `blend_0.25`, Fold 2 `bias_0.01`이었다. 이 경로를 합치면:

- log loss: 기준 0.956820 → 선택 0.957553, 차이 +0.000733 [−0.001265, +0.002825]. 낮을수록 좋다.
- Brier(세 클래스 제곱오차 합): 0.568024 → 0.568615, 차이 +0.000591 [−0.000622, +0.001863].
- argmax 적중률: 54.0087% → 53.7172%, 차이 −0.2915%p [−0.8137, +0.2229].
- 경기당 argmax 하나에 1단위씩 투자하는 **별도 단일픽**: 기준 1,372픽/741적중, 순손익 −42.72/ROI −3.1137%; 확률 선택 경로 1,372픽/737적중, 순손익 −52.20/ROI −3.8047%. 두폴 예산에 합산하지 않는다.

10모델 각각의 합친 outer log loss / Brier / argmax 적중률 / 단일픽 ROI는 다음과 같다. 전부 공개하는 기술통계이며 이 순위로 winner를 다시 선택하지 않았다.

- multiplicative: 0.957928 / 0.568643 / 54.0087% / −3.1137%.
- power: 0.956459 / 0.567842 / 54.0087% / −3.1137%.
- Shin: 0.956820 / 0.568024 / 54.0087% / −3.1137%.
- temperature: 0.957550 / 0.568407 / 54.0087% / −3.1137%.
- bias .01: 0.956855 / 0.568408 / 54.0816% / −2.8054%.
- bias .1: 0.956690 / 0.568225 / 53.9359% / −3.2515%.
- linear .01: 0.958035 / 0.568961 / 53.8630% / −3.4227%.
- linear .1: 0.957594 / 0.568769 / 54.0087% / −3.0328%.
- blend .25: 0.956892 / 0.568147 / 53.9359% / −3.2923%.
- blend .5: 0.957118 / 0.568344 / 54.2274% / −2.4803%.

power의 관측 log loss가 낮았다는 사실은 outer를 본 뒤의 기술적 비교다. 유의한 최고 모델이나 수익 가능한 방법이라고 주장하지 않는다. 모든 240개 정책의 inner/outer 요약과 선택 해시는 [결과 JSON](2026-10-01-local-tournament-results.json)에 있다.

## 문헌과 시험·보류 범위

아래는 **주 담당자가 원문/발행처에서 확인한 문헌**이다. 논문이 모든 데이터에서 수익을 보장한다고 해석하지 않는다.

- 시험: [Clarke, Kovalchik & Ingram 2017](https://www.sciencepg.com/article/10.11648/10026106)의 power/multiplicative/Shin 비교를 방법 선택 근거로 삼았다. 축구 ROI 보장 근거는 아니다.
- 시험: [Guo et al. 2017](https://proceedings.mlr.press/v70/guo17a.html)의 temperature scaling. 신경망 보정 연구를 배당확률에 탐색적으로 적용했으며 같은 개선이 보장되지 않는다.
- 시험: [Kull et al. 2019](https://papers.neurips.cc/paper_files/paper/2019/hash/8ca01ea920679a0fe3728441494041b9-Abstract.html)의 regularized log-prob linear-softmax 계열. 이 구현은 identity 주변 residual L2이며 논문의 모든 정규화 변형을 복제한 것은 아니다.
- 이 실험에서 보류: [Hvattum & Arntzen 2010](https://www.sciencedirect.com/science/article/pii/S0169207009001708)의 Elo 축구 모형, [Dixon & Coles 1997](https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/1467-9876.00065)의 득점모형. 별도 담당자의 `local_score_model_tournament.py` 동반 실험에서 Elo/득점 계열을 다룬다. 그 결과는 이 보고서 수치에 포함하지 않는다.
- 제한: [White 2000](https://doi.org/10.1111/1468-0262.00152)의 data snooping 문제를 고려해 outer 순위는 탐색으로만 기록했다. **White Reality Check를 구현하거나 통과했다고 주장하지 않는다.**

주 담당자의 로컬 감사에서 추적 파일 `data/raw/xg_snapshots.jsonl`의 존재를 확인했다. 189행이며 snapshot_at은 2026-08-30~09-04, 리그별 행 수는 kleague1 36, kleague2 49, j1 52, j2 52다. 다만 **이번 D1/SP1 평가 기간과 연결되는 시점 검증 xG·라인업 자료는 없다**. 이 실험에는 해당 경기의 부상 자료·실제 구매 배당·live feed도 연결하지 않았으므로 해당 특징 기반 방법은 비교하지 않았다. 딥러닝/대규모 hyperparameter 탐색도 제외했다. 모든 예측 방법을 시험했다는 주장은 하지 않는다.

## 재현·검증·인계

작업 디렉터리: `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/local-model-tournament-20261001`.

```powershell
python -B -m unittest tests.test_local_model_tournament -v
python -B scripts/local_model_tournament.py --output docs/research/2026-10-01-local-tournament-results.json
```

첫 평가 전에 합성 테스트 9개를 통과했다. 최종 테스트는 원천 parser 및 저장 결과의 독립 Decimal 정산·inner winner 재계산 검사를 추가한 **11개**다. 날짜 경계·동일 날짜 학습 거부, outer label 교란, 결과 없이 예측/선택, cash와 동일 예산, distinct-game 두폴, exhaustive pair 비교, 1.5 미만 배당과 문턱 경계, 주 공동 표집을 검사했다. 전체 운영 테스트는 주 담당자가 수행하며 중복 실행하지 않았다.

주 담당자의 독립 원천 대조도 완료했다. urllib로 고정 CSV 12개를 다시 가져와 저장된 모든 SHA256을 확인하고, 4,116개 원천 경기의 FTR과 Decimal B365 가격을 직접 파싱했다. 세 정책 × 482예산인 **1,446개 날짜별 정산 기록의 적중·배당이 모두 원천과 일치**했다. 별도 소유 `tests/test_local_tournament_artifacts.py`의 stdlib Decimal/코드 해시/grid·inner winner/원장 감사 테스트 3개도 주 담당자가 통과를 확인했다. 이 검증은 실제 Proto 체결 가능성이나 새 holdout의 독립성을 입증하는 검증은 아니다.

토너먼트는 한 번 실행했다. 평가 후 모델·정책 코드는 변경하지 않았고 테스트/보고서만 추가했다. 실행 스크립트 SHA256은 `2863b28313b537331a8aec3e78de983d4fe4be6594117f798b20e9530d479adb`이며 저장 JSON과 일치했다. 결과 JSON에는 devig 의존 코드 해시, 원천 해시, 환경·seed 20261001, 모델 계수·수렴 상태, 960개 trial, inner 선택 ID, outer 날짜별 tickets 및 누적 곡선이 있다. 원천 CSV는 재배포하지 않는다.

재사용 인터페이스: `download() -> (rows,sources)`, `split_fold(rows,23|24) -> (train,inner,outer)`, `choose(pregame_rows, probability_matrix, policy) -> tickets`, `settle(all_period_rows,tickets) -> records`, `summary(records)`, `paired_ci(base,challenger,numerator,denominator)`. 행 필드는 match_id/league/season/date/prices/y다. 가격 누락 행은 확률 계산에서 제외하되 all_period_rows에는 남겨 현금 날짜를 보존한다. paired_ci에는 동일한 날짜·리그 순서의 두 기록을 전달한다.

95% 구간은 관측 73주를 조건부 재표집한 것이다. 240개 설정 탐색, 이미 노출된 자료, 학습 추정과 모델 선택 불확실성을 모두 제거하지 못한다. 두 경기의 확률 곱은 독립성 **가정**이며, distinct-game 검사와 주 bootstrap이 경기 독립성을 입증하지 않는다. 같은 날짜 배당의 동시 구매 가능성도 확인하지 못했다. 이번 결과는 운영 승격이나 수익 약속이 아니다.

커밋은 사전 프로토콜 하나만 만들었다. 스크립트·테스트·JSON·이 보고서는 주 담당자의 독립 감사와 최종 커밋을 위해 남긴다. 운영자료 감사 및 득점 동반 실험 파일은 다른 담당자의 소유로 수정하지 않았다. push/PR/merge/배포/자동화 없음.
