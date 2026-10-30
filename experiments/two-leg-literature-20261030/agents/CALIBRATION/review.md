# FROZEN 소스 최종 확인 — 현재 상태

확인한 SHA-256: `92df250c9bb7e16f8ad187d0ad7a8ef2afb572816e7699e432bdd2389fd83ec5`. 요청된 FROZEN 해시, primary/claimed manifest, outcome-blindness.json의 소스 해시가 모두 일치한다. 현재 상태는 current-check.json에 기록했다. 아래 이전 버전의 상태·해시는 이 절이 대체한다.

주 결과 915 rosters / 35 sessions, 별도 claimed-time 민감도 552 rosters / 29 sessions를 확인했다. CLI --claimed-time-only와 claimed_time_frames는 소스가 주장한 pre-T30 게시 시각을 반영하는 별도 회고 민감도이며 실제 수신·실행 가능 가격 증명은 아니다. primary와 민감도 manifest 및 전체 fit의 strict_asof_training_certified=false, result_receipt_times_verified=false를 확인했다.

고유 경기 수 최소 표본·축소량, TIMED final 완전 공통집합, support hash, source_sha256 문서 별칭 수정은 유지돼 있다. 새 actionable 결함을 확인하지 못했다.

부모의 outcome-blindness.json만 읽고 통과로 인정한 것이 아니라 저장된 OUTCOME-PERTURBATION의 primary/claimed 결과를 원본과 독립 비교했다. 두 경우 모두 fits 동일, target 제외 예측 행 동일, session/선택 leg·stake·quoted odds 동일을 확인했다. net이 달라진 정책은 각각 101건으로 감사 기록과 일치한다. 검증 스크립트가 두 split test 날짜의 교집합에서 label을 회전한다는 범위도 확인했다. 이는 그 구간에서 직접적인 holdout label 의존성이 없다는 증거이며 upstream 특징의 인과적 가용성이나 실제 결과 수신 시각을 인증하지 않는다.

이번 확인에서는 fit/run/감사 스크립트를 실행하지 않았다. 저장 산출물 읽기·비교만 수행했고 CALIBRATION의 검토 파일만 갱신했다.

---

# 최종 종료 상태 — 이전 미해결 표시는 이 절로 대체

코드/manifest SHA-256: `2257c9a059a45f3625ffd26499c3f33b15c0e054b7a2fa9e6c681a1333b3682d`.

검토 범위에서 미해결 actionable 결함을 확인하지 못했다. 이전 검토의 sample gate/shrinkage 지적은 해결됐다. 글로벌 최소 학습/검증 표본과 리그별 축소 강도가 고유 event_id 개수를 사용하며 train_events/validation_events 출력도 실제 저장 ID의 고유 경기 수와 일치한다. TIMED 공통 roster는 final 벡터까지 완전해야 통과한다. 문서 키의 source_sha256 별칭도 적용했다.

native/common 평가 필드 및 paired 평가를 구분하고 support_sha256을 제공한다. 저장 예측에서 각 범위를 독립 재구성해 496개 비어 있지 않은 native/common 지표의 표본 수와 support 해시를 모두 확인했다. 공통 표본 관련 이전 지적을 미해결 버그로 남기지 않는다.

manifest와 6개 fit 출력의 result_receipt_times_verified=false, strict_asof_training_certified=false를 확인했다. 실제 정산 수신 시각을 증명하지 못하는 점은 명시된 연구 한계이며, 누수가 발견됐거나 아직 수정하지 않은 산술 버그라는 뜻이 아니다. 결과 확인 후 부록과 데이터 감사 수정이 이뤄졌다는 기존 설명도 유지한다.

최종 915 forecast rosters / 35 sessions를 확인했다. saved_pregame validation=0에 따른 learned pool 부재도 유지한다. 실제 적합·전체 실행·ROI 재실행 없이 코드와 저장 결과만 검사했다. 머신 판독 상태는 final-review.json에 기록했다. 아래 내용은 각 과거 버전의 검토 이력이며 현재 actionable 목록이 아니다.

---

# 최종 변경 재검토 (2026-10-30)

