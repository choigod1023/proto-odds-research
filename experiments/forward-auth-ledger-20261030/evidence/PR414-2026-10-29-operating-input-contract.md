# 운영 원장 비교 입력 계약 (2026-10-29)

`src/operating_ledger_inputs.py`는 저장된 운영 예측의 정책·선택·당시 가격·시점을
읽기 전용으로 정규화하는 sidecar다. 후보 실행, 결과 수집, 정산 연결, ROI·손실·낙폭
계산, 서버·DB·배포 동작은 구현하지 않는다. 연구의 고정 9:1 정책이나 시장 기준을
현재 운영 투표 정책으로 간주하지 않는다. GitHub 모의 베팅 journal도 입력으로
추정하거나 전체 모델의 성능 원장으로 대체하지 않는다.

## 공개 API

```python
from operating_ledger_inputs import normalize_operating_prediction, latest_pre_cutoff

observation = normalize_operating_prediction(record)
report = latest_pre_cutoff(iter(records), cutoff_minutes=30)
selected = report["selected"]  # 제외 사유가 있는 최신 revision도 여기에 남는다.
usable = [item for item in selected if item["roi_input_eligible"]]
```

입력은 디코딩한 prediction/settlement 레코드의 iterable이다. `record_json`이 JSON
문자열 또는 mapping인 SQL export 행도 받는다. wrapper 컬럼으로 원장 내용을 덮어쓰지
않는다. 파일·DB·네트워크를 직접 열지 않으며 입력 객체를 변경하지 않는다.

단일 행 API는 정규화한 dictionary와 `exclusion_reasons`, `warnings`를 반환한다.
`roi_input_eligible`은 **비교 입력 계약을 통과했다는 뜻**이다. 정산 여부, 실제 구매,
수익성, 확률 보정, 해시체인 인증을 뜻하지 않는다. `result_used=false`,
`hash_chain_verified=false`를 명시한다. 호출자는 별도로 검증된 export를 공급해야 한다.

배치 API는 `selected`, `rejected`, `ignored_record_counts`를 반환한다. settlement와
기타 record type은 개수만 기록한다. 정산 여부나 결과로 예측 revision을 고르지 않는다.
일반적인 부적격 행은 예외 대신 원인을 남긴다. 잘못된 cutoff 인자는 `ValueError`다.

## 시각과 revision 선택

- 시각은 timezone이 있는 ISO-8601 또는 aware datetime이어야 한다. UTC로 정규화하며
  timezone이 없는 시각을 한국 시간 등으로 추정하지 않는다.
- 단일 행은 `market_observed_at <= as_of <= captured_at < kickoff`를 요구한다.
- 배치는 `as_of`, `captured_at`이 모두 `kickoff - cutoff_minutes`보다 **엄격히 이전**인
  후보에서 `(as_of, captured_at, ledger_sequence)`가 가장 큰 revision을 먼저 고른다.
  선택한 행의 `market_observed_at`도 cutoff보다 엄격히 이전이어야 한다.
- 최신 행의 잘못된 observed clock, 확률·배당·선택·모델 신원, 보류 상태 때문에
  더 오래된 정상/정산 revision으로 돌아가지 않는다. 경기 후 capture를 과거 as_of로
  소급해서 인정하지 않는다. cutoff 이후 실제 capture 행은 그 시점에 없던 자료이므로
  `rejected`에 남고, cutoff 전에 존재했던 revision을 대신 무효화하지는 않는다.
- event가 같지만 kickoff가 다르면 `conflicting_event_kickoff`다. 순위를 정할 수 없는
  kickoff/as_of/capture 행이 event에 있으면 `unorderable_event_revision`으로 해당 event의
  채택을 막는다. 같은 순위에서 서로 다른 내용이 발견되면 `ambiguous_latest_revision`이다.
  동일 행의 반복은 허용한다. 충돌한 동순위는 첫 대표 행을 남기되 채택하지 않는다.

## 반환 필드와 정책 신원

- `event_id`, `snapshot_id`, `decision_id`, `ledger_sequence`, `record_hash`와 네 시각.
- `fixture`: 저장된 sport/league/home/away/year/round/date/source_event_id/match_id.
  sport/league/home/away가 없으면 제외한다. 팀명·경기 ID를 결과나 외부 자료로 복원하지 않는다.
- `model_identity`: 저장된 `policy`, `model_version`, `model_artifact_hash`, `model_hash`,
  residual/pipeline version과 `feature_policy_version`, `feature_policy_status`.
  `model_metadata`에는 원래 모델 mapping을 보존한다.
