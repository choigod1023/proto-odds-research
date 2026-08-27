# AI 적중률 통합 설계

- 작성일: 2026-08-27
- 대상: 경기별 확률, 오늘의 선택, 선수·출전 정보, 홈페이지 설명
- 운영 결론: **현재 최종 확률은 Shin 시장확률이며 AI 보정은 0%p다.**

## 1. 먼저 확인한 사실

현재 워크포워드 시장 잔차 모델은 다음 식을 사용한다.

\[
\operatorname{logit}(p_{\mathrm{final}})
=
\operatorname{logit}(p_{\mathrm{Shin}})
+ X\beta
\]

2024년만으로 규제 강도를 정하고 2025·2026년을 순서대로 평가한 결과는 아래와 같다.

| 평가 구간 | 시장 적중률 | 잔차 모델 적중률 | 차이 | 판정 |
|---|---:|---:|---:|---|
| 2025 | 60.23% | 60.41% | +0.17%p | 신뢰구간이 0 포함 |
| 2026 | 61.49% | 61.69% | +0.20%p | 신뢰구간이 0 포함 |
| 2026 농구 | 70.38% | 70.91% | +0.52%p | 종목 부분집합이며 신뢰구간이 0 포함 |

점추정은 약간 올랐지만 사전등록된 깨끗한 미래 홀드아웃이 없고, 목표인 +0.5%p를
통계적으로 입증하지 못했다. 그래서 모델을 운영에 승격하지 않는다. 상세 수치는
`findings/accuracy_formula_lab.json`에 보존한다.

이 결과는 “AI가 소용없다”가 아니다. **시장과 같은 정보를 다시 학습하는 AI에는
추가 정보가 거의 없었다**는 뜻이다. 다음 실험은 모델 크기를 키우는 것보다 시장에
아직 반영되지 않은 새 정보와 그 최초 관측 시각을 만드는 데 집중해야 한다.

## 2. AI를 넣을 위치

### A. 시장 잔차 모델

AI가 팀 승률을 처음부터 다시 만드는 대신 시장확률이 틀린 부분만 학습한다.

\[
r_\theta=f_\theta(X_{\mathrm{as\text{-}of}}),\qquad
p_{\mathrm{raw}}=\sigma\!\left(\operatorname{logit}(p_{\mathrm{Shin}})+r_\theta\right)
\]

후보 모델은 종목별 계층 로지스틱 회귀와 작은 gradient boosted tree를 함께 둔다.
복잡도는 성능이 아니라 시간순 홀드아웃 결과로 선택한다. 입력에는 다음만 허용한다.

- 같은 예측 시점의 프로토 배당과 해외 컨센서스
- 오프닝 대비 현재 움직임과 교차 마켓 일관성
- 예측 시점 이전 팀 폼, 득실·슈팅 품질, 휴식·이동·연전
- 예측 시점에 이미 발표된 선발, 라인업, 결장·징계
- 리그·시즌·규정 변화와 자료 누락 상태

종료 결과, 마감 배당, 나중에 수정된 라인업은 입력할 수 없다.

### B. 선수·출전 정보 AI

생성형 AI가 확률을 직접 쓰지 않는다. 공식 발표와 신뢰 가능한 원문에서 다음
구조화 이벤트만 추출한다.

`event_id, player_id, team_id, status, source_url, published_at,
first_seen_at, effective_at, confidence, verification_status`

결장 영향 후보식은 다음처럼 분해한다.

\[
\Delta_{\mathrm{absence}}
=
P(\mathrm{결장}\mid\mathrm{당시\ 정보})
\times (V_{\mathrm{선수}}-V_{\mathrm{대체}})
\times E(\mathrm{출전비중})
\times I_{\mathrm{전술\cdot 포지션}}
\]

선수 이름만 있는 결장 목록은 설명 자료다. 최초 관측 시각, 대체선수 가치,
예상 출전시간이 함께 있어야 잔차 모델 입력 자격을 얻는다. NBA 연구에서도 중요한
선수의 결장은 오프닝 라인을 움직였지만 대부분 마감 전에 가격에 반영됐다. 따라서
우리 기회는 “결장 자체”가 아니라 **고정된 프로토 가격 이후 처음 확인된 정보**다.

### C. 보정·앙상블 AI

