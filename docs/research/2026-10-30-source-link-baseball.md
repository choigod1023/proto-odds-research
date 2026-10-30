# KBO·NPB 공식 과거 투구 기록 연결 — 2026-10-30

## 수행 범위와 실제 산출물

PR424 입력의 `retrospective_year_strict__claimed_time_only` KBO 204건·NPB 110건에서 리그별 현지 날짜가 가장 이른 6일을 고정했다. 경기 결과·배당·ROI로 표본을 고르지 않았다. 대상은 KBO 20건, NPB 11건이다. 주 사례 31건 모두 공식 날짜·홈/원정 팀·경기 ID를 확인했다. 과거 기록으로 수치 특성을 만들 수 있었던 사례는 KBO 17건·NPB 11건이다. KBO 3건은 고정한 과거 날짜에 검증된 이전 경기가 없어 특성을 만들지 않았다. 나머지 주 사례 283건은 이번 날짜 한도 밖이며 `primary-coverage.jsonl.gz`에 사유를 남겼다.

실제 공식 경기 98건(KBO 35, NPB 63), 투수 행 890개(KBO 384, NPB 506)를 파싱했다. 실제 투구수가 있는 행은 KBO 70개·NPB 506개다. KBO의 70개는 **각 팀 선발투수**의 기록이며 구원투수 투구수가 아니다. NPB는 9명으로 구성된 사후 선발 타순 126개를 파싱했다. KBO 공식 Summary에는 타순이 없었고, 상세 박스스코어 API는 오류 페이지를 반환했다.

요청 한도 160회 중 실패를 포함해 실제 HTTP 요청 95회(KBO 17, NPB 78)를 사용했다. 실제 요청 시작부터 마지막 응답 수집까지는 **2026-10-30 05:21:24.642165~05:25:55.971569 UTC**다. 응답별 실제 수집 시각·URL·POST 인자·SHA-256·원문 압축 캐시를 보존했다. 이 시각은 과거 경기 전 수신 시각을 뜻하지 않는다. 재실행은 캐시를 사용한다. 재시도·자동 리디렉션·인증·유료 자료·접근 제한 우회는 사용하지 않았다.

## 출처와 파서