- `model_identity_hash`와 `cohort_key`는 위 명시적 신원 mapping의 SHA-256이다.
  저장된 모델 artifact 또는 실행 코드의 해시가 아니다. 누락된 artifact/model hash는
  null과 경고로 남긴다. model hash의 root/nested 충돌과 artifact hash 충돌은 제외한다.
- `operating`: action/selection_id/offer_id/market/label/line/selection/odds/probability,
  probability_kind/basis/calibration_status.
- `same_market_offers`: 선택된 market/label/line과 정확히 일치하는 전체 저장 offer.
  `all_offers`는 전체 저장 roster다. 중복 offer/시장 내 중복 방향, selection-offer 쌍
  불일치, 저장 가격·label 등 불일치, 결측·비유한·1 이하 배당은 제외한다. 승패·승무패·
  언더오버에는 알려진 전체 방향도 요구한다. 그 외 시장은 두 개 이상의 저장 offer만
  확인하며 외부 상품 목록과의 완전성까지 입증하지 않는다.
- `input_revision_hash`, `batch_id`, `common_input_hash`는 실제 저장값만 보존한다.
  input_revision_hash를 공통 입력 해시로 바꾸거나 서로 다른 모델의 입력 동일성을
  추정하지 않는다. 후보마다 공유할 batch/input 계약은 부모 작업에서 공급해야 한다.
- `source_feature_cutoff_at`, `source_features`, `model_inputs`, `model_inputs_scope`.
  명시적 features.model_inputs가 없으면 저장된 score_inputs만 model_inputs에 담고
  범위를 `saved_score_inputs_only`로 밝힌다. 원장에 source cutoff가 없으면 null이다.
  as_of가 그 입력 전체의 독립 관측 증거인 것처럼 보충하지 않는다.
- `tipster_hybrid`와 `candidate_provenance`를 그대로 보존한다. saved/replayed 주장을
  인증하거나 후보 실행·재현 여부를 추정하지 않는다. 배치 선택에는 운영 원장 stream을
  넣어야 한다. 서로 다른 후보의 출력을 같은 event revision stream으로 섞으면 안 된다.

`model.operating_version`, `versions.operating_model`, 실제 적용된 hybrid.version이
불일치하면 제외한다. 단, hybrid가 **보류/market_fallback** 상태이면 operating_version이
시장 anchor인 것이 정상일 수 있다. 따라서 `feature_policy_version/status`를 cohort key에
포함한다. `tipster-vote-v1 / withheld`를 과거 시장 전용 코호트에 합치지 않는다.
모델의 selection_policy가 누락되면 null로 남기며 feature 정책으로 몰래 채우지 않는다.

투표 적용 행은 정책·모델·hybrid version, 선택 적용/확률 미적용 플래그, 운영 상태,
권한, 저장된 유효 정수 득표수의 단독 최다 득표 방향과 선택을 확인한다. 보류 행에는
`vote_policy_not_applied`, `no_operating_selection` 등을 남긴다. 과거 fixed9:1 모델은
원래 version과 feature policy 상태를 보존하며 현재 투표 정책으로 바꾸지 않는다.

## 확률과 결과 누출

확률이 null이면 당시 선택·배당을 보존해 ROI 입력으로 허용한다. `probability_kind=missing`,
`probability_missing_roi_only`로 표시한다. 시장·자체 모델·픽스터 지지율로 결측 확률을
채우지 않는다. 원장의 scalar probability와 구형 nested probability.final을 지원한다.
명시된 Shin 시장 근거는 `market_reference`, 근거가 없는 값은
`unclassified_recorded_probability`다. 보정 상태는 항상 `unverified`다.
현행 투표 행에 저장된 확률은 해당 offer의 market_probability와 맞아야 하며,
vote_share를 확률로 선언하면 제외한다. 정규화기는 확률의 보정 성능을 검사하지 않는다.

feature 안의 current result/outcome/winner/정산/최종 점수 형태, 종료 상태, as_of 이후
관측 시각은 제외한다. `pending`, `경기전` 같은 빈 결과 표시는 결과로 쓰지 않고 경고한다.
중첩 객체에 미확정 winner/점수가 담긴 경우도 제외한다. runtime이 저장하는
team_context.home_form/away_form/head_to_head는 과거 전적 문맥으로 보존한다.
이는 해당 전적의 진실성·시각이나 임의의 불투명 feature에 결과가 없다는 증명이 아니다.

