# 최종 동결 코드·게시 주장 시각 민감도 감사

동결 SHA256 `92df250c9bb7e16f8ad187d0ad7a8ef2afb572816e7699e432bdd2389fd83ec5`가 현재 엔진 및 primary/CLAIMED 양쪽 manifest와 일치한다. 아래 감사 이력보다 이 절과 최신 final-review-audit.json이 우선한다.

primary 915 rosters(VOTES 592 + TIMED 323)·35 sessions와 기존 투표 재구성·시장확률·유일 방향·해시 별칭 합성 검증을 현재 코드에서 다시 통과했다. source_sha256 동일 문서는 1표, 상충 문서는 제외된다. 현재 baseline은 유일 투표 방향을 선택하고 시장확률을 유지한다. 원시 표 EV 티켓은 없고 forced2 진단만 허용한다.

claimed_time_frames를 원천에서 독립 구현한 필터와 비교했다. 시간대가 명시된 게시 주장 <= kickoff-30분만 남기며 552경기 전부에서 추천 행과 available_at=max(원래 가상 가용 시각, 남은 게시 주장 최댓값)이 일치했다. 미상 게시 시각 44개·T-30 이후 8개 추천을 제외하고 45경기가 모든 표를 잃는다. primary의 경기 시작 이후 6개 제외 필터와 CLAIMED의 T-30 필터는 다른 조건이다.

합성 입력의 필드 누락·None·빈 문자열·날짜만·시간대 없음·잘못된 문자열·숫자를 모두 예외 없이 제외하는 것을 확인했다. T-30 정확히 일치는 허용, 1초 뒤는 제외된다. 원래 available_at이 게시 주장보다 늦으면 원래 값을 유지한다. None은 dt의 문자열 검사에서 ValueError로 바뀌고 claimed 함수가 이를 처리한다.

별도 .local-test/CLAIMED/results의 552 rosters·29 sessions, normalized frame의 가용 시각과 재구성 벡터, 참고선 선택 다리의 유일 방향·시장확률, 원시 표 forced2 제한도 검증했다. CLI는 --claimed-time-only를 제공하고 두 결과 manifest가 민감도 적용 여부를 구분한다. 이 민감도는 VOTES만 포함하고 TIMED를 제외하므로 primary 전체 915와의 직접 동일 표본 비교가 아니다.

게시자 주장 시각을 지킨 것은 실제 당시 HTTP 수신·내용 동결·해당 시점 배당의 입증이 아니다. CLAIMED도 availability_proven=false이며 strict-asof 학습·결과 수신 시각은 미인증이다. 수익 또는 실전 우위의 새 증거로 표현하지 않는다. 부모의 홀드아웃 결과 변조 재실행은 이 감사 범위 밖이다.

재현 명령: python .local-test/TIPSTER/source_time_audit.py. 이 스크립트가 primary 감사 후 source-time 검증을 수행하고 final-review-audit.json을 함께 갱신한다. 학습·ROI 탐색·원천/엔진/결과 수정은 실행하지 않았다.

---

# 최신 코드 재검증: 2026-10-30

현재 엔진 SHA256은 `2257c9a059a45f3625ffd26499c3f33b15c0e054b7a2fa9e6c681a1333b3682d`이며 RESULTS manifest와 일치한다. 아래 이전 감사 중 source_sha256 미지원 지적은 현재 코드에서 해결됐다. 이전 버전의 판정은 이력으로 보존한다.

- source_sha256이 같은 두 작성자·서로 다른 URL 합성 입력은 **1표**로 집계된다. 같은 문서의 상충 방향 합성 입력은 **0표**로 제외된다.
- 시장 [0.7,0.3]·표 [0,1] 합성 입력에서 참고선은 투표 방향을 선택하며 확률은 시장 [0.7,0.3]을 유지한다. 표 [0.5,0.5]는 참고선 평가에서 제외된다.
- 실제 592개 원천 재구성 벡터, 참고선 예측 151개 시장 벡터, 티켓 다리 62개의 유일 방향·선택 시장확률이 다시 모두 일치했다. 전체 915 rosters·35 sessions도 확인했다.
- forecast 및 common_unique_vote_forecast의 support_sha256 **496개**를 저장된 예측의 product_id에서 독립 재계산해 전부 일치했다. 운영 참고선 20개 집계 행의 forecast/common/paired 표본 수가 같다.
- TIMED 323개 roster의 final_p가 모두 존재한다. prepare의 TIMED final vector 완전성 요구를 확인했다.
- 저장된 fit 6개×train/recent×훈련/검증의 고유 event_id 수 메타데이터 **24개**를 검증했다. 최소 훈련·검증 표본 및 리그 축소 가중치가 roster 수가 아닌 고유 event 수를 쓰는 코드도 확인했다. 학습은 다시 실행하지 않았다.
- manifest와 fits 모두 result_receipt_times_verified=false, strict_asof_training_certified=false를 명시한다. 가정한 48시간 embargo를 실제 결과 수신 증거로 표현하지 않는다.