이 절이 아래 이전 검토보다 우선한다. 대상 코드/RESULTS manifest SHA-256은 `15b699de71c8a07a11ce8266d3186490d08341f245c8cd777dc05037981e8483`이다. 적합·run·ROI 정책은 다시 실행하지 않았다. 저장 예측·원자료·결과를 읽고 독립적인 집계 및 paired forecast 점수/CI 계산만 수행했다. 이 검토 파일 외 수정 없음.

## 판정

추가된 기준선, 공통 유일 투표 평가 범위 및 날짜별 paired CI에서 현재 결과를 무효화할 새 결함은 찾지 못했다. 원자료 제외 6행과 최종 915 forecast rosters / 35 sessions를 확인했다. 이전 검토의 '모델별 평가 모집단 차이' 지적은 paired 평가 경로에서는 해소됐다. 기존 native forecast 필드는 부분 모집단일 수 있다는 의미만 남는다.

## 독립 확인 결과

- 코드와 manifest 해시 일치. 검사 시작/종료 사이 RESULTS 파일 해시 변동 없음.
- 원자료 VOTES 597 events의 raw_forecasts를 별도로 읽어 publication>=kickoff인 6행/5경기를 확인했다. 이를 제거한 뒤 같은 event 내 문서 키 중복 0건, 같은 source/author 중복도 0건이었다. 원래 중복 문서의 두 행이 모두 늦은 게시로 먼저 제외되므로 duplicate_document_forecasts_excluded=0은 오류가 아니다. 최종 event 무표 제외 5건도 manifest와 일치한다.
- 최신 admissible_votes는 알려진 늦은 게시를 먼저 제외하고 event 안에서 document_sha256(없으면 source/author/url)로 묶는다. 문서 내 상충 방향은 문서 전체 제외하며 모르는 게시 시각은 unproven으로 센 뒤 회고 자료에 남긴다. 이를 경기 전 검증으로 승격하지 않는 부록과 맞는다. author당 여러 문서가 생기는 입력의 일반 처리와 현재 계약의 작성자당 한 표는 같지 않지만, 이번 남은 실제 입력에는 해당 중복이 없으므로 새 실제 오류로 올리지 않는다.
- 운영 기준선의 실제 코드 키는 `operating_unique_vote_market_reference`이다(요청에서 말한 current_unique_vote_market_reference의 의미). VOTES에만 정의되고 유일 최다 투표 방향을 선택하며 동률은 보류한다. EV·pair 순위에는 해당 방향의 market_p를 쓴다. proper scores는 시장 벡터, accuracy는 투표 선택이라는 설명이 결과 probability_semantics에 있다. 이는 확률모델 자체의 argmax accuracy와 구분돼야 한다.
- common_unique_vote_forecast는 VOTES에서 유일 최다인 같은 domain을 먼저 고정해 각 모델을 채점한다. forecast_delta는 candidate와 fixed90 예측을 모두 가진 target-known product를 짝지은 뒤 그 집합에서 event 가중치를 다시 계산한다. 운영 기준선의 동률도 제외한다. 따라서 이전 독립 결측 제거 지적이 paired 비교 경로에 그대로 적용되지는 않는다.
- 저장 heldout-predictions와 forecast-rosters를 결합해 ALL 52행의 paired accuracy/Brier/log-loss 차이를 별도 구현으로 재계산했다. 차이 0(허용오차 1e-10)이었다. 10개 이상 날짜가 있는 40행의 1,000회 날짜 재표집 CI도 별도 구현으로 재계산해 전부 일치했다. 날짜 블록 안의 event 가중 분자·분모를 함께 재표집하므로 point estimate와 다른 날짜 동일 가중 모수로 바뀌지 않는다. 10일 미만은 CI=None이다.
- 6개 fit 모두 train∩validation 및 (train∪validation)∩test event_id 중복 0. saved_pregame는 두 split 모두 train=189products/53events, validation=0, test=56products/17events이며 채점 가능한 것은 53products/16events다. weight=None이고 validation-selected/league/recent learned pool이 없는 동작은 맞다. train에서만 맞춘 temperature 후보가 남는 것은 validation-dependent pool을 학습했다는 뜻이 아니다.
- RESULTS-V1은 920 rosters/37 sessions 결과를 보존한다. 최종 결과는 915/35이며 입력 원파일 해시는 그대로다. source metadata에 의한 prepare filtering 변경이므로 입력 원파일 해시만으로 두 실행을 같은 것으로 취급하면 안 되고 코드 해시와 출력 버전을 같이 써야 한다.