예외는 `research_player_inputs`가 **입력 없는 unavailable 상태 메타데이터만** 담고
`affects_probability=false`, `training_eligible=false`인 경우다. 허용 키는 version,
observed_at,status,affects_probability,reason,training_eligible뿐이다. 이 표시의
as_of 이후 시각은 `non_input_metadata_after_as_of` 경고로 남긴다. 실제 입력 또는
결과 필드가 하나라도 붙으면 예외를 적용하지 않는다. invalid/naive 시각도 제외한다.
주 원장의 세 시각에 대한 엄격한 cutoff 규칙은 이 예외와 무관하게 그대로 적용한다.

## 실행 검증과 실제 로컬 export

합성 fixture도 실제 `build_decision_snapshot`, `ledger_features`, `prediction_payload`로
만들었다. 최다 득표가 시장 선호 방향을 뒤집는 경우, 실제 build로 생성한 최신 보류가
과거 투표 선택을 대체하는 경우, 가격 변경, 오래된 snapshot, 잘못된 offer/선택·중복,
T30 경계·경기 후 capture·timezone·확률 누락·미확정 결과 혼입을 Python으로 검사한다.

기본 테스트는 외부 데이터가 없어도 실행한다. 실제 export 검사는 다음과 같이 명시적으로
경로를 지정한다. 로컬 gzip을 읽기만 하며 DB/네트워크에 접속하지 않는다.

```powershell
$env:OPERATING_LEDGER_TEST_EXPORT='C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/operating-ledger-comparison-20261029/.local-test/production-ledger-20261029.jsonl.gz'
python -m pytest tests/test_operating_ledger_inputs.py tests/test_ai_decision.py tests/test_prediction_runtime.py tests/test_probability_validation_audit.py tests/test_prediction_ledger.py -q -p no:cacheprovider
```

최종 실행 결과: **104 passed, 9 subtests passed**. `git diff --check`도 통과했다.

2026-10-29에 부모가 제공하고 LF 경계를 수정한 export를 JSONL로 다시 읽었다.
8,674행(prediction 7,454 / settlement 1,220)이며 gzip SHA-256은
`481e288999cb1465f16fe0f222c8a38c936f62fd924226937658e1299cbbf910`이다.
초기 로컬 합성본의 행 경계 문제는 운영 원장/해시체인 손상을 뜻하지 않는다.
부모가 수행한 hash-chain 검증을 이 sidecar가 자체 검증했다고 보고하지 않는다.

- 실제 `tipster-vote-v1` 16 prediction / 10 event 모두 저장 offer roster·hybrid 입력으로
  운영 `fixed_tipster_hybrid.apply`를 실행해 정규화 선택·offer·배당·확률과 일치했다.
  이는 적용 단계의 parity이며 원래 수집기의 원시 HTTP 입력 전체를 재현한 검사는 아니다.
- 현재 정책 보류 143 prediction은 operating_version이 `shin-market-anchor-v1`이어도
  `tipster-vote-v1 / withheld` 문맥을 보존하고 시장 전용 코호트와 분리했다.
- 전체 자료에서 T30 이전 최신 revision을 고른 event는 2,639개, 입력 적격은 257개다.
  제외 사유별 건수는 겹칠 수 있다. 예를 들어 fixture 신원 누락은 2,238개 event,
  실제 player-input 관측이 as_of 이후인 경우는 120개 event다.
- 현행 투표 16개 단일 행 중 15개가 입력 계약을 통과했다. 나머지 하나는 실제
  research_player_inputs 관측이 as_of 이후여서 제외했다. 최종 event 선택에서는
  투표 적용 9개가 적격이고, 다른 1개 event는 **더 최신 보류 revision**이므로 이전
  투표 선택을 다시 채택하지 않는다. 이 수치들은 정산/성과 표본 수가 아니다.

별도 과거 검사본 `roi-lineage-audit-20261018/.local-test/production-ledger-20261018.jsonl.gz`
도 읽기만 했다. 6,999행(6,145 prediction / 854 settlement), T30 이전 최신 2,238 event
전부 fixture 신원이 없어 입력 부적격이었다. 이것을 최신 운영 상태로 해석하지 않는다.

후보 동시 기록과 공식 정산을 연결하는 부모 작업이 남아 있다. existing per_event_shadow의
종료 상태나 후보 부재를 이 모듈이 보완했다고 주장하지 않는다. 이 코드의 commit은
운영 채택·배포·손실 개선이나 수익성 증거가 아니다.
