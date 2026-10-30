# 농구 선수 출전시간·연장 부하 추출

2026-10-30, 브랜치 `codex/rd-basketball-workload-20261030`. 운영 코드·설정·DB를 변경하지 않았다. 월 손실 제한 OFF인 부모 연구에 특징을 전달하는 작업이며, 픽 생성·모델 학습·ROI 비교·배포는 실행하지 않았다. 두 번째 경기를 강제로 채우는 정책도 추가하지 않았다.

## 전달 파일과 실제 커버리지

`experiments/pregame-rd-basketball-20261030/features.jsonl.gz`에는 원본 프레임의 `cohort/event_id/league/kickoff`를 그대로 보존한 205행이 있다. 결측 경기에도 빈 `features`와 감사 기록을 남겼다.

- 실제 로스터: NBA 99행 중 89행에 한쪽 이상 특징, 63행은 양쪽 모두 특징이 있다. KBL 18행 중 16행에 한쪽 이상 특징, 9행은 양쪽 모두 특징이 있다. 합계 117행 중 105행, 양쪽 모두 72행이다.
- 게시 시각 주장 코호트: NBA 88행 중 77행에 특징, 70행은 양쪽 모두 특징이 있다. 게시 시각 주장은 인증된 경기 전 캡처가 아니다.
- `archive-features.jsonl.gz`: `historical_market_proxy` 251행(NBA 223, KBL 28)이며 양쪽 모두 특징이 있는 것은 89행이다. 원본 농구 ARCHIVE 5,503행 중 실제 새 특징이 있는 행만 내보냈다. 추가 ARCHIVE용 수집은 하지 않았다.

특징이 있는 경기의 kickoff 범위는 2025-10-29부터 2026-10-27까지다. 2023년부터의 전체 ARCHIVE를 커버했다는 뜻이 아니다. 양쪽 특징이 없는 행도 있으므로 부모 모델은 결측을 별도로 처리해야 한다. 현재 코드는 과거 경기 자체가 없는 경우도 결측으로 보존한다.

보조 ARCHIVE는 정산 경기만 남은 사후 시장확률 자료이며 운영 모델·실제 픽스터 모델 성능의 증거가 아니다. 실제 로스터와 달력상 같은 경기가 포함될 수 있으므로 학습은 반드시 평가 시점보다 과거인 행만 사용하고, 같은 실제 경기가 학습·평가에 중복되지 않도록 부모 평가기에서 확인해야 한다.

## 사전 고정한 특징

홈·원정 각각 다음 네 변수를 내보낸다. 이름 앞에는 `home_` 또는 `away_`가 붙는다. `frozen-protocol.json`은 추출·ROI 평가 전에 저장했다. 이 작업에서는 ROI를 조회하거나 특징 선택에 사용하지 않았다.

- `player_top5_minutes_72h`: 선수별 최근 72시간 누적 출전시간을 먼저 합산한 뒤 상위 다섯 선수의 합.
- `player_peak_minutes_72h`: 같은 누적 출전시간 중 최댓값.
- `player_squared_load_72h`: 각 선수 누적 출전시간을 리그 정규 경기 시간으로 나눈 값의 제곱합. NBA는 48분, KBL은 40분이다.
- `ot_team_minutes_72h`: 해당 기간 과거 경기의 연장 횟수 × 5분 × 5명. 팀의 연장 노출이며 개별 선수의 실제 연장 출전시간이라고 해석하면 안 된다.

`src/context_features.py`의 기존 `rest_days`, `games_7d`, 일정 밀도와 다른 선수 단위 입력이다. 대상 경기의 라인업·출전시간·득점·결과·배당, 시즌 최종 누적 통계는 새 특징으로 사용하지 않았다. 부상·발표된 결장으로 잃는 예상 출전시간과 실제 위치 기반 이동 부하는 증거가 부족해 만들지 않았다. `audit.jsonl.gz`에 `announced_absence_minutes_lost=missing_no_pregame_announcement_history`를 기록했다.

## 출처와 시간 처리