## 보고서에 반드시 반영할 구체적 변화

metadata 제외 후 남은 날짜 집합에서 비율 split을 다시 계산하므로 단순히 같은 test에서 5경기만 제거한 비교가 아니다. 실제 strict 60/20/20 test 시작은 2026-08-15→2026-08-18, test 날짜 수는 23→22로 바뀌었다. strict 50/20/30은 2026-07-18→2026-07-19, 34→33으로 바뀌었다. 부모 부록이 결과를 본 뒤 보정했으며 새 사전등록/untouched 평가가 아니라고 명시한 것은 적절하다. V1↔최종 성과 차이를 제외된 경기의 효과만으로 설명해서는 안 된다. 이것은 최종 split 알고리즘의 산술 결함이 아니라 버전 간 비교 범위를 명확히 해야 하는 실제 변경이다.

paired CI는 후보별 고정9:1 대비 구간이며 전체 후보·리그·split을 동시에 보정한 구간은 아니다. 구현은 표시한 개별 구간과 일치한다. 이를 White Reality Check 또는 다중 비교 통과라고 부르면 안 된다. 현재 부록처럼 회고 탐색으로 남기는 한 새 결함으로 보지 않는다.

## 이전 지적의 현재 상태

1. 결과 실제 가용 시각 검증 부재: 남아 있다. 다만 protocol.label_timing이 이미 '48h는 보수적 가정이며 실제 결과 수신 시각 증명 아님'이라고 명시한다. 현재 실험 범위의 공개된 한계이므로 알려진 label 누수로 표현하지 않는다.
2. event 동일 가중 손실 대 product 수 sample gate/shrinkage: 코드상 남아 있다. 이번 회고 VOTES는 1event=1product이며 saved_pregame는 validation=0으로 pool/league shrinkage가 실행되지 않아 이번 최종 비교가 달라졌다는 증거는 없다.
3. 공통 표본: native metrics에는 결측 제거 경로가 남지만, 새 common_unique_vote_forecast 및 paired_forecast_vs_fixed90가 직접 비교용 경로를 제공한다. 이전 P2를 최종 paired 결과의 미해결 결함으로 제시하지 않는다.

---

# 이전 버전 검토 기록 (아래 수치·해시는 당시 버전)

# CALIBRATION 읽기 전용 검토

대상: scripts/two_leg_literature_research.py 및 .local-test/RESULTS.
최종 확인 코드/manifest SHA-256: 7dd04e3d74ce110305f175dccb5a9e51d71be608db44d58889612951cf640a7a.
검토 중 부모가 스크립트와 결과를 갱신해 최종 버전을 다시 확인했다. 적합 함수·전체 실행·ROI 재실행은 하지 않았다. 기존 JSON/gzip 집계와 vector/forecast_metrics의 작은 합성 입력만 실행했다. 이 파일 외 수정 없음.

## 판단

현재 저장 결과의 학습/test 경기 중복, 모델별 공통 평가 표본 불일치, log-loss/Brier 계산의 산술 오류는 확인하지 못했다. 아래 결함은 코드가 보장하지 않는 계약을 구체적으로 재현하거나 코드 경로로 확인한 것이다. 현재 결과를 실제로 오염시켰다는 증거가 없는 항목은 그렇게 명시한다. P1급 실제 누수나 현재 우세 순위를 뒤집는 오류를 발견했다고 보고하면 안 된다.

## 구체적인 수정 대상

### [P2] 결과 가용 시각 없이 킥오프 48시간을 학습 label embargo로 사용

위치: prepare 약 68/110행, run의 first_val/first_test 및 train/va 필터(최신 파일의 해당 이름으로 검색).

prepare는 hit/refund/result_status를 저장하지만 결과가 최초 가용해진 시각을 저장하지 않는다. run은 kickoff < 다음 단계 최초 available_at - 48h이면 target이 알려져 있다고 취급한다. 따라서 48시간 이상 지연 종료·정정·공식 결과 수신 지연이 있으면 학습 시점 이후에 알려진 target도 허용한다. 이것은 시간순 날짜 분할과 별개인 실제 contract 누락이다. normalized 입력 TIMED/VOTES에도 결과 최초 가용 시각이 없음을 확인했으므로 이번 자료로는 이를 사후 검증할 수도 없다.

