# 동일 스냅샷 모델 비교 자동 기록

## 범위와 구현

`src/operating_model_capture.py`는 원장의 새 prediction에 연구용 비교 arm을
함께 저장하는 순수 계산 모듈이다. 실제 통합 지점의 함수명은
`prediction_runtime.ledger_features(game)`다. 기존 `_safe_json` 변환이 끝난
features와 `game['decision_snapshot']`을 전달해 `features.model_comparison`을
붙인다. 단건·배치 저장 경로 모두 이 함수를 사용한다.

추천 선택·운영 확률·티켓·손실 한도·기존 실험 상태를 변경하지 않는다.
DB·네트워크·현재 시각 조회·외부 모델 실행·재학습·별도 cron이 없다.
`per_event_shadow`를 재개하거나 과거 원장을 수정하지 않는다.
이 구현은 매출판 입력의 비교 기록이며 성과 개선이나 모델 승격이 아니다.

## API와 기록 구분

```python
from operating_model_capture import capture, recipe

# 운영 경로: 이미 저장할 새 prediction의 스냅샷과 JSON-safe features
paired = capture(snapshot, features)

# 과거 evaluator: 원본 feature 계약을 사용하며 전향 기록과 명시적으로 구분
control = capture(
    original_snapshot,
    normalized['source_features'],
    capture_kind='retrospective_same_snapshot_control',
)
```

시그니처는 `capture(snapshot, features, *, capture_kind='prospective_same_snapshot')`다.
기본 모드는 새 원장 저장 경로용이다. 과거 재생에는 반드시
`retrospective_same_snapshot_control`을 지정한다. 알 수 없는 모드는
`unavailable / unsupported_capture_kind`다. 재구성 시각이 명시된 snapshot은
전향 모드에서 거부한다. 함수 자체에는 현재 시각이 없어 호출자가 전달한
자료가 실제 새 자료인지 독립적으로 증명할 수 없다.

snapshot에서 `decision_id`, `as_of`, `selection_id`, `offer_id`,
`probability.final`, `model`, `schema_version`, 선택적으로 `audit.built_at`과
`audit.reconstructed_at`을 읽는다. 옵션의 market/label은 선택·오퍼 ID가
동시에 일치하는 저장 features에서 읽으며, snapshot에도 해당 필드가 있으면
일치해야 한다. features는 기존 원장의 `fixture`, `options`, `tipster_hybrid`,
`score_inputs` 계약을 사용한다. 원본 인수를 수정하지 않으며 반환값도 분리한다.

`model_comparison` 공통 필드:

- `record_version = operating-model-comparison-v1`, `capture_kind`.
- `status = captured | unavailable`, `reason`.
- `affects_selection = false`, `affects_probability = false`.
- `prediction_as_of`, `source_generated_at`, `source_version`.
- `timing_source = outer_ledger`,
  `eligibility = requires_outer_ledger_timing_audit`.
- `implementation_hash`, `input_batch_hash`, `price_roster_hash`, `roster_size`.
- `arms`: 아래 다섯 arm을 키로 갖는 객체.

arm 공통 필드는 `status = available | abstain | unavailable`, `reason`,
`role`, `recipe_id`, `recipe_hash`다. 계산할 수 있는 arm에는
`selection_id`, `offer_id`, `market`, `label`, `selection`, `odds`,
`probability`, `probability_basis`를 저장한다. `meta`에는 필요한 입력 출처와
정책·artifact 한계를 기록한다. unavailable에는 임의 선택·성공·확률을 만들지 않는다.
공통 필드는 실패 단계에 따라 없을 수 있으므로 소비자는 먼저 status를 읽는다.

## 고정 recipe

- `operating`: snapshot의 실제 선택·오퍼·최종 확률을 보존하고 가격은 같은
  옵션에서 읽는다. 선택 없음이나 withhold는 abstain이다. `selection_policy`
  (없으면 feature의 tipster version)와 `operating_version`을 별도로 보존한다.
  무투표에서 tipster 정책과 `shin-market-anchor-v1`이 함께 있어도 시장 매수로
  바꾸지 않는다. 현행 투표 정책의 확률은 시장 기준이며 투표 지지율이 아니다.
