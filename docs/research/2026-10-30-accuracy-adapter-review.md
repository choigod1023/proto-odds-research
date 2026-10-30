# #436 선수 입력 브리지 후속 독립 검토

2026-10-30. 검토 대상 #436 head `45a2197abebf7a0c577c981e7020419c965b02c6`, 구조 비교 #438 head `a079e5136fc2b75a0f72b4dec36af3f47e825790`. 코드·기존 테스트를 해당 Git 객체에서 독립 복제해 검사했고, 정확한 바이트 해시는 `source-hashes.json`에 남겼다. 두 PR이나 운영 코드는 수정하지 않았다.

## 확인된 동작

- 합성 core에서 full-game win과 다른 기간·시장, 미등록 팀 매핑, 판정 뒤 배당 수신, 미래 상태 가용 시각은 verifier가 항상 True여도 `infer`가 거부했다. bridge는 공식 경기 ID가 문서 alias와 다른 계약을 거부한다. 테스트한 사례에 대한 판단이지 모든 core 조건을 다 확인했다는 뜻은 아니다.
- 라인업 팀·선수 ID 및 이닝 결측 등 선수자료만 무효인 경우 입력을 폐기하고 `lineup_invalid`를 기록하며 라인업 없는 경우와 같은 baseline 확률을 반환했다. 유효 선수자료도 아직 선수 효과를 학습하지 않았으므로 baseline과 같다.
- `lambda *_: True`는 함수가 요구하는 callable 조건을 충족해 통과하지만, 반환 봉투에 외부 수신 인증 true 또는 별도의 verified=true 필드를 새로 만들지는 않았다. 원래 quote의 `external_receipt_verified`는 false로 남는다. 호출 성공은 독립 인증의 증거가 아니다. 무조건 True 함수는 문서에서 요구하는 실제 신뢰 검증을 하지 않으므로 운영 검증기로 사용하면 안 된다.

## 남은 경계와 반례

1. **결정 시각은 검증기에 묶이지 않는다.** `adapter.infer`는 verifier에 `contract`와 state만 전달하며 contract에는 `decision_at`이 없다. 같은 계약·고정 스냅샷 검증기로 09:00과 09:30의 서로 다른 decision이 모두 통과하는 합성 반례를 재현했다. 각각의 시각은 배당 수신 이후 및 T-30 이전이고 어느 쪽이 실제 동결 시각인지 검증하지 않았다. verifier에 정확한 결정 시각과 판정/동결 증거를 포함시키고, quote·state·identity와 함께 바인딩하는 설계가 필요하다. 재현은 과거 445건의 decision이 잘못됐다는 증거가 아니다.
2. **팀 매핑 및 상태 provenance는 진짜 독립 검증기에 의존한다.** 기존 이름 두 개를 서로 뒤바꾼 매핑은 상태에 두 이름이 모두 있어 자체 검사에 통과하고, 무조건 True verifier에서는 예측 확률이 바뀌었다. 고정된 신뢰 계약과 대조하는 테스트 verifier는 변경을 거부했다. `state_available_through`도 조건에 맞는 다른 시각으로 바꾸면 True verifier에서는 받아들인다. state hash와 clock은 내용·시간의 자기 일관성을 검사하지만 해당 학습 결과가 실제 언제 수신됐는지 증명하지 못한다. 외부의 변조 불가능한 공식 팀 alias와 상태별 학습·결과 수신 이력 증거가 필요하다.
3. **경기 ID도 단독 infer에서는 신뢰 계약에 의존한다.** request와 contract의 공식 경기 ID를 함께 변경하면 True verifier에서 단독 infer는 수용한다. 검토한 bridge는 문서의 기존 official alias와 대조해 해당 변경을 거부한다. 이 비교가 두 자료의 독립적인 진실성을 인증하지는 않는다. 최초 테스트에서 단독 infer가 자체적으로 이를 거부할 것으로 잘못 가정해 1개 실패했으며, 경계를 분리해 수정한 최종 테스트는 통과했다.
4. **구조 v1.1과 bridge 제공 v1은 다르다.** #436에 포함된 `vendor_structure_v1`의 schema/validator/fixture와 #438 최신의 세 파일은 모두 바이트가 다르다. 전자는 `schema_version=v1`, 후자는 `v1.1`이며, 실제 이전 schema의 `shape`는 `v1.1`을 거부했다. 최신 구조는 참조 자료 내용과 경기 identity 해시를 포함하는 방향으로 변경 중이다. bridge는 전체 freeze를 인증하지 않고 caller 제공 validator로 quote core만 확인한다. 최신 구조에 자동 호환된다고 주장할 수 없다. 총괄의 계약 선택, 별도 변환과 실제 validator·독립 신뢰 스냅샷 연결 후 다시 검증해야 한다.

## 범위

합성 Python fixture와 저장 코드만 시험했다. 인증된 수신, 원문 HTTP, 실전 가격, 실제 선수·팀 매핑, 전체 학습 상태 provenance 또는 배포를 입증하지 않았다. 새로운 성과 후보를 만들거나 적중률·ROI를 평가하지 않았다.
