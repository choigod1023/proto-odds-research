# CALIBRATION 문헌·계약 검증 (2026-10-30)

## 판단과 범위

공유 양의 온도 T 한 개를 독립 보정 구간에서 NLL로 학습하는 후보를 우선한다. 이는 확률 품질 후보이지 동일 경기 argmax 적중률을 직접 높이는 방법은 아니다. 적중률·확률 품질·ROI는 각각 검증한다. 아래는 부모 실험에 제안하는 사전 기준이며 새로운 적합·ROI 실험을 실행하지 않았다.

연구는 조건 충족 시 정확히 2폴, 부족하면 보류하며 채우기 픽을 넣지 않는다. 연구 월 손실 제한 OFF는 부모 실험의 조건이고 운영 계약 변경이 아니다. 코드·추적 파일·운영·배포·커밋·PR을 변경하지 않았다.

## 논문 6편: 원문 검증과 적용 한계

### 1. Guo, Pleiss, Sun, Weinberger (2017)
On Calibration of Modern Neural Networks. ICML, PMLR 70:1321–1330.
원문: https://proceedings.mlr.press/v70/guo17a/guo17a.pdf

공유 온도 보정 softmax(z/T)는 검증 NLL로 T>0 한 개를 맞추며 클래스 순위를 보존한다. 이미지·문서 신경망에서 효과를 보였지만 시장·투표의 우세나 스포츠 ROI를 검증한 논문은 아니다. 확률만 있으면 log(p)를 logits 대용으로 쓸 수 있으나 0 확률 처리와 정규화를 사전 고정해야 한다. 온도만 바꿨는데 같은 평가 경기의 argmax 적중률이 달라지면 동률·경기 집합·구현부터 조사한다. EV 임계치가 선택 경기 집합을 바꾸면 선택 표본 적중률은 달라질 수 있다.

### 2. Kull, Silva Filho, Flach (2017)
Beta calibration: a well-founded and easily implemented improvement on logistic calibration for binary classifiers. AISTATS, PMLR 54:623–631.
원문: https://proceedings.mlr.press/v54/kull17a/kull17a.pdf

이진 확률에서 sigmoid(a log p - b log(1-p) + c)를 사용하며 a=b=1,c=0은 항등이다. 작은 보정 표본의 isotonic 과적합을 지적한다. beta를 무조건 온도보다 우수하다고 해석하지 않는다. 논문이 항등을 포함하지 않는다고 지적한 logistic(score)과 저장소의 logistic(logit(p))는 다르다. 후자는 slope=1,intercept=0으로 항등을 포함한다. 3-way에 이진 beta를 각각 적용하고 재정규화하면 같은 보정 성질이 보장되지 않는다. 현재 작은 표본에서는 beta/isotonic을 이번 주 후보로 늘리지 말고 후속 독립 기간의 이진 민감도 분석으로 남긴다.

### 3. Walsh, Joshi (2024)
Machine learning for sports betting: should model selection be based on accuracy or calibration? Machine Learning with Applications 16, 100539.
논문: https://doi.org/10.1016/j.mlwa.2024.100539
저자 원고: https://arxiv.org/html/2303.06021v4

NBA 여러 시즌 학습과 한 시즌 베팅 평가로 정확도 기준과 보정 기준의 모델 선택을 비교한다. 동일 모델에 온도만 적용한 인과 실험이 아니며 선택된 알고리즘도 달라진다. 원고의 흑자 수치·모든 전략 수익 주장은 다음 정정문으로 수정됐으므로 재인용하지 않는다. 단일 시즌·단폴 평가를 우리 다종목 2폴로 일반화할 수 없다.

### 4. Walsh, Joshi (2025), 위 논문의 Corrigendum
Machine Learning with Applications 19, 100627.
DOI: https://doi.org/10.1016/j.mlwa.2025.100627
기관에 실린 정정문 전문: https://researchportal.bath.ac.uk/en/publications/corrigendum-to-machine-learning-for-sports-betting-should-model-s/