수정 방향: result_available_at을 source에서 보존하고 각 학습 cutoff 전에 알려진 label만 허용한다. 값이 없으면 strict as-of 학습에서는 제외하거나 별도 unverified 회고 집단으로 분리한다. 48h 버퍼는 추가 보수 조건으로 유지할 수 있지만 label availability 증명을 대체하지 못한다.

영향 판정: 실제 지연 label이 있었는지는 미확인이다. 이번 실행을 settlement-time 누수 검증 완료라고 부르는 것은 부정확하다. retrospective 표시를 유지한 회고 성과 기록 자체를 무효라고 단정하지 않는다.

### [P2] 손실은 경기 동일 가중인데 최소 표본·리그 축소량은 product 수 사용

위치: loss의 counts[event_id], fit의 len(train)<30 / len(validation)<10, len(tr)/len(va) 및 n/(n+50), len(tr)/(len(tr)+100).

loss는 이벤트 내 각 product에 1/product_count를 줘 경기마다 총 가중치 1을 만든다. 반면 fit의 최소 표본 통과와 shrinkage 신뢰도는 같은 경기의 마켓 수만큼 늘어난다. 예를 들어 10경기×3마켓은 30으로 통과하지만 10경기×1마켓은 실패한다. 같은 경기 정보를 복제해도 최적화 목적함수는 같을 수 있는데 학습 가능 여부와 축소 강도가 달라지는 구조다. recent 가중치를 적용할 때도 표본 신뢰도는 recency 유효 표본과 무관한 원래 product 개수다.

현재 증거: saved_pregame train_n=189이지만 고유 경기는 53이다. 다른 회고 집단은 1경기=1product여서 현재 수치와 같다. saved_pregame validation_n=0이라 리그 축소는 실행되지 않았고, 고유 경기 53도 30을 넘으므로 이번 후보 선정이 이 결함 때문에 달라졌다는 증거는 없다.

수정 방향: raw market_n과 unique_event_n을 분리해 기록하고 최소 표본 및 shrinkage 기준은 unique event 또는 사전에 정의한 event-level effective sample size로 일치시킨다. 현재 실행을 재적합할 필요가 있는지는 부모가 이 기준 변경의 적용 시점을 정한 뒤 판단한다.

### [P2] forecast_metrics가 모델별 결측을 독립 제거해 공통 표본을 보장하지 않음

위치: forecast_metrics의 valid=[... if p is not None], operating_unique_vote_market_reference의 동률 제거.

함수는 모델마다 독립적으로 결측 예측을 빼고 event_count와 이벤트 내 product 가중치를 다시 계산한다. 한 모델에 마켓 하나가 빠지면 단순 표본 감소뿐 아니라 남은 마켓의 가중치도 달라진다. 그 상태의 accuracy/Brier/log-loss를 한 표에서 비교하면 모델 차이와 평가 모집단 차이가 섞인다.

합성 재현(적합 호출 없음): A는 target=0, B는 target=1, market=[.8,.2]; final은 A만 None, B는 [.8,.2]. market 결과는 2events, accuracy=50%, logloss=.916290731874155; saved_final_reference는 1event, accuracy=0%, logloss=1.6094379124341003. 동일 예측이라도 support 차이만으로 점수가 달라진다.

현재 증거: 현재 .local-test/RESULTS의 기존 모든 모델별 product_id support를 대조했으며 같은 cohort/split에서 차이 0이었다. 추가된 operating_unique_vote_market_reference도 현재 test의 동률은 0이다. 그러므로 지금 보고된 점수 차이를 이 결함으로 설명하면 안 된다. 이후 일부 final 누락 또는 투표 동률이 생기면 바로 발현한다.

수정 방향: native-coverage 지표는 유지하되 비교용 paired support를 명시적으로 고정하고 그 집합에서 모든 비교 모델의 event 가중치를 공통 계산한다. product/event support 해시 또는 ID 집합도 함께 남긴다.

## 확인해 통과한 사항과 정확한 해석

