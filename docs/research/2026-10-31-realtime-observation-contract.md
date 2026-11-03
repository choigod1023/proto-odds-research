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

경기 전 snapshot은 kickoff−30분보다 엄격히 이전에만 허용하며 정확히 T−30도 동결로 취급한다. 경기 중 snapshot은 kickoff 이후만 허용하고, 경기 전 원천이라도 킥오프 뒤 최초 수신된 최신 자료는 `late_pregame`으로 사용 불가 처리하고 더 오래된 가격으로 돌아가지 않는다. T−30 이상에서 받은 경기 전 새 수정본은 `pregame_frozen`을 반환해 기존 동결 픽을 갱신하라고 요청하지 않는다. 경기 중 새 수정본은 기존 경기 전 픽과 별도의 live 계산에만 사용해야 한다. 현재 코드는 공급자가 신고한 phase와 kickoff를 검사할 뿐 실제 경기 시작·중단·종료를 확인하지 못한다.

`append()`의 action은 `recompute_prediction`, `refresh_freshness`, `invalidate`, `pregame_frozen`, `settlement_only`, `none` 중 하나다. 이는 작업 요청 후보이지 모델 호출이나 작업 큐가 아니다. 호출자는 snapshot을 다시 읽고 필요한 모든 자료의 사용 가능성·종목별 적합성·실제 경기 상태를 확인해야 한다. 재계산과 모델 재학습은 별개며 이 모듈은 학습을 실행하거나 학습 신호를 내지 않는다. `snapshot.provenance_sha256`은 판정 시각·모드·연령 한도와 모든 선택된 레코드 및 사용 가능 상태를 묶는다. 독립적인 신뢰 스냅샷 없이 DB와 해시를 같이 다시 쓰는 변조는 검출할 수 없다.

## 연결 전 남은 작업

1. 공급자별 실제 event/game/player/product ID와 단조 revision의 의미를 확인하고 기존 v1.2 연구 계약의 별칭·상품 참조와 매핑한다. 이 모듈은 이름 유사도나 결과로 ID를 추정하지 않으며 공급자 ID가 동일 선수·경기를 뜻하는지 검증하지 않는다.
2. 수집 완료 시각을 신뢰할 수 있는 ingress에서 기록하고 원문 접근 권한·보존 정책, 외부 시각 인증·키 관리, 요청 실패·재시도·중복 메시지와 서버 장애 복구를 별도 설계한다. 소스 발표·배당 시각과 외부 인증을 사실로 승격하지 않는다.
3. 축구 실시간 점수·남은 시간, 야구 이닝/아웃, 농구 시간·파울 등 종목별 상태와 선수·팀 ID 매칭 및 모델이 요구하는 단위/결측 규칙은 미구현이다. 시장 중단과 유효 기한의 공급자별 해석·개별 가격 구매 가능성도 미검증이다.
4. 기존 동결 원장과 별도 live 결과 저장, 변경 이벤트 유실 방지/outbox, idempotent 큐, 운영 DB 마이그레이션, 부하·보안 검토 및 실서비스 검증은 별도 작업이다. 이 API는 source-provided sequence 순서와 SQLite 로컬 트랜잭션에만 의존하며 전체 분산 시스템의 정확히 한 번 처리를 보장하지 않는다.

연구 v1.2 계약은 경기·상품·관측·동결·정산 관계와 입력 해시를 가진다. 이 모듈의 이벤트 `provider_*`와 v1.2의 alias/product/revision을 자동 변환하거나 기존 레코드 해시를 바꾸지 않는다. 구조와 변환·호환성은 통합 워커가 실제 공급자별로 결정해야 한다.

## 기존 원장과 LIVE 소비 경로로 넘기는 방법

이 단락은 통합 워커를 위한 연결 계약이며 아직 어댑터나 호출 경로를 구현했다는 뜻이 아니다.

