# 외부 북메이커 정보 증분 검증 사전 프로토콜

2026-10-01. 작업 기준 `ec96bf37`(main97ffc5da + PR258 의존성). 결과 평가 전에 이 문서만 커밋한다. 운영·DB·추천·배포·유료 연산 없음. 최종 커밋/push는 주 담당자가 맡는다.

## 결과 비열람 가격 감사와 자료 고정

고정 원천 revision `e82cf59161e82e0fbcdc49ad02819e45c821a5c8`, https://github.com/sosthene14/footballdataset/tree/e82cf59161e82e0fbcdc49ad02819e45c821a5c8/datasets . D1/SP1 2019/20~2024/25 12파일, E0/I1/F1 2023/24~2024/25 6파일, 총18파일. D1/SP1 4,116경기와 외부3리그 2,132경기로 범위를 고정한다. 기존 연구에서 노출된 기간이며 전체 평가가 탐색적 후향 검증이다. 외부 리그는 이번 inner 선택에서 제외하지만 과거 연구 노출 때문에 fresh holdout이 아니다.

프로토콜 전 `local_market_information.py --audit`는 헤더와 가격의 유효성만 검사했다. HomeTeam 빈 행을 제외하고 가격 H/D/A의 finite/>1 여부만 집계하며 FTR/득점/실제 적중은 읽지 않았다. 결과 CSV 전체는 전송됐지만 결과 열을 추출·출력·집계하지 않았다.

처음 BW/IW/PS/WH/VC만 감사했을 때 2024/25의 3개 이상 공통 커버리지는 60~63%였다. **결과가 아닌 헤더**에서 추가 비마감 BF/1XB를 확인해 최종 원천 집합을 BW,IW,PS,WH,VC,BF,1XB로 고정했다. B365, BFE(exchange), Avg/Max, 모든 closing(C) 열은 consensus에서 제외한다. 두문자 VC의 C는 closing 표지가 아니므로 명시적 whitelist `VCH/VCD/VCA`는 허용한다.

- 2024/25 D1 유효 가격 수: B365306/BW189/IW0/PS306/WH234/VC0/BF304/1XB297. 최종 공통306/306.
- SP1 2019/20은 PS 누락2건으로 공통378/380, 2021/22는 누락1건으로379/380.
- F1 2024/25는 3개 외부 가격 요건으로 공통305/306.
- 나머지 파일은 최종 공통100%. 모든 파일이 95% 이상이다. 파일별 헤더/원천별 유효수/SHA256을 최종 JSON에 저장한다.

공통 표본은 유효 B365+PS 가격과 **외부 whitelist 3개 이상**을 모두 만족해야 한다. 파일별95% 미만이면 중단하고 표본을 바꾸지 않는다. 결측 모델을 B365로 대체하지 않는다. 평가 모델 전체와 matched baseline에 동일 공통 표본을 쓰고, 누락 경기의 날짜도 원래 리그-날짜 예산에는 남긴다. 전체 B365 가격 표본 기준선도 별도로 보고해 제외 영향을 드러낸다. 단순 가용 가격 평균이므로 원천 조합이 시즌별 달라지는 한계가 있다.

## 고정 모델4개 × 기존 정책5개

모든 원천은 비마감 H/D/A를 각각 Shin devig한다. 확률모델4개:

1. `b365`: 목표 구매 가격 B365만의 Shin 확률. 고정 기준선.
2. `consensus_mean`: 목표 B365를 제외한 유효 whitelist 원천의 Shin 확률 산술평균.
3. `consensus_median`: 같은 확률들의 outcome별 중앙값 후 합1로 재정규화.
4. `ps`: PSH/PSD/PSA만의 Shin 확률.

가중치 학습·별도 보정·결과 기반 원천 제외 없음. 다른 북메이커 가격은 예측 정보로만 쓰며 **모든 선택·정산의 가격은 B365**다. 최고 배당 쇼핑, 마감배당, 거래소 수수료 없는 가상 수익을 만들지 않는다. 비마감이라는 사실만으로 동시 관측/구매 가능성이 입증되지는 않는다.