- 코드 SHA와 manifest SHA가 최종 버전에서 일치했다. 초기 검토 버전은 8daa59d5…였고 최종 결과는 7dd04e3d…이다. 서로 다른 버전의 결과를 섞어 결함을 주장하지 않았다.
- 6개 fit 모두 train∩validation 및 (train∪validation)∩test의 event_id 교집합은 0이었다. 날짜 분할 후 train은 first_val−48h, validation은 first_test−48h 전에 kickoff가 있는 자료만 남기는 경로다. 온도는 train 손실에서, pool weight는 validation 손실에서 선택한다. test target으로 T나 weight를 맞추는 경로는 없다.
- saved_pregame는 공통 유효 마켓 기준 4개 날짜뿐이다. 두 split 모두 train 189products/53events, validation 0, test 56products/17events이며 실제 채점은 53products/16events이다. 60/20/20과 50/20/30이 독립 재현이라는 주장은 틀리다. 다만 코드가 validation 부족에서 pool 후보를 None으로 반환하는 동작은 맞다. temperature 후보만 train으로 맞춰 평가하는 것은 그 자체로 test 누수가 아니다.
- 1/count(event_id) 가중 Brier와 log-loss는 채점 가능한 product들의 경기별 평균을 낸 뒤 경기 간 동일 가중 평균을 구한다. fit은 target=None을 loss 호출 전에 제거하므로 loss helper의 counts에 미채점 행이 들어갈 때 생길 수 있는 편향은 현재 fit 경로에서 발현하지 않는다.
- Brier는 클래스별 제곱오차의 합(sum-k)이다. 2-way/3-way를 같은 모델 간 동일 product support에서 비교하는 것은 유효하다. 서로 다른 cohort 또는 ALL↔league의 raw 점수를 단순 우열로 해석하면 평가 분포가 다르다. 현재 strict test의 2-way/3-way는 60/20/20=37/5, 50/20/30=65/11, saved_pregame=29/24이다.
- vector는 수치 범위·유한성·합 오차 .002 이내를 검사해 재정규화한다. 실제 준비된 데이터에서 동일 vector 길이의 market/external/final을 같은 options 순서로 구성한다. bool을 수치로 허용하고 문자열은 TypeError를 내지만 현재 자료에 그 타입 문제를 확인하지 못했으므로 주요 발견으로 올리지 않는다. roster의 길이와 합만으로 클래스 상호배타성을 증명할 수는 없으나, 현재 자료의 실제 잘못된 roster 사례는 발견하지 못했다.
- external_raw_direction_reference는 회고 집단에서 대부분 한 명의 투표로 생기는 one-hot 방향 참조다. 큰 log-loss(약 8–10)는 1e-8 floor를 적용한 결과이지 학습된 확률 모델의 보정 실패를 직접 측정한 값이 아니다. 현재 명칭/semantics에 reference가 표시돼 있다. log-loss의 max(p_y,1e-8)는 수치적으로 상한을 둔 손실로, 영확률을 포함한 전체 simplex에서 엄밀한 uncapped log-score와 같다고 해석하지 않는다.
- operating_unique_vote_market_reference는 시장 확률 벡터로 proper scores를 계산하고 투표 방향으로 accuracy를 계산한다. 현재 코드가 probability_semantics에 이를 명시한다. accuracy를 시장 argmax 정확도와 같은 종류의 확률 분류 정확도라고 부르지 않으면 계약 위반은 아니다. 현재 해당 모델의 Brier/log-loss가 시장과 같은 것은 예상되는 동작이다.
- 최종 결과에서 common_results_complete=False인 ticket session은 0이다. 다른 정책의 미정산 때문에 완료 정책까지 표본에서 탈락시키는 경로가 있긴 하지만 이번 자료에 영향을 준 결함이라고 보고하지 않는다.

## 부모에게 넘길 우선순위

이번 결과에서 실제 확인된 산술 오류나 train/test 중복은 없다. 먼저 결과 가용 시각 미검증을 보고서의 시간 검증 범위에 명확히 남긴다. 코드 보강은 event 단위 sample gate/shrinkage와 paired scoring support를 우선하면 된다. 위 세 항목의 현재 영향 범위를 넘어서 수익률 결과가 틀렸다고 단정하지 않는다. 논문 정정은 부모가 직접 검증했으므로 재조사하지 않았다.