1. **경기 키:** 기존 `src/ai_decision.py:event_id(game)`은 `source_event_id` 또는 `match_id`와 종목을 해시하며, 없으면 종목·연도·날짜·리그·홈/원정으로 fallback ID를 만든다. 이벤트의 `provider_game_id` 또는 `provider_event_id`를 기존 `event_id`에 바로 대입하지 않는다. 제공자·공식 ID, 종목·리그·팀·시각·더블헤더를 대조한 별칭 매핑이 유일할 때만 `canonical_game_id`를 해당 기존 `event_id(game)`에 연결한다. 충돌·미확인은 보류한다. 기존 fallback 키와 공식 ID가 같다고 추정하거나 동결 원장의 키를 바꾸지 않는다.
2. **상품 키:** 기존 `selection_id(game, option)`은 기존 경기 ID·market·label·line·선택을, `offer_id(game, option)`은 selection ID·회차·게임번호를 사용한다. 새 `provider_product_id`나 outcome ID는 이 두 해시와 자동으로 동일하지 않다. 어댑터가 원천 상품의 시장·기간·기준점·정산 범위·선택지와 회차/재발매를 유일하게 대조해 기존 option과 연결하고 다르거나 부족하면 사용을 보류해야 한다. 원 관측은 원 ID와 해당 mapping evidence를 보존하며 재발매를 옛 offer로 덮어쓰지 않는다.
3. **경기 전 소비:** 수집기는 `append()` 후 필요할 때 `snapshot(..., phase='pregame')`을 호출하고, 필수 시장과 선수 입력이 모두 usable인지 확인한다. 실제 경기 전 원장의 판단 시각 `as_of`, `kickoff`, `market_observed_at`, `features`/예측 payload와 `decision_snapshot`을 기존 형식에 맞게 구성하는 어댑터는 아직 없다. `source_published_at_claim` 또는 `quote_at_claim`을 실제 수신이나 기존 원장의 `market_observed_at`으로 대체하지 말고, 실제 수신 완료와 시장 스냅샷의 as-of 의미를 검증해 전달해야 한다. 새 `provenance_sha256`과 기존 `ai_decision._input_revision_hash`는 서로 다른 내용의 해시이므로 같은 필드에 그대로 치환하지 않는다. 참조 저장 방식과 기존 signature/스키마 변경은 별도 설계·검증이 필요하다.
4. **append-only와 T−30:** `PredictionRuntime.record_pregame`/`record_pregame_batch`는 기존 revision signature가 같으면 기록을 건너뛰고 달라지면 `PredictionLedger.append_prediction`을 통해 새 prediction revision을 추가한다. 기존 경량 갱신 `record_live_market_revisions`는 `as_of >= kickoff−30분`을 보류하고 `refresh_document`도 T−30부터 기존 선택과 option을 보존한다. 새 API도 정확히 T−30을 포함해 이후 사전 snapshot과 재계산 신호를 막는다. 기존 `append_prediction` 단독 검사는 kickoff 이전 여부만 검사하므로 통합에서 원장 함수 단독 호출을 T−30 보호로 간주하면 안 된다.
5. **경기 중 LIVE:** 기존 `web/src/lib/saved-live-prediction.js`는 보존된 `prediction_record`의 선택·사전 확률과 신선한 live 점수로 `estimateLiveProbability`를 호출하고, `Markets.jsx`는 진행 중 그 추정치를 LIVE에 표시한다. 이 추정치는 `bet-ledger.js`에서 명시한 점수·진행률 기반 상황 추정이며 선수/실시간 배당 모델 검증 결과가 아니다. 새 `phase='inplay'` snapshot의 사용 가능한 관측은 별도 live 입력과 별도 현재 추정치에만 연결해야 하고, `record_pregame`/기존 사전 `prediction_record`를 덮어쓰거나 새 사전 픽을 만드는 데 사용해서는 안 된다. 점수·시간 feed의 경기 일치/신선도, 모델 지원 시장, 표시 경로와 재현 검사는 별도 구현이 필요하다.
6. **정산:** 새 settlement 관측은 snapshot에서 제외된다. 기존 `PredictionRuntime.settle_latest`는 기존 event ID로 보존된 prediction revision을 찾아 공식 결과를 별도 settlement로 추가한다. provider 결과를 그 API에 연결하는 매칭·확정·상품별 정산 검증은 구현하지 않았으며, 결과를 선수/가격 특징이나 과거 사전 판단에 다시 넣지 않는다.