모델 원확률은 그대로 쓰지 않는다. 시간순 보정 구간에서 종목별 temperature scaling
또는 isotonic calibration을 비교하고 Brier score와 log loss가 좋아지는 방식만 쓴다.
앙상블도 평균을 내면 좋아진다고 가정하지 않고, 앙상블 자체의 보정을 다시 검정한다.

### D. 선택적 예측 AI

매일 화면은 만들되 모든 경기에 확률 보정을 강제하지 않는다. 고정된 커버리지
(예: 전체의 20%, 40%, 60%)마다 시장과 같은 경기에서 적중률을 비교한다.

- 자료 시각 불일치
- 공식 라인업 발표 전
- 모델 간 방향 불일치
- 학습 분포 이탈
- 배당 갱신 후 재계산 전

위 조건에서는 `withhold` 또는 `recalculating`을 반환한다. 보류 횟수를 줄이려고
사후에 문턱을 바꾸지 않는다.

### E. 생성형 AI

허용: 공식 원문 구조화, 팀명 정규화 후보, 계산된 사실을 읽기 쉬운 한국어로 변환.

금지: 선택·확률·기준점·배당·금액 생성, 출처 없는 부상 추정, 문장을 그럴듯하게
만들기 위해 반대 근거 삭제. LLM이 문장을 다듬었는지는 기록하되 확률 영향은 항상
`false`다.

## 3. 운영 승격 관문

AI 잔차를 0이 아닌 값으로 반영하려면 아래를 모두 통과해야 한다.

1. 모델·특성·데이터 컷오프를 경기 전에 해시로 고정한다.
2. 한 실제 경기당 하나의 `event_id`로 append-only 예측 원장을 만든다.
3. 최소 300개는 배관 점검용일 뿐 최종 증거로 부르지 않는다.
4. 1·7·14일 날짜 블록 부트스트랩에서 Brier와 log loss 차이의 상한이 0보다 작다.
5. 적중률 개선 점추정과 95% 신뢰구간 하한이 모두 +0.5%p 이상이다.
6. 종목별 적중률 저하가 0.5%p를 넘지 않고 Brier 저하가 0.0005를 넘지 않는다.
7. 고정 커버리지, T-30분 실제 구매 가능 가격에서 같은 방향으로 재현된다.
8. ROI는 확률 정확도와 별도로 평가하며, 수수료·마진을 넘지 못하면 수익 우위라 부르지 않는다.

관문 하나라도 실패하면 운영식은 자동으로
\(p_{\mathrm{final}}=p_{\mathrm{Shin}}\)으로 돌아간다.

## 4. 홈페이지 정보 구조

한 경기에는 하나의 `decision_snapshot`만 둔다.

`시장 기준 → AI 후보 잔차 → 실제 반영 잔차 → 최종 확률`

화면의 모든 컴포넌트는 원시 JSON을 각자 해석하지 않고 하나의
`decision-view-model`만 읽는다. 이 원칙으로 다음 오류를 막는다.

- 추천이 없을 때 화면이 모델 최대확률을 새 추천으로 만드는 오류
- 경기 카드와 상세 설명이 서로 다른 팀을 고르는 오류
- 실시간 배당만 새 값인데 확률과 해설은 옛값인 혼합 오류
- 같은 폼·선발·결장 내용을 요약과 별도 패널에서 반복하는 오류
- 자료가 확률에 쓰이지 않았는데 아무 설명 없이 노출되는 오류
- 스냅샷이 없는 예전 모델 선택을 브라우저가 시장 판정으로 재포장하는 오류
- 실시간 배당과 예전 확률·오늘 조합이 한 화면에 섞이는 오류

자료마다 `market_baseline, ai_residual, decision_gate, explainer` 네 사용처를 모두
기록하고 `used, shadow, context_only, ignored, missing` 중 하나를 명시한다.
홈페이지는 이를 “최종 반영 / 연구 중 / 설명만 / 미반영 / 자료 없음”으로 보여준다.

배치는 다음 한 번씩만 사용한다.

1. 페이지 상단: AI의 네 역할과 현재 0%p 반영 상태
2. 경기 요약 탭: 최종 판정과 경기 흐름
3. AI 판정 탭: 확률 계산 경로와 자료 사용 원장
4. 선수·팀·출전 탭: 원자료
5. 배당 표: 시장확률과 연구 수치를 접어서 비교

