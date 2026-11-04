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

## offer ID와 읽기 전용 전송 후속 확인

운영 Fly 체크아웃 `be716c1ac63a930a6d9abccd5a886ab032153b89`의 `src/ai_decision.py`, `src/prediction_runtime.py`는 기준 `b3f0d5d6`와 차이가 없었다. 운영 저장 원장의 선택지에는 게임번호가 없지만, 운영 `odds_snapshots`의 같은 KBO 경기·회차·2-way 승패 상품에는 문자열 게임번호 `2395`가 있었다. 운영 `offer_id()`에 해당 문자열을 사용하면 홈·원정 offer ID가 모두 실제 원장과 일치했고 정수 `2395`는 일치하지 않았다. 기준 후보 `G101:260131:2395`를 브리지의 기존 엄격 검사에 넣은 독립 로컬 시험에서도 두 ID가 일치했다. 이 후보는 M0 Betman 응답을 확인해 얻은 값이 아니다. 근처 가격 스냅샷의 관측 시각과 원장의 가격 관측 시각은 달랐고, 실제 M0 capture나 같은 원문 수신을 증명하지 않는다. 결과는 `offer-id-source-probe.json`에 기록했다.

`refresh_operating_export(state, events, now)`는 각 event에 M0 원본 `market_id`를 요구한다. 한 건이면 `load_operating_export` 결과와 `export_path`, `source_receipt_path`를 반환하며, 여러 건이면 `exports_by_official_id`, `errors_by_official_id`로 구분한다. 빈 목록은 네트워크와 파일 쓰기를 하지 않는다. 최대 5건, 각 전송 25초, 출력 32 MiB, stderr 64 KiB이며 정확한 exporter 바이트 해시, 레코드 해시·건수·출처 시각과 fixture를 검사한다. stdout 원본과 수신 기록은 내용 해시 경로에 보존한다. 외부 서명이나 전체 체인 인증은 아니다.

Windows에서 `flyctl ssh console -q --pty=false`는 정상 `123` 출력 뒤에도 `Error: The handle is invalid.`와 종료 코드 1을 반환했다. 합성 공식 ID를 사용한 실제 읽기 전용 exporter 점검에서도 JSON과 운영 행 36개가 돌아왔지만 종료 코드는 1이었다. 함수는 출력이 있더라도 모든 비영 종료 코드를 거부하므로 현재 Windows에서 실제 source refresh는 막힌다. OS/CLI 처리 문제의 독립적인 수정과 성공적 종료 검증 전까지 source 연결 성공으로 표시하지 않는다. 기존 main common ledger는 `market_id=null`인 degraded 표본을 의도적으로 거부한다.

시장 기준 선택은 운영 원장에 저장된 시장 확률의 순위를 재사용하지 않고, 비교에 쓰는 실제 양쪽 공통 배당의 역수를 정규화해 산출한다. 배당이 같으면 동률로 제외한다. 운영 모델의 저장 확률과 정책은 바꾸지 않는다.

## Fly Machines API 전송 확인

후속 점검에서 SSH 대신 이미 실행 중인 `proto-odds-collector` 머신을 `flyctl machine exec -j`로 읽었다. 머신 목록은 메모리에서 ID·상태·process group만 확인하고 전체 설정이나 환경변수는 출력·저장하지 않는다. 합성 공식 ID와 상품 후보를 이용한 단일 제한 export는 CLI 종료 0, 원격 종료 0, exporter 바이트 해시 일치, 운영 prediction 36행, 양쪽 엄격 offer ID 일치였다. 실제 M0 사전 capture나 공식 Betman 상품을 확인한 결과는 아니다. `-j` 성공 응답은 zero `exit_code` 필드를 생략하며, 비영 원격 종료는 `exit_code`로 반환하는 것을 별도 명령으로 확인했다. bridge는 CLI와 원격 종료를 각각 검사하고 비영 종료를 모두 거부한다.

이제 `refresh_operating_export`는 실행 중인 app 머신이 정확히 하나일 때 `flyctl_machine_exec` 수신 기록을 만든다. 한 경기 결과에 `export_path`, `source_receipt_path`, `source_receipt`, `export_sha256`, M0 원문 루트가 포함된다. 복수 경기면 `exports_by_official_id`, `errors_by_official_id`로 분리한다. 실제 네트워크·DB 조회는 빈 이벤트에서는 일어나지 않는다. 파일 재생과 비영 원격 종료 거부는 mock 전송 테스트로 검증했다. 위 단일 실제 점검은 API 응답과 해시를 검사한 시험이며 수신 기록 파일의 실전 생성까지 확인한 것은 아니다.
