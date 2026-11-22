# 선수 슛 이력 스키마

`player_games.jsonl.gz`는 한 선수의 한 경기 **사후 이력** 한 행이다. `game_id`, `day`(KST), `date_utc`, `team`, `home_away`, `athlete_id`, `name`, `source_summary_sha256`, `source_received_at`을 보존한다. `hard_attempts`, `hard_makes`, `soft_attempts`, `soft_makes`는 manifest의 9개 구역 순서다. `fta`, `ftm`과 `box`를 포함한다. `starter_postgame`, `minutes_source`, `minutes_seconds_displayed`, `minutes_precision_seconds`는 경기 후 박스 정보다. 정수 분을 60배 한 값은 실제 초가 아니며 precision은 60초다. `did_not_play`도 사후 필드다.

`accounting_ok`는 해당 선수의 슛·자유투와 선수 박스가 모두 일치할 때만 true다. `team_attribution_complete`는 팀의 슛·자유투에 미귀속이 없을 때만 true다. 모델이 완전한 선수 이력을 요구한다면 두 조건과 출전시간의 유효성을 모두 확인해야 한다. `team_audit.jsonl.gz`에는 미귀속 시도/성공, 팀 합계, 연장 포함 예상 팀 시간과 표시 시간의 불확실성이 있다. 선수 ID가 없는 비활동 기록은 임의 ID 없이 제외해 manifest에 센다.

타깃 경기의 `starter_postgame`, 출전시간, 실제 출전 여부와 타깃 PBP는 경기 전 입력으로 금지한다. 이력은 목표 KST 날짜 D-4 이전만 허용하며, 사후 수신된 원문이 당시에 실제 가용했다는 증거는 아니다. `validate_pregame`은 별도 증거의 출처 바이트 해시, 경기 ID, 10명, 게시·수신 시각과 금지 필드를 검사한다. 이 함수만으로 출처의 신뢰성이나 바이트가 실제 확정 라인업을 뜻한다는 사실은 검증되지 않는다. 별도 확인된 경기 전 확정 라인업은 0건이며 projected roster/minutes는 만들지 않았다.
