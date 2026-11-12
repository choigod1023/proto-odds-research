# KBO 공식 타순 회고 교체 시나리오 사전 계약

상태: `registered_before_overlay`. 고정 시나리오 이름 `retrospective_official_confirmed_lineup_assumed_available`. PR #514 v4 확정 출력 SHA-256 `92015aa1af2e23813b0b4d8c3c5bd182d1c0ee7b2226920af3445e86569ae67c` 중 2026년을 전수 검사한다. 기존 rows, labels, projected gzip과 manifest는 수정하지 않는다.

한 행의 경기 ID, 원본 kickoff 날짜, 공식 G_ID·날짜·양 팀 코드·더블헤더 번호가 맞고 공식 LINEUP_CK=true, 선수 프로필 이름과 사후 식별을 교차한 고유 선수 ID로 각 팀의 1~9번이 완성된 경우만 포함한다. 같은 경기·같은 팀·같은 선수 중복이나 모호한 경기는 거부한다. 원본과 공식 raw bytes·수신 메타데이터 및 각 프로필의 SHA를 검사한다. 사용자 소유 v4 파일을 다시 수집하거나 수정하지 않는다. 미처리 공식 일정 533경기는 미확정이 아니라 미처리로 남긴다.

원래 projected JSON을 깊은 복사한 뒤 `home_lineup_strength`와 `away_lineup_strength`의 이력 객체만 변경한다. 나머지 `features`, 기존 투수/팀/휴식/불펜/구장과 기존 projection 메타데이터를 바꾸지 않는다. 별도 `official_lineup_context`는 기존 projection의 출처가 아니라 새로운 회고 선택자의 근거를 기록한다. 시나리오를 명확히 다른 이름으로 표시하고, 모델이 이전 projected 선택자와 혼동하지 않도록 `baseline_projection_metadata_not_official_selector=true`를 둔다. 공식 선수 ID를 타깃 실제 선수 성적/라인업이 아닌 유일한 선택자로 사용한다.

각 공식 타자의 최근 10개 사후 기록 중 타깃보다 **이전 달력일**인 기록만 제공한다. 동 일자 두 경기, 현재 경기와 미래 자료는 제외한다. 기존 모델 adapter `scalar(name,value)`가 받는 `[{player_id, history:[{fixture_id,date,ab,hit,bb,hr}]}]` 형태다. 수치 미확보는 null로 보존하며 이력 부재는 history=null이다. 공식 페이지는 현재 받은 내용이며 과거 공개·수신은 입증되지 않았다. 응답 실제 수신 시각, URL, SHA와 프로필 SHA·시각을 따로 남기고 `eligible_as_pregame_feature=false`, `point_in_time_verified=false`를 유지한다. 사용자 요청의 회고 가정 외에 엄격 PIT, 실제 T-30 예측이나 검증 ROI에 사용하지 않는다.

출력은 별도 gzip JSONL과 manifest로 저장하며 193개의 후보·제외 사유, 모델 공통 표본, 결측 프로필을 보고한다. 당일·미래 입력과 타깃 점수·타자 ID·성적 변조에 따른 불변성, 중복 선수/경기·팀·날짜·더블헤더 거부, 다른 feature의 정확한 동일성, 실제 원문 바이트 및 Git LF/CRLF 차이를 Python으로 검사한다. 모델 가중치·스케일·alpha·선택 규칙을 바꾸거나 재학습·성과 계산을 하지 않는다.