- [KBO 경기이슈 Summary](https://www.koreabaseball.com/Game/Summary.aspx): `P`가 투구수임을 명시한다. 화면 예시 `P91`은 필드 설명이며 관측 자료로 넣지 않았다. `gameId` 쿼리만으로 과거 경기 자료가 나오는 것으로 간주하지 않는다.
- [KBO 날짜별 SummaryView](https://www.koreabaseball.com/Game/SummaryView.aspx): 공개 화면에 명시된 POST `gamedate=YYYYMMDD`, `leagueId=1`, `seriesId=0`을 사용했다. 화면의 팀 헤더·투수 목록을 응답에 포함된 `G_ID`, `G_DT`, `TEAM_ID`, `TB_SC`, `END_SC`와 대조했다. ASP.NET ViewState를 역직렬화하지 않았다. base64를 풀어 들어 있는 XML 문자열의 단순 태그만 읽었다. 경기 ID·날짜가 없는 별도 이닝/선수 데이터셋은 무시한다. 같은 날짜·대진에 두 경기 ID가 있으면 Summary의 보드 순서를 추정하지 않고 모호함으로 제외한다.
- [NPB 과거 경기 날짜 색인](https://npb.jp/bis/2026/games/gm20260329.html): 정확한 날짜의 1군 `/scores/YYYY/MMDD/home-away-series/` 링크만 수집했다. 각 날짜의 전체 링크와 실제 선택 링크를 `source-cardinality.json`에 특성 계산 전에 기록했다.
- [NPB 실제 박스스코어 표본](https://npb.jp/scores/2026/0329/c-d-03/box.html): `投球数` 104·95와 `打者` 31·28을 구분한다. 실제 종료 표기 15:41도 읽는다. 이닝 안의 중첩 표 때문에 열이 밀리지 않도록 직접 자식 셀만 읽는다. 투수 합계 행과 중첩 이닝 행은 선수 행에 넣지 않는다.
- [기존 공식 NPB 파서](../../src/npb_lineups.py)의 정확한 날짜별 링크 규칙과 번호가 있는 타순 처리 방식을 참고했다. `npb-live-refresh-20260905` 워크트리와 현재 `kbo_pregame.py`도 읽기 전용으로 확인했다. 운영 모듈은 가져오거나 변경하지 않았다.

수집 원문은 공식 경기 통계 페이지다. 기사 본문이나 유료 자료는 수집하지 않았다. 모든 URL·요청 인자와 원문 해시는 `source-manifest.json` 및 `http-attempts.jsonl`에 있다.

## 특성 정의와 시간 제한

원래 고정 프로토콜은 유지했다. 실제 출처에서 확인한 필드에 맞춰 `source-adapter-spec.json`에 두 개의 별도 수치 묶음을 기록했다. KBO에서 원래 원했던 구원투수 투구수는 확보하지 못했다는 사실을 그대로 남긴다.

공통 과거 날짜는 대상 경기 현지 날짜 D-4, D-3, D-2다. 확보한 공식 과거 경기만 더하며 필드명에 `observed`와 `prior3_source_dates`를 쓴다. 빈 날짜·페이지 실패를 0으로 채우지 않는다. 실제 전체 창의 부하, 현재 불펜 가용 인원, 선수 건강, 현재 명단을 뜻하지 않는다. 함께 제공하는 `observed_historical_team_games`로 포함 경기 수를 확인할 수 있다.

- KBO: 팀 선발투수들의 실제 투구수 합, 구원투수 등판 수, 2회 이상 등판한 구원투수 수, 포함 경기 수. 대상 경기 선발투수 개인의 누적 투구수라는 뜻은 아니다. 선수 식별자는 팀별 표시 이름이며 공식 등록 ID는 없으므로 동명이인에 따른 반복 등판 수의 충돌 가능성이 있다.
- NPB: 구원투수 실제 투구수 합, 구원 등판 수, 누적 30구 이상 구원투수 수, 2회 이상 등판한 구원투수 수, 포함 경기 수. 공식 선수 ID로 연결한다. 한 경기에서 동일 선수가 중복 관측되면 한 번만 더하고, 더블헤더의 서로 다른 경기 ID는 별도 등판으로 센다.

NPB는 공식 표기의 실제 종료 시각을 날짜와 결합해 `max_fact_time`으로 쓴다. 출처 시간대는 `Asia/Tokyo`다. KBO Summary에는 정확한 종료 시각이 없으므로 `max_fact_time=null`, `max_fact_date`, `fact_date_timezone=Asia/Seoul`을 제공한다. 해당 날짜의 자정 종료에 24시간 여유를 더한 상한이 의사결정 시각 이전이어야 한다. 이를 실제 종료 시각으로 표기하지 않는다. 날짜 기반 해석은 `retrospective_date_lagged`, 필드 증거의 기존 호환 tier는 `retrospective_lagged`다. NPB는 실제 종료 시각이 있어 기존 부모 게이트에서도 날짜 시간대 분기를 사용하지 않는다. 부모의 `Asia/Tokyo` 지원 어댑터와도 모순되지 않는다.

각 필드에는 출처 해시, 실제 관측 시각, 사용한 과거 경기 ID, 시간 제한 근거가 있다. 대상 경기 자체, 미래 경기, 고정 날짜 밖의 경기를 특성에서 제외한다. 과거 투수 기록을 집계하는 코드에 결과·배당·타깃 라벨을 넘기지 않는다.

사후 선발 타순은 `parsed-official-games.jsonl.gz`와 `target-lineups-DIAGNOSTIC-ONLY.jsonl.gz`에만 남겼다. 대상 경기 타순의 경기 전 게시·수신 증거가 없으므로 특성으로 쓰지 않았다. 경기 전 확정 라인업을 확보했다고 주장하지 않는다.

## PR422 보조 사례

사용자가 지정한 PR422 `ARCHIVE/events.jsonl.gz`를 읽기 전용으로 조사했다. 이미 수집한 공식 경기 날짜에 있는 KBO 35건·NPB 65건을 후보로 삼았으며 추가 HTTP 요청은 없었다. 결과·배당·시장 선택 필드를 참조하지 않고 날짜·팀·경기 시작 시각만 연결했다. 후보 선택과 부모의 정식 fullgame 정규화 의무를 `auxiliary-protocol.json`에 따로 기록했다.

보조 사례는 공식 경기 ID가 공유되지 않으므로 날짜·홈/원정 팀이 일치하는 유일한 경기와 실제 시작 시각 차이 30분 이내를 요구했다. 실제 시작과 예정 시작이 다른 경우 `fixture-joins.jsonl.gz`에 별도 사유와 실제 시각을 남긴다. 두 경기가 모호하면 제외한다. NPB 1건은 시작 시각 차이가 30분을 넘었고, 2건은 수집된 공식 대상 경기가 없어 연결하지 않았다. 과거 기록이 없는 사례까지 연결 성공으로 부풀리지 않고 최종 특성 행 **KBO 25건·NPB 50건**을 `auxiliary-features.jsonl.gz`에 제공한다.

이 보조 특성 행은 운영·픽스터 성과 사례가 아니다. 부모가 필요한 ID만 정식 fullgame 사례로 만들고, 주 사례와 겹치는 날짜를 보조 학습에서 제외한 뒤 공통 평가를 해야 한다.

## 실패·결측과 검증

KBO 공개 `GetKboGameList` 및 `GetBoxScoreScroll` POST, `GetBoxScoreScroll` GET은 HTTP 200 오류 페이지였다. 총 3개 응답을 오류로 보존하고 정상 통계로 파싱하지 않았다. NPB `2026/0401/s-c-02`는 종료된 경기 자료가 아니어서 제외했다. 날짜별 빈 링크/완료 경기 없음, 주 사례의 한도 밖 283건, 과거 이력 없음 3건, 필드별 결측을 각각 보존한다. 실패는 `negative-probes.jsonl.gz`, 연결 근거는 `fixture-joins.jsonl.gz`, 필드 가용성은 `field-availability.jsonl.gz`에 있다.

실제 KBO 원문을 사용한 첫 회귀 실행에서 잘못된 날짜 오류 1개를 재현했다. 같은 ViewState 안에 날짜가 없는 별도 Table1 데이터셋도 있어 발생했다. 이 데이터셋을 경기 식별에 사용하지 않도록 수정한 뒤 테스트가 통과했다. 다른 날짜의 실제 경기 응답은 여전히 거부한다.

검증 명령:

```powershell
python -m pytest tests/test_rd_kbo_npb_sources.py tests/test_npb_lineups.py -q --disable-warnings
python scripts/rd_kbo_npb_sources.py collect
python scripts/rd_kbo_npb_sources.py verify
```

회귀 테스트 **22개**가 통과했다. 실제 원문 표본, 투구수/BF 구분, 날짜·홈/원정·경기 ID·시각 불일치, KBO 더블헤더 모호함, NPB 중첩 이닝, 대상·미래 경기 제외, 중복/더블헤더 집계, 달력 상한, 결측 유지, 요청 실패 계수와 캐시를 검사한다. 전체 특성 **103행·946개 수치 필드**를 기존 PR424 증거 게이트로 검사해 모두 통과했다. 그중 KBO 336필드는 달력 종료+24시간 가정, NPB 610필드는 실제 종료 시각을 썼다. 실제 경기 전 수신을 입증하는 것은 아니다.

정확한 변경 경로 목록은 `experiments/source-link-baseball-20261030/changed-paths.txt`, 산출물 해시는 `artifact-hashes.json`, 실행 검증은 `validation.json`에 제공한다. ROI 열람·모델 학습·운영/스케줄러/DB 변경·푸시·개별 PR 생성은 하지 않았다. 부모가 이 격리 커밋을 통합하고 공통 평가와 통합 PR을 담당한다.