특징 생성 구현 오류로 후속 결과를 정정했다. 보정 기준 대 정확도 기준의 평균 ROI는 −9.77% 대 −26.78%, 최고는 +23.13% 대 +10.9%이다. eighth-Kelly는 −42.67% 대 −64.45%로 둘 다 손실이다. 보정 기준의 상대 우세와 평균 흑자는 다르다. 잘 보정된 모델도 Kelly에서 실패할 수 있다고 고쳤다. 이 수치는 논문 자체 결과이며 우리 ROI가 아니다. 출판사 직접 접근은 403이었으나 저자 소속 대학의 정정 전문에서 확인했다.

### 5. Gneiting, Raftery (2007)
Strictly Proper Scoring Rules, Prediction, and Estimation. JASA 102(477):359–378.
원문: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
DOI: https://doi.org/10.1198/016214506000001437

엄밀 적정 점수는 기대 점수에서 참 분포를 유일하게 최적으로 만든다. log-loss와 Brier를 확률 비교 지표로 쓸 이론적 근거다. 유한 표본에서 점수가 나아졌다고 완벽한 보정이나 양의 수익이 보장되지는 않는다. ECE 하나만 최소화하면 분별력이 낮은 예측도 유리할 수 있으므로 주 지표는 log-loss, 보조는 Brier, 신뢰도 구간·클래스별 보정은 진단으로 둔다. 시장과의 확률 차이도 참확률 오차 자체가 아니다.

### 6. White (2000)
A Reality Check for Data Snooping. Econometrica 68(5):1097–1126.
원문: https://users.ssc.wisc.edu/~behansen/718/White2000.pdf
DOI: https://doi.org/10.1111/1468-0262.00152

여러 규칙을 탐색한 뒤 최고 결과만 검정하면 우연을 우세로 오인한다. 전체 후보군의 기준모델 대비 성과를 고려하는 bootstrap Reality Check를 제시한다. 후보·리그·혼합·임계치·정책 탐색 전체를 기록해야 한다. 의존성·정상성 등 조건이 필요하며 6개 날짜로 검정의 신뢰성을 확보했다고 주장하지 않는다. 이번에는 소수 사전 후보와 손대지 않은 시간순 평가를 우선하고, 블록 재표집 구간도 탐색 진단으로만 해석한다.

## 저장소에서 읽은 실제 계약

- AGENTS.md와 docs/research/2026-10-25-research-scope-correction.md를 읽었다. 기존 odds/form 실험을 픽스터·모델 비교 완료로 바꾸어 설명하지 않는다.
- docs/PRODUCTION_CONTRACT.md의 최신 2026-10-28 정책은 tipster-vote-v1이다. 유일 최다 득표 방향만 선택하고 무표·동률은 보류한다. 투표는 확률에 작용하지 않고 선택 방향의 시장 확률을 사용한다. 과거 market90-tipster10-v2 계약과 구분한다. 실제 서버 상태는 조회하지 않았으므로 배포 사실을 보증하지 않는다.
- src/ai_decision.py의 OPERATING_MODEL_VERSION은 shin-market-anchor-v1이며 정책 선택과 확률 모델 이름이 서로 다른 층이다.
- src/probability_pipeline.py:149–249는 identity, platt/logistic, isotonic/piecewise_monotonic만 허용한다. platt/logistic은 sigmoid(intercept+slope*logit(p)), slope>0이며 isotonic 이름의 실행은 저장된 knots 사이 선형 보간이다. temperature/beta 전용 artifact는 지원하지 않는다. 이진 온도는 intercept=0,slope=1/T와 수학적으로 같지만 3-way softmax 온도를 개별 이진 변환으로 대체하면 안 된다. 지원 여부는 사용·승격 증거가 아니다.
- src/operating_model_capture.py의 operating, market_reference, fixed91_same_electorate, saved_score_reference, home_always는 역할이 다르다. 마지막 대조군은 확률이 없으므로 calibration 비교 대상이 아니다. saved_score_reference는 미검증 참조이고 동일 electorate 9:1은 과거 운영 모델을 정확히 재생했다는 뜻이 아니다.

## 입력에 대한 증거 수준

