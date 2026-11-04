# 운영 투표 브리지 제한 조회 기록

- 기준: main `b3f0d5d6044ce9eea501f506d98ac78f22b1d547` (#576). Fly 앱 `proto-odds-collector`를 SSH로 읽었다. 환경변수와 비밀은 출력하지 않았다.
- 운영 공개 코드의 supervisor 기본 DB 경로 `/data/proodd.sqlite3`를 SQLite `mode=ro`로 열었다. WAL이 존재했으므로 immutable 모드를 사용하지 않았다. 최근 3일 prediction 행 수는 1,817개였다. 이는 전체 KBO 수가 아니다.
- `operating-export.json.gz`는 2026-11-04 14:47:14 UTC에 전역 원장 최신 300행을 읽어 그중 KBO prediction 21행만 남긴 원본 부분집합이다. 압축 파일 SHA-256과 로컬 파일 시각은 `export-receipt.json`에 있다. 원본 행 자체 해시 21개를 재계산해 확인했다. 이 자료는 전체 체인이나 최종 T-30 revision 완전성의 증거가 아니다. Fly CLI는 결과 출력 후 Windows handle 오류를 반환했다. 로컬 수신 기록은 서명되지 않았다.
- 해당 21행은 모두 `tipster-vote-v1`이며 fixture의 공식 `source_event_id`와 `match_id`는 모두 비어 있다. 최근 표본의 첫 선택지에 대해 게임번호 1~1000의 정수·문자열 및 앞자리 0 문자열을 사용해 기존 offer ID를 재계산했지만 일치하지 않았다. 게임번호가 그 범위 밖이라는 증거는 아니다.
- 이 21행 각각에서 `market_observed_at`, 연도, 회차, 홈/원정 팀을 정확히 일치시키는 운영 `odds_snapshots` 행은 0개였다. 이 제한 조회는 운영 DB나 파일 전체에 가격 원문이 없다는 증거가 아니다. 확인한 운영 prediction 행에는 Betman 원문 바이트와 원문 해시가 없었고, 확인한 snapshot 디렉터리는 과거 CSV였다.
- 실제 #576 M0 priced capture와 검증된 공통 표본은 이 조사에서 확인하지 못했다. 실제 비교·정산 결과는 0건이며 적격 경기 부재와 관측 실패를 구분해야 한다. 11월 6일 예정 경기는 이후 별도 실측이 필요하다.

브리지는 저장 원장 가격을 세 선택의 그림자 공통 가격으로 사용하고 M0 원래 가격을 보존한다. M0 실제 capture, 공식 원문 기반 입력 재구성, 모델 재계산, 적법한 사전 시각, 저장 원장 해시, 공식 ID 또는 정확한 팀·시간과 양쪽 offer ID, 동일 회차/게임번호를 요구한다. 운영 가격 공식 원문이 없으면 `official_raw_price_verified=false`, `formal_promotion_eligible=false`다. source receipt는 서명되지 않은 파일 및 시계 일관성 증거로, 외부 공증이 아니다. 체인 전체나 최종 운영 성과를 인증하지 않는다.

`export_forward_operating_votes.py`는 공식 G_ID를 내부 `event_id`에 그대로 넣지 않는다. 공식 대상의 팀·시간과 선택지 offer ID를 최근 3일/최대 3000행에서 대조해 내부 ID를 찾은 뒤 동일 SQLite 읽기 트랜잭션에서 해당 ID의 전체 prediction 행을 건수·최대 sequence와 함께 읽는다. 임계치 초과와 모호한 매핑은 실패한다. `complete`는 T-30 이후 export의 행·건수·해시·수신 시각과 exporter 코드 해시가 서로 부합하는지의 검사이며 서버 출처나 시각에 대한 독립 서명은 아니다. 이 새 공식 ID 발견 경로의 실제 Fly 실행과 다음 경기 매칭은 아직 검증하지 않았다.

## 게임번호 미검증 그림자 경로 (후속)

`--allow-degraded-first-shadow`를 명시한 경우에만, 최근 3일에 검색된 prediction 행에서 KBO 공식 팀 코드에 대응하는 정확한 홈·원정 이름, 정확한 시작 시각, 같은 연도·회차로 내부 event ID가 하나인지를 확인한다. 다른 event ID는 그 행의 승패 선택지가 불완전해도 중복으로 취급한다. 선택된 최신 T-30 이전 revision에는 홈·원정 두 선택지가 각각 하나씩 있어야 하며, 이후 보류 revision이 있으면 기존 `latest_pre_cutoff` 검사에서 제외한다. 3000행 또는 바이트 상한 초과는 실패한다. 다른 기간이나 전체 운영 원장에 같은 이벤트가 없다는 뜻은 아니다.

양쪽 `offer_id`를 M0 상품 게임번호로 재현하지 못해도 위 조건을 만족하면 `degraded_first_shadow`로 반환한다. `market_id=null`, `candidate_m0_market_id`는 M0 원본의 ID로 별도 보존하고 `game_number_verified=false`, `operating_market_identity_verified=false`, `operating_settlement_rule_verified=false`, `official_raw_price_verified=false`, `formal_promotion_eligible=false`를 명시한다. 원래 M0 가격과 운영 원장 가격은 각각 보존한다. 운영 가격의 실제 상품번호·공식 판매 조건이 일치했다는 뜻이 아니므로 정식 동일 상품 표본이나 최종 운영 성과로 합산할 수 없다.

보관한 실제 운영 행 21개를 임시 SQLite에 넣고 합성 공식 ID와 게임번호를 넣어 시험했을 때, 최근 3일 범위에서 고유 내부 event 1개와 그 revision 21개를 찾았다. 이 실험은 실제 공식 ID·게임번호·M0 capture를 검증하지 않는다. 실운영의 다음 경기 연결과 정산은 별도 실측이 필요하다.