- `market_reference`: 같은 전체 경기 roster에서 저장된 market_probability 최대값.
  운영 정책이 아니라 비교 control이다.
- `fixed91_same_electorate`: 같은 시점의 저장된 확률과 유효 vote_shares로
  `0.9 * market + 0.1 * vote`를 계산한다. 현재 투표군을 고정한 비교이며,
  과거 `market90-tipster10-v2` 배포 전체 재현이 아니다. authors > 0과
  applied 또는 실제 투표 동률의 withheld 상태, 유효한 share·관측 시각을 요구한다.
  무투표를 market fallback이나 home_vote_share 추정치로 대체하지 않는다.
- `saved_score_reference`: 전체 roster의 score_model_probability가 모두
  유효할 때만 최대값을 선택한다. `artifact_identity=unverified`,
  `training_evidence=unverified`, `promoted=false`로 출처 한계를 명시한다.
  새 MLB lineup/KBO dynamic 학습 artifact의 실행 결과로 해석하지 않는다.
- `home_always`: 홈 선택 null control. 확률은 `None`, basis는 `not_applicable`이다.

최대값의 정확한 동률은 selection_id 오름차순으로 해결한다. full-game 옵션은
빈 label, line=None인 단일 `승패` 2방향 또는 `승무패` 3방향만 허용한다.
홈/원정 표기와 기존 승/패 표기를 읽고, 누락·중복 selection/offer/방향·복수 시장을
거부한다. 가격은 유한한 숫자 > 1, 시장 확률은 0 < p < 1이어야 한다.
저장 시장·스코어 벡터 합 허용 오차는 0.0002, 투표 비율은 0.000002다.
결측·범위 밖·비정상 합계를 재정규화하지 않는다. 한 arm의 확률 결측은 다른
독립 arm을 추정치로 채우지 않는다. 비정상 roster는 전체 비교를 unavailable로 한다.

## 해시·시각·비용 계약

`recipe(arm)`은 수정해도 원본 catalog에 영향이 없는 frozen recipe 사본을 준다.
recipe_hash는 UTF-8, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
allow_nan=False로 직렬화한 recipe의 SHA-256이다. 구현 hash는 모듈 import 시 한 번 읽은
모듈 소스의 SHA-256이다(CRLF는 LF로 정규화). Linux 운영 Python과 Windows 로컬
Python의 bytecode 차이를 모델 변경으로 오인하지 않도록 했다. 소스를 읽을 수 없으면
부가 비교만 unavailable로 반환한다. 경기별 파일 조회는 없으며 학습 artifact hash로 주장하지 않는다.

price_roster_hash는 selection_id 정렬 순서의 전체 경기 선택·오퍼·시장·label·방향·가격을
묶는다. input_batch_hash는 이 roster와 저장 시장·스코어 확률, compact fixture,
원본 decision identity, 운영 정책·확률, 투표군 meta와 score_inputs를 묶는다.
기존 model_comparison, 선수 문서, evidence 원문, 전체 feature 문서는 재해시하지 않는다.
원본의 완전한 features와 ledger hash chain은 바깥 원장에 그대로 남는다.
모든 arm은 공통 hash와 바깥 원장의 동일 관측·캡처 시각을 공유한다.

source_generated_at은 snapshot.audit.built_at을 그대로 보존하며 없으면 None이다.
captured_at, market_observed_at, kickoff를 새로 생성하지 않는다. timezone이 없는
시각도 추정하지 않는다. 경기 이후 생성된 원본을 순수 함수만으로 판별할 수 없으므로
후처리 auditor가 outer ledger의 kickoff/captured_at/market_observed_at과
source_generated_at을 확인해야 한다. 특히 기존 normalizer의 시각 필드 목록만으로
이 새 필드가 검사된다고 가정하면 안 된다. captured 상태는 사전 입력 적격 판정이 아니다.

기존 입력 변경 signature는 파생 model_comparison 필드를 제외한다. 실제 입력은
원래 features에 이미 들어 있으므로 입력·가격·투표·스코어가 바뀌면 기존 경로대로
새 revision을 저장한다. 비교 코드 추가나 시각만 바뀐 재시도로는 과거 원장을
backfill하지 않는다. 같은 decision의 immutable rewrite도 하지 않는다.