읽기 전용 최종 감사 범위에서는 추가 불일치를 찾지 못했다. 작성자별 최초 관측 미재현, 과거 가용성 미입증, 원시 지지율과 확률의 구분은 여전히 유효한 제한이다. 부모의 홀드아웃 결과 변조 재실행은 이번 감사에 포함하지 않았으며 통과했다고 주장하지 않는다. 원천·엔진·RESULTS 수정 및 ROI 재계산 없이 final_review_audit.py와 final-review-audit.json을 현재 코드로 갱신했다.

---

# TIPSTER 최종 입력·현재 계약 참고선 재감사

2026-10-30. 엔진 SHA256 `15b699de71c8a07a11ce8266d3186490d08341f245c8cd777dc05037981e8483`와 RESULTS manifest가 일치하는 버전을 검토했다. 이전 review.md는 V1 감사로 그대로 보존한다. 이번 검토는 읽기 전용이며 원천·엔진·RESULTS를 수정하지 않았고 모델 학습이나 ROI 탐색을 실행하지 않았다.

## 판정

현재 자료에 대한 입력 재구성과 운영 참고선의 방향·확률 매핑은 독립 Python 검증을 통과했다. 다만 운영 적격을 입증한 것은 아니며, 일반적인 문서/작성자 중복 제거까지 구현했다는 설명은 좁혀야 한다. 915 rosters와 35 sessions는 파일에서 직접 확인했다.

## 직접 검증한 항목

- 원천 635개 추천에서 명시적 timezone-aware 발행 주장 >= kickoff인 6개를 승패를 보지 않고 제외하면 629개가 남는다. TonysPicks 5개와 golfmasterj 1개다. 5경기가 표를 모두 잃어 597→592 VOTES rosters가 된다. 동일 문서 Tony Tellez/Staff 2개 표도 이 6개에 포함되어 먼저 제외된다. 따라서 최종 duplicate_document_forecasts_excluded=0은 이전에 찾은 중복 사례와 모순되지 않는다.
- 592개 벡터와 voters를 raw_forecasts에서 별도로 재구성해 저장된 external_p와 전부 일치함을 assertion으로 확인했다. source별 생존 추천은 Pickswise 105, Betblog 21, TonysPicks 85, Miky938 Tips 28, golfmasterj 103, guidesyj 175, sportsanalyst1 106, Covers 6이다. 원래 저장된 vote_support를 그대로 사용하지 않는다.
- strict 503경기 중 유일 투표 방향 487·동률 16, NBA 추가군 89경기 중 유일 방향 88·동률 1이다. 전체 유일 방향 575·동률 17이다. 유일 방향과 재구성 표 기반 9:1 방향이 다른 경기는 139개(strict 97, 추가군 42)다. 이전 감사의 580·142는 V1 수치이며 수정본 결과와 혼용하면 안 된다.
- heldout-predictions의 operating_unique_vote_market_reference 예측 151개는 모두 VOTES에만 존재하며 p가 해당 market_p와 정확히 같다. 선택된 해당 참고선 티켓 다리 62개는 모두 유일 최다 투표 방향이고 probability는 그 방향의 market_p다. 시장 argmax로 투표 방향을 덮어쓰지 않는다.
- 결과의 운영 참고선 20개 집계 행에서 forecast, common_unique_vote_forecast, paired_forecast_vs_fixed90의 markets가 같았다. 모든 VOTES 모델 집계에서도 common-unique와 paired 비교 건수가 다르게 나온 행은 없었다. 이는 현재 결과의 검증이며, 일반적으로 paired 함수가 모든 모델을 유일 투표 부분집합으로 제한한다는 뜻은 아니다. paired 함수의 동률 제외는 운영 참고선일 때만 적용되므로 미래 시험 표본에 동률이 들어가면 common-unique 필드끼리 비교해야 한다.
- VOTES의 raw vote 티켓은 forced2만 있고 conditional EV 티켓은 없다. raw vote의 log loss/Brier가 출력되는 것은 원시 지지율 진단이며 검증된 확률 품질 또는 운영 확률 성능으로 소개하면 안 된다. 원시 표 진단은 현재도 argmax 동률을 풀 수 있으나 운영 참고선은 동률 보류로 구현되어 서로 다른 의미다.
- alias 선택은 문자열 정렬에서 (연도,회차,int(game_no),시장/라인) 숫자 정렬로 바뀌었다. 16개 뒤 alias 생략은 고정 발매 좌표 기준이며 성과로 고르지 않는다. 이것이 실제 당시 최초 구매 가능 가격을 입증하지는 않는다.
- 전체 915 rosters = VOTES 592 + TIMED 323이다. TIMED 외부 벡터는 saved_score_shadow이며 실제 작성자 표가 아니다. TIMED의 9:1은 시장+저장 점수 대조군이며, VOTES의 9:1만 실제 표 기반이다. TIMED에는 전체 승패 외 마켓도 있어 현재 전체 경기 투표 계약의 재생으로 합칠 수 없다.

