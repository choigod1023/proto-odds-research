# 실시간 예측 입력 관측 계약 v1

`src/realtime_observation_store.py`는 외부 수집기나 기존 운영 경로를 연결하지 않는 공통 관측·로컬 SQLite 저장 API다. 라인업·부상·교체·배당의 수정본을 보존하고, 특정 시각의 예측 입력과 재계산 신호를 만든다. 모델의 확률 계산·재학습·자동 수집·배포는 포함하지 않는다. 기존 `src/prediction_ledger.py`의 경기 전 동결 원장, `live_market_refresh.py`, 모의 지갑 및 운영 DB는 변경하지 않았다.

## 레코드 계약

`normalize(observation, raw)`는 아래 필드를 엄격 검증하고 원문 바이트의 SHA256을 대조한다. `tests/test_realtime_observation_store.py`의 `event()`는 실행 가능한 예시다.

- `schema_version=prediction-observation-v1`, `provider`, `provider_game_id`, `canonical_game_id`, `provider_event_id`, `stream_id`, `provider_revision_id`, 음이 아닌 정수 `sequence`. `(provider, provider_game_id, kind, stream_id)`가 스트림이며 그 안에서 이벤트 식별·경기 ID·킥오프는 고정, revision ID와 sequence는 각각 유일하다. provider의 실제 단조 revision 순서가 확인되지 않으면 이 계약에 임의 순서를 만들어 넣지 않는다. `supersedes_revision_id`는 선택 항목이며 명시 시 이미 저장된 같은 스트림의 낮은 sequence를 가리킨다.
- `kind`: `odds`, `lineup`, `injury`, `substitution`, `settlement`. `phase`: `pregame` 또는 `inplay`. `kickoff_at`, `received_at`, `source_published_at_claim`은 시간대가 있는 시각이며 발표 시각은 null 가능하고 수신 뒤라고 주장하면 거부한다. 첫 append의 `received_at`이 해당 수정본의 최초 수신 시각이다. 중복 재수신은 그 시각을 바꾸지 않는다. 소스가 제공하는 시각 주장은 실제 수신 증명이 아니다.
- `source_url`, 실제 `raw` 바이트, `source_sha256`, `external_receipt_verified=false`. 현재 외부 신뢰 정책과 서명 검증은 없으므로 true는 거부한다. raw는 SQLite BLOB으로 보존하며 `raw_revision()`은 읽을 때 해시를 다시 검사한다. 이 내부 해시는 발행처 진위나 과거 수신을 증명하지 않는다.
- `odds` payload: `provider_product_id`, `market`, `period`, 명시적 `line`(없으면 null), `settlement_scope`, `status=open|suspended|closed`, `outcome_ids`, `outcomes`, `quote_at_claim`, `valid_until`. open은 명시한 각 outcome ID의 배당이 모두 있어야 하고 숫자는 유한하고 1 초과여야 한다. 중지/종료는 빈 가격과 null 시각을 허용한다. 수신 시 이미 만료된 가격도 보존하되 사용 불가로 표시한다. 상품 ID·시장·기간·라인·정산 범위·선택지 ID는 스트림 내 고정이다. `outcome_ids`의 실제 완전성은 원천별 어댑터가 확인해야 한다. `quote_at_claim`은 원천 주장이고 수신보다 뒤일 수 없다. open 가격은 `valid_until`과 `quote_at_claim`을 모두 명시해야 한다. 누락된 가격·기준점·시각을 추정하지 않는다.
- `lineup`: `provider_team_id`, `announcement_status=confirmed|probable`, `players`의 `provider_player_id`, `role`, `order`(미상이면 null). 선수·타순 중복은 거부한다. `injury`: 팀·선수 ID와 원천 `status`; 진단의 확실성이나 결장 여부를 자동 추론하지 않는다. `substitution`: 팀·선수 in/out ID, 경기 중 phase만 허용하며 같은 선수의 교체는 거부한다. `settlement`: 상품 ID, 정산 범위, `status=final|void|corrected`, `final_at` 및 결과; 확정 시각은 수신보다 늦을 수 없고 void를 제외하면 킥오프 이전일 수 없다. void는 결과 null이다. 기존 공식 정산 원장을 대체하거나 정산의 정확성을 보증하지 않는다.

## 저장과 입력 선택

`ObservationStore(connection)`은 호출자가 지정한 **별도 SQLite 연결**에 `observation_event_v1` 테이블과 조회 인덱스를 만든다. 운영 DB에 연결하지 않는다. 하나의 append는 `BEGIN IMMEDIATE` 트랜잭션을 사용한다. 동일 revision·동일 내용의 재전달은 `duplicate`로 최초 수신을 보존하고, 동일 ID 내용 충돌·동일 sequence 충돌·수신 시각 역행·다른 경기/상품/시장/기간/라인 변경은 롤백한다. 더 낮은 sequence가 나중에 수신되면 `out_of_order`로 이력을 저장하되 최신 수정본을 대체하지 않는다. 신규 revision의 동일 payload는 `refresh_freshness`이며 호출자가 사용 가능 상태의 변화를 검사해야 한다. 동일 revision의 실제 최초 수신을 증명하는 것은 수집 경계의 책임이다.