옵션은 한 번만 순회하고 최대 3개 roster와 작은 벡터만 보관한다. 전체 roster나
설명 문장을 arm마다 반복 저장하지 않는다. UTF-8 compact payload는 8,192 bytes를
넘으면 unavailable로 반환한다. 비정상 입력이나 예상 밖 예외도 지원 불가 사유를
반환해 선택·확률 계산 경로에 예외를 전파하지 않는다.

## 실행 근거와 한계

읽기 전용 원본은 부모 worktree의
`.local-test/production-ledger-20261029.jsonl.gz`다. 파일만 읽고 JSONL 8,674행을
파싱해 record_hash, previous_record_hash, ledger_sequence를 전부 재검증했다.
prediction 7,454행, settlement 1,220행이며 마지막 hash는
`3b2d473e792cfffa80304749d0af0850452ced2ece7c4e208da9371e024247bf`다.
operating_version이 tipster-vote-v1인 prediction은 16개·10개 event이고
정산 연결은 4개다. selection_policy가 같은 행은 보류까지 포함해 159개다.
이 수치는 최신 기회별 정규화 표본이나 실제 구매·수익을 뜻하지 않는다.
이 worker는 match_results나 DB에 접속하지 않았고 ROI를 재계산하지 않았다.

Python 3.14.3 예제 측정에서 5-arm payload는 3,038 bytes,
tracemalloc peak는 21,100 bytes였다. score는 결측이라 unavailable이며
나머지 4개 arm이 available인 2방향 fixture다. 이 측정은 전체 운영 프로세스
RSS나 대량 판정 처리량 측정이 아니다. 10,000개 무관 옵션과 큰 미사용 문맥을
붙인 회귀에서도 단일 옵션 순회, payload < 8KB, peak < 256KB를 확인한다.

검증 명령:

```powershell
$env:PROODD_DB_PATH=$null
python -X utf8 -m pytest -q tests/test_operating_model_capture.py tests/test_prediction_runtime.py tests/test_tipster_vote_selection.py tests/test_fixed_tipster_hybrid.py tests/test_prediction_ledger.py -k 'not loader_uses_time_bounds and not new_votes_refresh and not sql_since_bounds and not production_ledger_reads_and_writes_only_sqlite and not verified_session_reads_once_and_reverifies_after_unlock'
```

112 passed, 6 deselected. DB 경로 및 loader 통합 검사는 실행하지 않았다.
기존 JSONL 원장·운영 snapshot·투표 정책 회귀를 포함한다. 새 회귀는 실제
build_decision_snapshot의 선택·확률·decision_id 불변, 단건/배치 append와
해시 재계산·오염 탐지, 기존 행 backfill 방지, 2/3방향·동률·무투표,
잘못된 확률·가격·오퍼·시각, 원본/반환값 불변성, 반복 입력 결정성,
전향/회고 구분, 저장·메모리 상한을 확인한다.

부모 HEAD `14078057`의 normalizer가 보존하는 source_features와 원본 options
계약을 읽어 호환성을 확인했다. worker는 독립 모듈이라 normalizer를 import하지
않으며, 부모 evaluator와 recipe 정렬 및 source_generated_at 적격 판정은
부모 통합에서 확인해야 한다. push·PR·merge·배포는 부모가 맡는다.

부모 통합에서는 DB를 제외하지 않은 전체 테스트와 실제 원장 입력 감사까지 실행했다.
최종 로컬 결과는 1,690 passed, 5 skipped, 93 subtests passed다. 구현 식별자는
bytecode 대신 줄바꿈을 정규화한 소스 해시로 보완했고, 비교기는 과거 replay와 미래
저장 arm을 구분하며 레시피 변경 시 미래 선택을 소급 재계산하지 않는다.

참조: `docs/PRODUCTION_CONTRACT.md`,
`docs/research/2026-10-25-research-scope-correction.md`,
부모의 `docs/research/2026-10-29-operating-input-contract.md`.