## 남은 구현 범위와 설명상 제한

1. admissible_votes의 키는 document_sha256 또는 (source,author,source_url)다. 원천 385개 행이 사용하는 source_sha256은 읽지 않는다. 같은 source_sha256과 다른 작성자/URL을 넣는 합성 입력을 직접 실행하면 2표로 남는다. 현재 생존 경기 안에는 source_sha256이 반복된 사례가 없으므로 이번 592개 결과를 바꾸는 확인된 오류는 아니다. 그래도 ‘모든 문서 해시를 사용한 중복 제거’라는 일반적 보장은 성립하지 않는다. source_sha256의 실제 문서 의미를 확인해 지원하거나, 지원한 키 범위와 이번 표본에 대한 영향 0을 명시해야 한다.
2. 이 함수는 문서 단위 상충 방향은 제외하지만, 같은 작성자가 서로 다른 문서에 남긴 여러 표의 최초 관측을 선택하거나 상충 표를 작성자 수준에서 처리하지 않는다. 현재 생존 경기에는 동일 (source,author) 반복이 없음을 별도로 확인했다. 따라서 현 표본에서는 영향이 없지만 운영의 ‘작성자별 최초 관측 한 표’ 수집기를 재현했다고 부를 수 없다. 작성자와 문서를 동일한 독립 단위로 설명하지 않는다.
3. 명시적 늦은 게시 주장 제거는 타당한 최소 필터다. 그러나 나머지 629개도 사후 수집이며 44개의 시각 미입증 자료가 포함된다. published<kickoff는 당시 내용 동결이나 실제 T-30 수신 증거가 아니다. 현재 계약의 실전 적격 VOTES 경기 수는 여전히 0이다. 현재 참고선은 ‘회고 투표 방향+시장확률’이며 조건부 2폴·연구 손실 제한 OFF는 운영 단폴·월 한도와 별개다.
4. 시장확률 참고선의 Brier/log loss는 시장 벡터를 평가한다. 투표 방향 accuracy와 같은 확률 모델의 argmax accuracy가 아니며 이 구분은 probability_semantics에 표시되어 있다. 동일 표본에서 시장과 참고선의 확률 지표가 같아도 투표가 시장확률을 개선했다는 결과가 아니다.
5. metadata 필터가 훈련·검증·시험 표본과 날짜 분할을 다시 바꿀 수 있으므로 V1 대비 차이를 오직 새 참고선 때문이라고 설명하지 않는다. 프로토콜의 audit_addendum 및 metadata_quality_addendum은 V1 결과를 이미 본 뒤 바꿨음을 명시하고 RESULTS-V1 보존도 확인했다. 새 사전등록 또는 새로운 미노출 holdout으로 부르지 않는 표시가 적절하다.

## 재현 근거

final_review_audit.py / final-review-audit.json에 원천별 생존 수, 제외된 6개 추천의 경기·작성자, 벡터·선택·확률·동일 표본 assertion과 코드 해시를 저장했다. 파일 개수와 선택 방향만 검증했고 승패에 따른 정확도·수익 또는 ROI를 새로 계산하지 않았다. parent의 수정된 결과를 검토한 시점의 판정이며 후속 엔진 변경에는 재검증이 필요하다.