PR258 `scripts/local_score_model_tournament.py`의 `POLICIES/choose_tickets/settle/betting_metrics/betting_ci/loss_ci`를 명시적 import해 재사용한다. 정책은 highestprob, p60range(과거60%·1.5~2.2 대리범위), p60low(60%·2.2미만), ev02, ev05. highestprob에는 1.5미만 저배당도 포함하고 EV 바닥을 두지 않는다. 총20개 고정 설정이며 새 문턱을 탐색하지 않는다.

리그-날짜별 1단위 고정 예산, 서로 다른 경기 두폴 최대1개, 없으면 현금. 확률 곱은 독립 가정이며 distinct-game/주 bootstrap이 독립성을 증명하지 않는다. 실제 Proto/현재 운영정책 재현은 아니다. Proto 반올림 시나리오는 생략한다.

## 시간 분할·선택·외부 적용

- fold23: D1/SP1 2022/23 inner → 2023/24 outer. 2019~21 자료는 가격 커버리지 감사 기록으로만 사용하며 fitted 계수가 없다.
- fold24: D1/SP1 2023/24 inner → 2024/25 outer. 2019~22도 동일하게 이전 자료이며 outcome으로 가중치를 학습하지 않는다.
- inner 모든 날짜 < core 및 E0/I1/F1 outer 모든 날짜를 검사한다. 당일 결과를 특징으로 쓰지 않는다.
- inner probability winner:4개 중 multiclass log loss 최소, 동률 ID. policy winner:20개 중 inner tickets≥20의 예산당 손익 최대, 동률 모델 log loss와 ID. 자격 없으면 b365/highestprob.
- 선택 함수는 inner 요약만 받는다. core inner 선택 ID를 잠근 뒤 해당 시즌 core outer 및 외부 E0/I1/F1에 그대로 적용한다. 외부 리그에서 재선택/재학습 없음.
- 주 사전지정 정보 비교는 `consensus_mean` 대 `b365`의 matched log-loss 차이. 고정 highestprob 정책 비교와 inner 선택 전략을 별도로 보고한다. 단일 모델을 outer 결과로 다시 고르지 않는다.

## 출력·한계·검증

모든 inner/core outer/external outer20설정의 건수·coverage·cash·적중·ROI·예산당 수익·선택 해시, 모델별 log loss/Brier/argmax 및 단일픽 가상 수익, inner winner ID, 날짜별 주요 정책 선택/정산을 저장한다. D1/SP1와 external3리그는 별도 보고한다. 시즌별·리그별 고정 분해를 모두 저장한다. 같은 정책의 B365 비교도 저장해 원천 정보 효과와 정책 변경 효과를 구분한다.

paired ISO주 bootstrap5000회, 기존 helper seed20261001. 모든 리그 같은 주를 공동 표집하며 현금 날짜 유지. 확률오차와 예산당 수익차이/ROI차이/적중차이 구간을 보고한다. 20개 설정·반복 사용 데이터·내부 선택을 포괄한 다중비교 보정은 아니며 White Reality Check를 구현하지 않는다.

테스트: B365/closing/Avg/Max를 바꿔도 외부 consensus 불변, 외부가격 변경 반영, 결측3원천/PS/95% 검사, 확률합, 결과 없는 예측/선택, inner 선택에 outer 불가, chronology, B365 정산·서로 다른 경기·현금 동일예산, 원천 FTR/득점/중복 검사. 가격 감사 뒤 프로토콜을 고정하고 한 번의 bounded 평가를 실행한다. 버그로 재실행하면 기록한다.

## 방법 근거

[Kaunitz et al. 2017](https://arxiv.org/abs/1710.02824)은 여러 북메이커의 확률 정보를 이용한 mispricing 탐색을 제안한다. 초록은 closing 장기 시뮬레이션과 고빈도/실제 베팅 실험을 구분한다. 이번 비마감 CSV와 관측/체결 조건이 같지 않으므로 수익을 보장하지 않는다. [2010 다중 북메이커 예측 연구](https://www.sciencedirect.com/science/article/pii/S0169207009001733)는 주 담당자가 확인한 근거다. 이 세션에서 발행처 열람은403으로 실패했으며 원문 결과를 새로 검증했다고 주장하지 않는다. 신규 정보의 유용성을 현재 고정 자료에서 직접 측정한다.