`snapshot(canonical_game_id, decision_at, phase, kickoff_at, max_age_seconds)`는 **그 판정 시각까지 실제 수신됐다고 기록된** 스트림별 최고 sequence만 읽고 전체 수정본·출처·시각·payload 및 콘텐츠 해시를 반환한다. `max_age_seconds`는 odds/lineup/injury/substitution 각각 명시해야 하며, 조건에 맞지 않는 최신 수정본은 `usable=false`와 `stale`·`expired`·`suspended`·`closed` 사유로 남긴다. 옛 가격으로 돌아가지 않는다. 배당은 수신 시각뿐 아니라 `quote_at_claim`의 나이도 제한하므로 오래된 인용을 다시 수신해도 새 배당이 되지 않는다. 단, 그 원천 시각의 진위는 검증되지 않았다. 결과 정산은 예측 snapshot에 포함하지 않는다. 빠진 필수 스트림이 없음을 확인하는 것은 호출자의 책임이며 빈 entries를 완전한 자료로 해석해서는 안 된다.

경기 전 snapshot은 kickoff−30분까지만 허용한다. 경기 중 snapshot은 kickoff 이후만 허용하고, 경기 전 원천이라도 킥오프 뒤 최초 수신된 최신 자료는 `late_pregame`으로 사용 불가 처리하고 더 오래된 가격으로 돌아가지 않는다. T−30 이후 받은 경기 전 새 수정본은 `pregame_frozen`을 반환해 기존 동결 픽을 갱신하라고 요청하지 않는다. 경기 중 새 수정본은 기존 경기 전 픽과 별도의 live 계산에만 사용해야 한다. 현재 코드는 공급자가 신고한 phase와 kickoff를 검사할 뿐 실제 경기 시작·중단·종료를 확인하지 못한다.

`append()`의 action은 `recompute_prediction`, `refresh_freshness`, `invalidate`, `pregame_frozen`, `settlement_only`, `none` 중 하나다. 이는 작업 요청 후보이지 모델 호출이나 작업 큐가 아니다. 호출자는 snapshot을 다시 읽고 필요한 모든 자료의 사용 가능성·종목별 적합성·실제 경기 상태를 확인해야 한다. 재계산과 모델 재학습은 별개며 이 모듈은 학습을 실행하거나 학습 신호를 내지 않는다. `snapshot.provenance_sha256`은 판정 시각·모드·연령 한도와 모든 선택된 레코드 및 사용 가능 상태를 묶는다. 독립적인 신뢰 스냅샷 없이 DB와 해시를 같이 다시 쓰는 변조는 검출할 수 없다.

## 연결 전 남은 작업

1. 공급자별 실제 event/game/player/product ID와 단조 revision의 의미를 확인하고 기존 v1.2 연구 계약의 별칭·상품 참조와 매핑한다. 이 모듈은 이름 유사도나 결과로 ID를 추정하지 않으며 공급자 ID가 동일 선수·경기를 뜻하는지 검증하지 않는다.
2. 수집 완료 시각을 신뢰할 수 있는 ingress에서 기록하고 원문 접근 권한·보존 정책, 외부 시각 인증·키 관리, 요청 실패·재시도·중복 메시지와 서버 장애 복구를 별도 설계한다. 소스 발표·배당 시각과 외부 인증을 사실로 승격하지 않는다.
3. 축구 실시간 점수·남은 시간, 야구 이닝/아웃, 농구 시간·파울 등 종목별 상태와 선수·팀 ID 매칭 및 모델이 요구하는 단위/결측 규칙은 미구현이다. 시장 중단과 유효 기한의 공급자별 해석·개별 가격 구매 가능성도 미검증이다.
4. 기존 동결 원장과 별도 live 결과 저장, 변경 이벤트 유실 방지/outbox, idempotent 큐, 운영 DB 마이그레이션, 부하·보안 검토 및 실서비스 검증은 별도 작업이다. 이 API는 source-provided sequence 순서와 SQLite 로컬 트랜잭션에만 의존하며 전체 분산 시스템의 정확히 한 번 처리를 보장하지 않는다.

연구 v1.2 계약은 경기·상품·관측·동결·정산 관계와 입력 해시를 가진다. 이 모듈의 이벤트 `provider_*`와 v1.2의 alias/product/revision을 자동 변환하거나 기존 레코드 해시를 바꾸지 않는다. 구조와 변환·호환성은 통합 워커가 실제 공급자별로 결정해야 한다.
