# KBO 공식 과거 선발 타순 복원

이 연구는 기존 frozen 자료를 수정하거나 운영 예측, 데이터베이스, 스케줄러에 연결하지 않는다. `GetKboGameList`와 `GetLineUpAnalysis`의 과거 경기별 응답을 현재 시점에 수신해 연구용 자료로 정리한다.

## 식별과 한계

- 일정 응답의 경기 ID, 연도, 리그, 팀, 더블헤더 번호를 확인하고, 원본 ID가 없을 때는 같은 날짜·홈팀·원정팀이 일정에서 하나일 때만 연결한다. 개시 시각이 있는 원본은 KST 기준으로도 비교한다. 날짜·팀만 존재하는 원본은 개시 시각 검증 불가로 남긴다.
- `LINEUP_CK`가 `true`이고 두 팀의 응답 메타데이터가 일정의 경기·팀·리그·시즌과 일치하며 각각 타순 1~9가 완전할 때만 공식이 현재 제공하는 해당 경기 확정 타순으로 기록한다. `false`일 때 표시되는 최근 명단은 확정 데이터에 넣지 않는다.
- 이 엔드포인트의 표에는 선수 ID가 없다. 동일 경기·팀·타순의 별도 사후 출장자 자료에서 후보 ID를 얻고 공식 선수 프로필의 이름과 비교한다. 이름이 축약된 자료는 같은 타순의 모든 ID 후보를 프로필과 비교한다. 해당 경기의 사후 기록이 없으면 다른 경기의 같은 시즌·팀·이름에서 유일한 ID를 찾고 공식 프로필을 비교하며, 출처 범위를 따로 기록한다. 유일한 일치가 없으면 ID를 비운다. 같은 이름 선수 등으로 인한 오연결 가능성을 완전히 제거한 독립적인 신원 증명은 아니다. 출장자 전체나 교체 선수를 선발 명단으로 사용하는 방식이 아니다.
- 일정의 선발투수 ID와 이름은 일정 응답 필드로 보존한다. 해당 시점에 선발이었거나 경기 전에 발표됐다는 별도 증거로 해석하지 않는다.
- 공식 페이지가 현재 보여주는 확정 타순과 과거 경기 전에 발표·수신됐다는 증거는 다르다. 표본 응답에는 발표 시각·변경 이력이 없었다. 모든 레코드는 `historical_publication_time_proven=false`, `historical_receipt_proven=false`, `eligible_as_pregame_feature=false`다. 실제 경기 전 자료인 것처럼 frozen 모델에 연결해서는 안 된다. 당시 발표 후 교체 여부도 이 단일 현재 응답만으로 판정할 수 없다.
- 일정·타순·선수 프로필 요청마다 URL, 메서드, 파라미터, 상태, 현재 수신 시각과 SHA-256을 JSON 메타데이터로 보관하고 응답 본문 바이트를 해시 이름으로 보관한다. HTTP 응답의 전송 계층 압축은 HTTP 라이브러리가 해제할 수 있으므로 보관된 것은 클라이언트가 파싱한 응답 본문이며 TLS/전송 패킷 원본은 아니다. 실패 응답도 보관한다.

## 실행 및 재시작

```powershell
python scripts/research_kbo_historical_lineups.py `
  --rows C:/Users/user/Documents/Codex/kbodata/experiments/kbo-player-data-20261103/rows.jsonl `
  --batters C:/Users/user/Documents/Codex/kbodata/data/raw/detail/kbo_batters_indiv_2023_2026.json `
  --output .research-state/kbo-lineups-v3 --max-requests 10000
```

요청 간격은 최소 1초, 타임아웃은 연결 5초·응답 15초다. `--max-requests`는 신규 네트워크 요청의 상한이며 캐시 조회는 제외한다. 같은 출력 경로로 재실행하면 수집된 응답의 SHA를 검사하고 완료된 경기는 건너뛴다. 403·429를 포함한 비정상 HTTP 상태와 네트워크 오류에서는 중단한다. 부분적으로 처리된 경기는 결과에 넣지 않고 다음 실행에서 캐시를 이용해 다시 처리한다. `--fixture`를 반복해 특정 경기만 대상으로 삼을 수 있다. 출력 경로는 Git에서 제외된 독립 연구 상태이며 운영 DB가 아니다.

`official-lineups.jsonl`은 완료·미확정·식별 불가 결과를 경기별로 기록한다. `manifest.json`은 실행을 정상 종료하거나 제한에 도달했을 때 분모, 상태별 건수, 미처리 수, 입력과 결과 해시, 중단 이유를 기록한다. `requests/`와 `raw/`의 파일은 함께 보존해야 출처를 재검증할 수 있다.

## 공식 표본

`samples/`에는 2023-04-01 `20230401HHWO0`, 2025-03-22 `20250322OBSK0`, 2026-03-28 `20260328HTSK0`의 공식 일정·타순 원문과 요청 메타데이터가 있다. 세 표본 모두 `LINEUP_CK=true`이며 홈·원정 각각 9명의 경기·팀 식별자가 일치했다. 선수 ID는 별도 동일 경기 사후 기록 및 공식 프로필 이름과 대조했다. 표본의 일정·타순 메타데이터에서 수신 시각은 두 요청을 마친 직후 기록된 상한 시각이다.

잘못된 시즌으로 요청한 표본은 `false`를 반환했다. 다음날 실제 경기의 요청은 다음날 경기 ID를 반환했으며, 전날 경기로 검증하면 거절된다. `20230401-boxscore`는 교체 출전자를 포함할 수 있는 공식 사후 박스스코어 비교 표본이며, 확정 명단의 원천으로 사용하지 않는다.

공식 원천: [경기별 라인업 안내](https://www.koreabaseball.com/Schedule/GameCenter/Preview/LineUp.aspx), [KBO 일정 API](https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList), [라인업 API](https://www.koreabaseball.com/ws/Schedule.asmx/GetLineUpAnalysis), [선수 프로필 예시](https://www.koreabaseball.com/Record/Player/HitterDetail/Basic.aspx?playerId=74163). K리그는 [공식 경기별 타임시트 API 문서](https://api.kleague.com/docs/api_match_timeSheet.jsp)가 있으나, 확정 명단 및 발표 시각 여부를 이 연구에서 검증하지 않았다.
