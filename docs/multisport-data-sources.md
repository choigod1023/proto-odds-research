# 종목별 최근 전적·상세 지표 수집

2026-09-05 조사 및 실행. `src/sports_history_collect.py`로 외부 데이터를
SQLite에 직접 저장하고, `SportsHistoryStore.team_form()`이 SQL로 최근 전적을
계산한다. CSV는 중간 저장소가 아니며 명시적으로 내보낼 때만 만든다.
이번 구현은 수집·검증·조회 기능이다. 서버 스케줄러, 추천 모델, 화면에는
자동 연결하지 않았다. 테스트 DB를 운영 DB로 덮어쓰면 안 된다.

## 종목별 출처와 실제 지원 범위

### 축구

- FotMob의 공개 리그/경기 HTML: 종료 스코어, 팀별 xG·npxG. K리그1/2,
  J1/J2를 등록했다. 현재 리그 페이지 범위만 탐색하며 전체 과거 시즌 수집은 아니다.
- [공개 HTML 정책](https://www.fotmob.com/robots.txt)에 따라 `/api/*` 등
  금지 경로를 사용하지 않는다. 페이지 접근 가능 여부가 재사용 라이선스를
  보장하지 않으므로 상시 운영·공개 재배포 전 제공사 조건 확인이 필요하다.
- [StatsBomb Open Data](https://github.com/hudl/open-data)는 명시한 대회·시즌의
  과거 연구 표본용이다. 이벤트의 `shot.statsbomb_xg`를 팀별 합산하고 승부차기는
  제외한다. 최신 경기의 대체 소스로 사용하지 않는다. 저장 시
  `sample_scope=historical_open_data`; 공개 시 출처 표시와 라이선스 확인이 필요하다.
- xG 미제공은 0이 아니다. 제공사가 다르면 모델·측정 범위가 다르므로 합쳐서
  평균 내지 않는다. 세부 계약은 [축구 소스 노트](soccer-source-notes.md).

### 야구

- KBO·NPB: Naver 경기 일정 응답에서 종료 스코어와 R/H/E(득점·안타·실책)를
  저장한다. 네 번째 RHEB 값을 볼넷으로 추정하지 않는다.
- MLB: 공식 StatsAPI 일정과 경기 박스스코어에서 득점·안타·타수·볼넷·삼진·홈런을
  저장한다. 요청당 최신 종료 경기 5개만 박스스코어로 보강한다.
- MLB 선수 시즌 `expectedStatistics` 응답의 `avg/slg/woba`를 해당 namespace
  안에서만 xBA/xSLG/xwOBA로 해석한다. 투수는 `_against`로 구분한다.
  API 필드 해석과 원본을 함께 저장하며 안정된 공개 API 계약으로 간주하지 않는다.
- [xwOBA 설명](https://www.mlb.com/glossary/statcast/expected-woba): 타구 질을
  반영하는 지표이며 축구 xG와 동일한 단위가 아니다.
  [Statcast 필드 문서](https://baseballsavant.mlb.com/csv-docs)도 참고할 수 있다.
- xERA는 이번 응답에 없으므로 `null`; `wobaCon`은 의미를 임의 변환하지 않고
  원본에만 보존한다. KBO·NPB의 xwOBA 제공은 확인되지 않아 만들지 않았다.
- 외부 대조용 공식 기록: [KBO](https://eng.koreabaseball.com/Schedule/DailySchedule.aspx),
  [NPB](https://npb.jp/bis/eng/2026/games/).

### 농구

- NBA·KBL·WKBL: Naver 종료 경기 스코어를 수집한다.
- 별도 지정 시즌 팀 통계에는 평균 득실점, 리바운드, 어시스트, 야투·3점 성공률,
  턴오버 등 실제 응답 필드만 저장한다. `winRate`와 성공률 필드는 원본 단위를
  보존하므로 모두 같은 0~1 확률로 취급하면 안 된다.
- ORtg·Pace·eFG% 같은 고급 지표는 [NBA 공식 정의](https://www.nba.com/stats/help/glossary)를
  따라 별도 검증해야 한다. 이번 소스의 평균 득점만으로 이를 만들지 않았다.
- WNBA의 검증된 수집 경로는 이번에 확보하지 못했다. 지원되지 않는 리그를
  임의 category로 요청하거나 경기 없음으로 처리하지 않는다.

### 배구

- V리그 남/여: Naver의 `kovo` / `wkovo` 카테고리에서 종료 세트 스코어를 수집한다.
- 지정 시즌 통계는 공격 성공률, 세트당 블로킹·서브·디그, 리시브 효율,
  세트/득점 비율 등을 원본 필드명·단위로 저장한다.
- 공격 성공률과 범실을 반영한 공격 효율, 사이드아웃 비율은 서로 다른 지표다.
  [Volleyball World 공식 통계](https://en.volleyballworld.com/volleyball/competitions/volleyball-nations-league/statistics/men/best-attackers/)
  같은 자료를 확장 소스로 검토할 수 있지만 이번 자동 수집기에는 포함하지 않았다.
- 시즌 ID는 공급자 문자열이다. 예: `022`를 정수 `22`로 바꾸지 않는다.
  현재 시즌 ID가 확인되지 않으면 과거 시즌을 최신으로 자동 대체하지 않는다.

## DB 저장·시간 규칙

- `sports_game_versions`: 제공사·리그·경기 ID별 버전. 최종 결과만 전적에 포함하고
  취소/연기 표시는 이전 결과를 무효화한다. 더블헤더를 날짜/팀명으로 합치지 않는다.
- `sports_metric_versions`: 선수/팀·시즌별 관측 스냅샷. 시즌 누적 값을 경기별
  측정치로 복사하지 않는다. `sport`는 원본 adapter 코드(`bs/bk/vl`)를 보존한다.
- `observed_at`: 실제 수집 완료 UTC 시각. 과거 경기라도 오늘 처음 받았다면
  과거 예측 시점에서 조회되지 않는다. `as_of` 필터 후 최신 버전을 선택한다.
- 시간대가 없는 StatsBomb 기록은 날짜 정밀도를 보존한다. DB 정렬에만 날짜 끝
  UTC를 사용하며 이를 실제 킥오프로 표시하지 않는다.
- 최근 승무패·평균 득실점·가용 지표의 평균/표본 수는 SQL 집계다.
  팀 ID는 제공사/리그에 종속된다. Proto 팀과의 검증된 매핑 없이 이름 유사도로
  자동 연결하지 않는다.
- 상세 요청 예산 때문에 MLB 박스스코어를 생략한 경우, 점수·팀·킥오프가 동일할 때만
  기존 지표와 원래 관측 시각·출처를 보존한다. 실제 미제공 응답·점수 수정·취소는
  그와 구분한다.
- 원문은 `documents`의 `sports_response:*`; 요청 감사는 `sports_fetches`,
  실행 결과는 `sports_collection_runs` 이벤트에 저장한다.
- HTTP 실패/잘못된 JSON/스키마 오류는 실패다. 정상 빈 일정, 지표 미제공과 구분한다.
  Naver/MLB 결과 수 제한 초과는 검증된 표본만 저장하고 `partial`/종료 코드 1로
  알린다. 페이지 누락·상충 데이터는 저장하지 않는다. 날짜를 좁혀 재수집해야 한다.
- 익명 요청, 요청/본문/날짜 예산, 호스트 제한을 적용한다. 401/403/429는 재시도나
  우회 없이 실패한다. Naver/MLB의 익명 응답도 무제한 수집·재배포 권리를 뜻하지 않는다.

## 실행 방법

프로젝트 루트에서 실행한다. 최초 검증은 별도 DB를 지정한다.
Windows에서는 `python` 대신 `py -X utf8`을 사용할 수 있다.

```sh
python src/sports_history_collect.py sources
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source naver:KBO --source naver:NPB --source mlb --since 2026-09-04 --until 2026-09-04 --limit 30 --max-requests 12
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source fotmob:kleague1 --source fotmob:j1 --since 2026-08-29 --until 2026-09-05 --limit 3 --max-requests 10
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source mlb-expected --player-id 693821 --season 2026 --limit 3 --max-requests 2
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source naver-stats:NBA --season 2025 --limit 3 --max-requests 2
# 명시적으로 과거 시즌 수집: 현재 시즌 자동 대체가 아님
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source naver-stats:KOVO남 --source naver-stats:KOVO여 --season 022 --limit 3 --max-requests 3
python src/sports_history_collect.py collect --db data/runtime/sports-smoke.sqlite3 --source statsbomb --competition 43 --season 106 --since 2022-12-01 --until 2022-12-31 --limit 1 --max-requests 3
python src/sports_history_collect.py inventory --db data/runtime/sports-smoke.sqlite3
python src/sports_history_collect.py team --db data/runtime/sports-smoke.sqlite3 --provider fotmob --league kleague1 --team-id 164734 --limit 10
python src/sports_history_collect.py metrics --db data/runtime/sports-smoke.sqlite3 --provider mlb_statsapi --subject-id 693821
# 사용자가 CSV를 원할 때만 별도로 실행
python src/export_runtime.py --db data/runtime/sports-smoke.sqlite3 --kind events --name sports_history --output exports/sports-history.csv
python src/export_runtime.py --db data/runtime/sports-smoke.sqlite3 --kind events --name sports_metric_snapshots --output exports/sports-metrics.csv
```

날짜는 Naver KST, MLB officialDate, FotMob UTC 기준이다. 시즌 통계에는 경기 날짜
필터가 적용되지 않으며 실행 보고서에서도 시즌 ID로 구분한다. 최대 기간은 31일,
최대 반환 기록은 100개다. 더 큰 백필은 날짜를 나눠 명시적으로 실행한다.

## 실제 실행 결과

2026-09-05 KST, 격리된 `data/runtime/sports-smoke.sqlite3`에 저장·조회했다.

- 9월 4일 일정: KBO 5, NPB 5, MLB 13경기. MLB 중 5경기는 박스스코어 보강.
- 8월 29일~9월 5일 범위: K리그1 3, J1 3경기 모두 xG 수집.
  J1 기존 등록 주소의 404를 발견해 `223/j-league`로 수정했다.
- 9월 4일 NBA/KBL/WKBL/V리그 남녀는 정상 응답의 종료 경기 0건.
  별도의 **2월 1일 과거 검증**에서 각각 6/3/1/1/1경기를 저장했다.
- **2022 월드컵 과거 연구 표본** 1경기를 별도로 저장했다.
- 시즌 스냅샷은 MLB 선수 1개, NBA 팀 3개, V리그 남녀 각 3개: 총 10개.
  KOVO `022`는 과거 시즌이며 최신 기록이라고 주장하지 않는다.
- 경기 총 42개. 전 리그 전체 수집 수가 아니라 요청 범위를 제한한 검증 결과다.
- 강원 `164734` 조회: 저장된 1경기 0승 1무 0패, xG/npxG 각 0.81, 표본 1.
  최근 10경기를 요청했다고 표본 10개를 만들어내지 않는다.
- Bryce Elder 시즌 기대 피타율 0.252, 기대 피장타율 0.401, 기대 피wOBA 0.313;
  xERA는 미제공. 시즌 누적 관측값이며 다음 경기 예측 확률이 아니다.
- KBO 5경기 재수집: 신규 버전 0건. 서버 재시작·운영 DB 수정·CSV 자동 출력 없음.

## 운영 연결 전 남은 작업

확인된 시즌 ID/리그 범위와 이용 조건을 확정하고 서버 수집 명령을 스케줄러에
연결해야 한다. 이후 제공사 팀 ID↔Proto ID를 검증해 연결하고, 충분한 실제
관측 이력을 축적한 뒤 시점 분리 검증으로 예측 성능을 평가해야 한다.
이 PR만 머지해도 운영 사이트에 xG가 표시되거나 추천 정확도가 개선되는 것은 아니다.