KBL은 공식 공개 API의 경기 목록, 경기별 정보 및 선수 통계를 사용했다. 예: [공식 경기 정보](https://api.kbl.or.kr/match/S47G01N230), [공식 선수 경기 통계](https://api.kbl.or.kr/match/S47G01N230/player-stat). `gameDate/gameStart/gameEnd/isEnded`, 선수별 `playMin/playSec`, 경기별 연장 배열을 읽었다. 실제 경기 통계 23경기를 정규화했다.

NBA는 CDN 접근 거부 후 공개 ESPN 프록시를 사용했다. 공식 NBA 자료라고 부르지 않는다. [ESPN 일정 API](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20251027)와 경기별 `summary?event=...`의 선수 경기별 MIN, 최종 `End Game` 플레이의 wallclock·period만 읽었다. 227경기를 정규화했다. 날짜 범위 질의는 400 응답이어서 일별 질의로 바꿨으며 실패 응답도 원문과 원장에 보존했다.

`decision_at`은 원본 `available_at`, 없으면 kickoff 30분 전이다. 입력의 available_at 자체가 실제 관측으로 입증됐다는 주장은 하지 않는다. 대상 프레임은 원본 ID·팀·kickoff로 연결하며 결과를 조인 키로 쓰지 않는다.

선택 창은 `[decision_at - 72시간, decision_at)`의 경기 시작 시각이다. 선택된 모든 경기의 실제 종료 시각이 decision_at보다 엄격히 빨라야 한다. ESPN은 실제 최종 플레이 wallclock이 없으면 제외한다. KBL 종료 시각은 원본의 분 단위 정밀도를 유지한다. 대상 경기 통계는 이 시간 조건으로 차단한다.

72시간 전체의 일정 출처가 있어야 하며, 그 팀의 예정된 과거 경기 중 박스스코어가 하나라도 빠지면 해당 팀의 특징 전체를 제외한다. 출전시간 누락은 0으로 바꾸지 않는다. ESPN이 명시적으로 DNP라고 표시하고 통계가 없는 선수는 출전 선수 집계에서 제외하지만, 출전 여부가 불명확한 결측은 거부한다. 선수 출전시간 합은 정규 시간+연장 시간의 5배와 대조한다. ESPN 분 단위 반올림에는 선수당 약 0.51분 오차를 허용한다.

출력된 모든 과거 특징의 증거 등급은 `retrospective_lagged`다. `max_fact_time`은 사용한 과거 경기의 실제 종료 시각 최댓값이고, `observed_at`은 원문을 실제 수집한 2026-10-30 시각이다. 과거 경기 전 캡처를 새로 만들어 넣지 않았다. 원문은 전체 경기 종료 후 수정됐을 가능성이 있어 당시 공개 내용과 완전히 같다고 보장할 수 없다.

## 결측 사유와 검증

실제+게시 시각 주장 코호트의 홈·원정 410개 슬롯 중 324개 슬롯에 특징이 있다. 나머지는 과거 72시간 박스스코어 없음 53개, 해당 과거 경기의 종료가 의사결정 이후 14개, 예정된 경기 박스스코어 누락 17개, 선수 출전시간 결측 2개다. 한 슬롯에 네 특징이 있으므로 이 코호트의 특징값은 1,296개다.

보조 ARCHIVE의 제외 사유는 일정 창 미확보 9,202개 슬롯, 박스스코어 누락 1,259개, 시간 조건 실패 21개, 72시간 과거 경기 없음 183개, 선수 출전시간 결측 1개다. 결측이 무작위라는 가정은 하지 않는다. 커버된 시즌·팀·시점이 선택된 작은 보조 표본이다.

`python -m pytest tests/test_rd_basketball_features.py -q`: 13개 테스트 통과. 미래 경기·종료 전 경기·대상 경기 배제, NBA/KBL 48/40분과 연장 5분, 결측과 0 구분, 실제 종료 시각 필수, 시즌 통계 무시, 부분 박스스코어 거부, 일정 누락 차단, 요청 허용 목록·예산을 확인했다.

전체 출력 특징값 2,656개의 숫자 형식과 `max_fact_time < decision_at < kickoff`를 실제 Python 실행으로 검증했다. 모든 원문 SHA-256도 확인했다. `--budget 0`으로 외부 요청 없이 재추출했을 때 두 특징 파일의 압축 해제 후 SHA-256이 동일했다. `verification.json`에 검증 결과를 저장했다.

적중률·ROI·모델 개선 수치는 이 작업의 결과가 아니다. 부모 작업이 실제 같은 경기의 배당·결과·픽스터 투표와 연결해 비교해야 한다. 시장 단독 프록시를 기존 운영 모델로 바꿔 부르지 않는다. PR #423의 공동 개선 실패 결과를 뒤집었다고 주장하지 않으며 Walsh/Joshi 2025 정정 평균 ROI는 −9.77%라는 기존 정정을 유지한다.

## 재실행과 향후 실제 캡처

추출기: `scripts/rd_basketball_features.py`. 재현 명령은 `python scripts/rd_basketball_features.py --budget 0`이다. 공개 GET은 호출당 최대 300회, 연결/읽기 제한 5/15초, 응답당 8MB 제한, 리다이렉트 거부, 인증·환경 프록시 비사용으로 묶었다. 이번 수집 원장의 총 시도는 485회이며 실패 및 향후 일정 캡처 1회도 포함한다. 원문 gzip 합계는 10,693,326바이트다.

향후 경기의 시각 증거를 쌓는 일회성 명령도 실행했다:

```powershell
python scripts/rd_basketball_features.py --budget 1 --capture-url "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20261031"
```

실제 캡처 메타데이터는 `capture-manifest.json`에 있다. 향후 일정 원문의 수집 시각만 기록했으며 과거 특징의 등급을 올리지 않는다. 명령 한 번에 URL 최대 10개이며 cron·배포·서버 작업은 없다. 자동 부상 공지 파서는 구현하지 않았다.

원본 로스터 두 파일 및 ARCHIVE는 외부 경로와 원본 SHA-256으로 참조한다. 전체 ARCHIVE를 복사하지 않았다. `selected-frames.jsonl.gz`와 `selected-archive-frames.jsonl.gz`에는 결과·배당을 뺀 필요한 식별 필드만 있다. `normalized-games.jsonl.gz`, `schedule.jsonl.gz`, `schedule-coverage.json`, `sources.jsonl`, `raw/*.gz`, `hash-manifest.json`으로 원문·정규화·특징을 추적할 수 있다. 모든 쓰기는 지정된 네 경로 안에서만 수행했다. 커밋 후 push·PR은 부모 작업에 맡긴다.
