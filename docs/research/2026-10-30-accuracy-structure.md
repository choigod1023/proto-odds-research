# 예측 자료구조 v1 제안 (2026-10-30)

이 문서는 운영 전환이 아닌 오프라인 연구 계약입니다. 합성 자료의 구조 검사이며 적중률·ROI 개선이나 실제 수신 인증의 증거가 아닙니다. `schema.json`은 Draft 2020-12 형태이고 `validator.py`는 이 스키마가 사용하는 제한된 키워드와 아래 관계 규칙을 검사합니다. 임의 스키마를 처리하는 범용 JSON Schema 엔진은 아닙니다.

경기와 공식기관별 별칭, 발매상품과 재발매 연결, 원문관측, 가격 및 선수 수정본, 동결 예측, 공식정산을 별개 배열로 둡니다. 더블헤더 번호와 canonical ID를 보존하며 공식기관/ID의 중복을 거부합니다. 상품 ID는 전역 고유하며 재발매는 동일 경기·시장·기간·라인·선택지의 별도 상품으로 연결합니다. 식별자의 발급과 실제 공식 일정과의 일치, 더블헤더 번호의 진위는 별도의 수집·매칭 단계에서 검증해야 합니다. 취소는 경기 상태 또는 결과 없는 void 정산으로 표현합니다. 실경기 취소가 반드시 void인지는 상품 규칙을 확인해야 합니다.

원천 게시 주장은 nullable이며 수신 시각과 혼동하지 않습니다. 시간대가 필수이고 최초수신 ≤ 수정수신 ≤ 판단 ≤ kickoff−30분을 적용합니다. 실제 입력으로 사용되는 관측은 observed여야 하며 가격 전체 선택지와 확률 벡터, 정확한 경기·상품·시장·기간·라인을 대조합니다. 결과의 final 시각과 결과 수신을 따로 기록하고 동결 후 정산을 연결합니다. `validate_append`는 이전 기록이 새로운 자료의 그대로인 접두부인지 확인합니다. 동결 해시는 정렬된 JSON의 SHA256이며, 자체 해시만으로 불변성이나 외부 시각을 증명하지 못하므로 신뢰할 수 있는 이전 사본이 반드시 필요합니다. 현재 독립 인증 검증기는 연결되지 않아 `external_receipt_verified`는 false만 허용합니다. 실제 원문 바이트와 해시의 일치 여부, 출처의 공식성, 선택지의 진위, 결과 정정 정책, 외부 서명 검증은 구현하지 않았습니다.

v0의 `official_game_id`는 기관과 ID를 별칭으로 옮기고 canonical ID를 새로 부여합니다. 상품·시장·기간·line과 전체 선택지를 명시해야 합니다. `source_published_at_claim`은 `published_at_claim`, `raw_sha256`은 `source_sha256`, `result_final_at`은 `final_at`으로 바뀝니다. lineups는 경기·팀별 revision으로, `input_revision`은 가격 및 선발 revision 참조로 구체화합니다. 결과는 freeze와 별도 배열입니다. v0의 미지 값, 수신시각·선수 ID·상품 정보가 부족한 레코드는 추정으로 채우지 않으며 자동 호환 변환은 제공하지 않습니다. v1 채택은 총괄 결정이 필요합니다.

DB 제안만 제시합니다: game(canonical PK), game_alias UNIQUE(authority, official_id), product(product PK, game FK, reissue FK), observation(observation PK), quote_revision 및 lineup_revision(revision PK, observation FK), frozen_prediction(freeze hash PK), settlement(freeze FK)의 정규화. 경기별 시간 조회에는 (game_id, revision_received_at), 상품별 가격에는 (product_id, revision_received_at), 선수별에는 (player_id, revision_id)를 검토합니다. append-only 제약·동결 내용의 바이트 보존·인증 기록의 외부 신뢰정책은 DB 트랜잭션과 권한으로 별도 강제해야 합니다. migration은 작성하거나 실행하지 않았습니다.

PR #431은 내부 체인만으로 외부 인증을 증명할 수 없음을 확인했고 실제 외부 인증 표본은 0입니다. PR #433 초기 모델은 선수 계층이 연결되지 않았고 비교에서 유의 개선이 0건이었습니다. 여기서는 모델을 학습하지 않습니다. main f5d4ff5는 #435를 포함하며, 오래된 운영 계약의 무투표 보류 문구와 달리 코드에는 조건부 시장 fallback이 있습니다. 실제 서버 버전은 확인하지 않았습니다. 모의 지갑과 운영 데이터는 변경하지 않았습니다.