판정 계약은 `event_id`(실제 경기), `selection_id`(결과 의미),
`offer_id`(회차·게임번호), `input_revision_hash`(가격·입력 상태)를 분리한다.
브라우저는 네 식별자와 v2 스키마가 정확히 맞지 않으면 새 선택을 만들지 않고
`withhold`한다. 배당이 실시간으로 바뀌면 조합·추천·시장확률·AI 차이를 모두
숨기고 같은 revision 재계산을 기다린다.

시간도 `feature_cutoff_at`, `built_at`, `reconstructed_at`으로 분리한다.
자료 관측 시각이 cutoff 뒤면 생성 단계에서 거부한다. 기존 JSON을 이관한 판정은
`pre_registered=false`로 표시해 적중률 및 모델 승격 근거에서 제외한다.

## 5. 구현된 안전장치

- `src/ai_decision.py`: 안정적인 경기·선택 ID, 시장 기준 선택, 단일 판정 스냅샷,
  자료 사용 원장, 입력 해시·시각 검사, 중복·침묵 미반영 검사
- `src/generate_v2.py`: 구조 모델은 shadow로만 저장하고 운영 선택은 Shin 시장확률로 고정
- `web/src/lib/decision-view-model.js`: 화면이 추천을 재생성하지 못하게 하는 단일 경계
- `web/src/components/AiDisclosure.jsx`: 페이지·경기별 AI 반영 상태
- `web/src/pages/Markets.jsx`: 중복 패널 제거, AI/선수/팀/출전 탭 분리, 실시간 배당 변경 시 확률 숨김

현재 승격 artifact 허용목록은 비어 있다. JSON에
`status=operational`, `validated_edge=true`를 임의로 넣어도 최종확률은 시장값으로
닫힌다. 미래 모델은 사전등록 결과와 artifact hash를 함께 코드 리뷰로 등록해야만
0이 아닌 AI 잔차를 반영할 수 있다.

## 6. 근거 문헌

- Forrest, Goddard, Simmons (2005), [Odds-setters as forecasters](https://www.sciencedirect.com/science/article/pii/S0169207005000300):
  정보가 많은 통계 모델도 북메이커 확률을 일관되게 이기지 못했다.
- Hubáček, Šourek, Železný (2019), [Exploiting sports-betting market using machine learning](https://www.sciencedirect.com/science/article/pii/S016920701930007X):
  시장과 같은 예측을 반복하기보다 시장과 다른 잔차와 선수 단위 정보를 연구했다.
- Dare et al. (2015), [Player absence and betting lines in the NBA](https://www.sciencedirect.com/science/article/pii/S1544612315000227):
  중요한 결장은 오프닝 편향을 만들지만 대부분 마감 전에 반영됐다.
- Arntzen, Hvattum (2021), [Team ratings and player ratings](https://journals.sagepub.com/doi/10.1177/1471082X20929881):
  팀·선수 평가를 함께 쓰는 경기 결과 모델을 검토했다.
- Guo et al. (2017), [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html):
  높은 분류 성능과 정확한 확률 보정은 다르다.
- Geifman, El-Yaniv (2019), [SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html):
  거절 선택을 포함한 모델은 고정 커버리지의 위험을 직접 최적화한다.
- Mortier et al. (2023), [Calibration of probabilistic classifier sets](https://proceedings.mlr.press/v206/mortier23a.html):
  앙상블도 자동으로 보정되는 것은 아니다.
- Bifet, Gavaldà (2006), [ADWIN](https://www.cs.upc.edu/~Gavalda/papers/adwin06.pdf):
  시간에 따라 분포가 바뀌는 데이터에서 변화 감지와 적응 창을 다룬다.

## 결론

적중률을 높일 가능성이 가장 큰 순서는 **더 큰 범용 AI**가 아니라
**동일 시점 시장 기준선 → 늦게 공개된 공식 선수 정보 → 종목별 잔차 →
확률 보정 → 고정 커버리지 선택**이다. 현재 데이터로 입증된 증가는 0%p이므로
운영 AI 반영은 0%p를 유지한다. 다음 전향적 원장에서 +0.5%p 관문을 통과할 때만
홈페이지의 “연구 중” 배지를 “검증 반영”으로 바꾼다.