사용자/부모가 제공한 입력 감사 결과를 전제로 삼았고 본 작업에서 전체 이벤트를 다시 세지 않았다. TIMED 7,458 snapshots는 경기 수가 아니며 실제 4모델 입력 중 score 모델의 채점 가능한 기간은 6일뿐이다. VOTES 597 events=508 strict+89 disjoint NBA는 actual Shin/votes/90vote10이나 회고 자료여서 경기 전 가용성 증거가 없다. ARCHIVE 44,396 settled events는 시장 proxy이며 서비스 모델 이력이 아니다. 세 집단을 합쳐 하나의 전향 표본 크기로 표기하지 않는다.

## 부모 실험에 제안하는 최소 사전 검증

1. 경기 ID로 snapshots를 묶고 하나의 사전 판정 시각만 쓴다. 입력 실제 수신 완료, 배당 관측, 모델 artifact 생성 시각이 판정 이전인지 확인한다. 같은 경기 revision은 서로 다른 split에 두지 않는다. 학습 label은 학습 cutoff 전에 결과가 실제 가용한 것만 허용한다. 경기 날짜만 과거라는 이유로 미정산 결과를 학습에 넣지 않는다. 입증 못한 회고 자료는 별도 회고 집단으로 둔다.
2. 시간순 train → calibration/validation → untouched test를 고정한다. T를 train-fold 안쪽 보정 구간에서 맞추고 혼합·리그 shrinkage/recency는 다음 validation에서 선택한다. 두 단계에 같은 데이터를 재사용하면 그 사실을 명시한다. 부모가 소유한 market/fixed90vote10/EB/temperature/blend/shrinkage 외 후보를 추가하지 않는다. 6일 TIMED는 모델 승격 결론을 내리지 않는 기술 통계로 제한한다.
3. 전체 동일 경기 집합에서 multiclass log-loss 평균과 sum-k Brier 평균을 보고한다. clipping epsilon=1e-6을 사전 고정하고 clip 후 재정규화한다. ECE는 고정 5 bins와 각 bin n을 진단으로만 보고, 희소 bin은 불확실을 표시한다. 선택 후 표본 지표와 전체 표본 지표를 나란히 보고한다. 종목별·무승부 클래스별 손실 악화를 숨기지 않는다.
4. 기각 기준 제안: 확률 비유한/범위/합 오류, label/시각 누수, 같은 경기 split 중복이면 해당 실행 무효. 공통 경기 온도 전후 argmax 변경은 구현 검사 실패. Δlog-loss=후보−시장 ≥0 또는 Brier 악화면 공동 개선 통과 아님. 둘 다 개선해도 paired 날짜 블록 95% 구간이 0을 포함하면 우세 미입증. 시간 블록이 부족하면 구간 통과 대신 표본 부족 판정. 수치 허용 오차·최소 실용 개선폭은 결과를 보기 전에 부모 프로토콜에 확정한다.
5. ROI는 위 점수와 별도다. 같은 quote·stake·정산 정의와 조건부 2폴/no filler, 연구 loss cap OFF를 고정한다. 서로 다른 경기라도 독립을 보장하지 않으므로 p1*p2는 독립 가정의 추정으로 표시하고 동일 경기 2leg는 금지한다. 2폴 적중·손익·노출·보류율·낙폭을 기록한다. 불충분한 2폴 수나 0 포함 수익 구간은 흑자 미입증이다. ROI·적중률 둘 다 개선을 주장하려면 고정 test에서 두 결과를 각각 충족해야 하며 보정 개선으로 대체하지 않는다.
6. 여러 후보를 살펴봤다면 실패 후보까지 남긴다. 결과를 본 뒤 T 범위·EV cutoff·리그·날짜를 바꾸면 새 탐색으로 표시한다. 독립 test를 다시 학습/선정에 쓰지 않으며 논문 ROI를 우리 성과표에 넣지 않는다.

## 실행 확인과 남은 한계

별도 적합 없이 Python 표준 라이브러리의 결정적 수학 검사를 실행했다(checks.json). 이는 온도의 argmax 보존·단조 보정과 decision threshold 차이·beta 항등 및 기존 logit-logistic 항등을 확인할 뿐 모델 수익성을 검정하지 않는다. 외부 원문 실험을 재현하지 않았고 실제 구매 가능 가격, 시간 가용성, 리그별 충분한 표본 및 2폴 독립성은 추가 증거가 필요하다.
