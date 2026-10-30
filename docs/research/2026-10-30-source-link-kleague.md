# K리그 과거 선수 기록·선발진 공격 기여 특징 연결

전용 작업트리 `rd-kleague-strength-20261030`, 브랜치 `codex/rd-kleague-strength-20261030`에서 구현했다. 기준 커밋은 부모가 통합한 PR424 의존성 `8bb66e8bccd4ac68fe46a2b59e75147a84de9679`다. 고정된 부모 프로토콜의 파일 해시는 `experiments/source-link-kleague-20261030/parent-protocol-reference.json`에 보존했다. ROI·모델 결과를 검사하거나 모델을 학습하지 않았다. 이 작업은 연구 입력 연결이며 운영 개선이나 미래 수익을 검증한 결과가 아니다.

## 실제 수집과 연결 건수

- 기존 로컬 Naver 원본: 816경기, 팀별 1,632행, 선발 선수 기록 17,952행. PR424 `raw-xi.json.gz`와 1,632행의 선수 ID 집합이 모두 일치한다. 이번에는 기존 XI에 없던 경기별 `goal`, `assists`, `pos` 필드를 실제 원본에서 추가 파싱했다. 도움 결측 5,104개는 결측으로 보존했다. 출전 시간은 원본에 없다.
- 새 공개 HTTP: 총 49회. HTTP 200은 45회, 404는 1회, 연결·시간 초과는 3회다. 실제 요청 시작은 `2026-10-30T05:21:32.921690+00:00`, 마지막 수신 시각은 `2026-10-30T05:26:33.961854+00:00`다. 탐색 중 대기 시간을 포함한 범위이며, 실행별 수집 제한은 240초·요청별 10초·4MB·재시도 없음이다. 요청 수 상한 60회는 로그를 통해 실행 사이에도 유지한다.
- 공식 선수 통계: 17경기에서 선수 기록 **515행**을 받았다. `official-stats-fields.jsonl.gz`는 실패한 XI 연결의 실제 통계도 보존한다. 중간 답변의 516행은 집계 오류였으며 정확한 값은 515행이다.
- 공식 선발과 선수 통계의 결합까지 통과한 자료: 11경기, 팀별 22행, 선발 242행, 교체 출전까지 포함한 선수 통계 337행. K리그1 4경기·K리그2 7경기이며, 2026-03-01~2026-09-13 기록이다. 나머지 6경기는 선수 ID 누락 또는 선발 11명 불일치로 제외했다. 원인을 `source-discovery.json`에 남겼다.
- 겹치는 공식 `kleague:2026:1:91`과 Naver `20260704052191`은 날짜·홈·원정이 같고, 양 팀 선발 22명의 ID와 비결측 득점·도움이 일치한다. 이 확인을 바탕으로 숫자가 정확히 같은 선수 ID만 연결했다. 이름 유사도나 스코어는 사용하지 않았다. 중복 경기에서는 공식 자료를 쓰고, 불일치한 팀·날짜 기록은 제외하도록 구현했다. 이번 실행의 충돌은 0건이다.
- 합친 과거 기록: 826경기·팀별 1,652행이다. 공식 11경기 중 1경기가 기존 원본과 겹쳐 고유 경기 순증은 10경기다.
- 고정 주 코호트 24건: 실제 통계 기반 특징 13건, 양 팀 특징 9건. K리그1은 12/15건, K리그2는 1/9건이다.
- 고정 `historical_market_proxy` 733건: 실제 통계 기반 특징 709건, 양 팀 특징 705건이다. 모두 K리그1이며 운영·픽스터 성과 자료로 바꾸어 부르지 않는다.
- 출력은 주 코호트 24행과 보조 아카이브 733행을 모두 보존한다. 특징 없는 사례도 빈 `features`와 사유가 있는 감사 기록으로 남긴다. 수치 필드는 총 8,065개다. 이 수에는 기록 보유 비율도 포함되며, 기록 보유 비율만으로 통계 기반 전력 연결 건수를 늘리지 않았다.

## 고정한 특징 정의

`protocol.json`을 먼저 저장했으며, 수집·연결 이후에도 ROI로 정의나 가중치를 고르지 않았다. 출력 접두어는 `kleague_strength_prior_{home|away}_`다.

1. `xi_goal_known_mean`: 가장 최근의 **과거** 선발 11명 중 이전 득점 기록이 있는 선수들의 과거 선발당 득점 평균.
2. `xi_assist_known_mean`: 같은 방식으로 계산한 과거 선발당 도움 평균.
3. `xi_goal_known_share`, `xi_assist_known_share`: 위 값을 실제로 계산할 수 있는 선수 수를 11로 나눈 기록 보유 비율. 결측을 0득점·0도움으로 채우지 않는다.
4. `attack_goal_known_mean`: 과거 선발진의 공격·미드필드 포지션 중 기록이 있는 선수의 선발당 득점 평균. 공식 세부 포지션은 코드에 적은 명시적 목록만 사용한다.
5. `rotation_goal_net`, `rotation_assist_net`: 직전 두 과거 선발진 사이에서 들어온 선수의 과거 기여율 합에서 빠진 선수의 기여율 합을 뺀 값. 들어온 선수와 빠진 선수 모두에 해당 기록이 있을 때만 계산한다. 교체가 없으면 해당 값을 내보내지 않는다.

선수 기여율은 기준으로 삼은 과거 XI 경기보다도 **앞선** 경기만 누적한다. 따라서 기준 XI 경기에서 넣은 골이나 도움으로 해당 선수의 교체 가치를 평가하지 않는다. 같은 팀·같은 시즌의 실제 선발 기록만 사용하며 이적 전 기록이나 시즌 최종 집계를 합치지 않는다. 관측된 선발 경기 표본의 공격 기여 대리변수이고, 완전한 선수 능력·선수 가치·수비력 추정은 아니다. 교체 출전은 선발당 비율 분모에 넣지 않는다. 새 공식 자료의 실제 `minutePlay`, `shot`, `shotOnTarget`은 파싱·보존하지만 이번 고정 특징에는 사용하지 않는다. 출전 시간을 추정하거나 선발 횟수를 분당 기록으로 부르지 않는다.

테스트에서는 선발 구성·횟수를 그대로 두고 과거 한 선수의 득점만 2에서 6으로 바꾸면 XI 기여 평균이 2/11에서 6/11로, 교체 기여 차이는 -1에서 3으로 바뀜을 확인했다. 따라서 기존 XI 출전 비율을 이름만 바꾼 특징이 아니다.

## 출처와 접근 제한

- [공식 일정](https://www.kleague.com/schedule.do): 실제 페이지의 `onChangedNew`에서 공개 `POST /getScheduleList.do`와 JSON 요청 필드를 확인했다. 2026년 3월 K리그2, 7·9월 K리그1 일정의 ID·팀·날짜·킥오프만 투영했다. 월을 `7`로 요청했을 때 비었던 응답도 로그에 남겼으며, 실제 화면 형식 `07`로 요청한 자료를 사용했다.
- [공식 매치센터 실물 표본](https://www.kleague.com/match.do?year=2026&leagueId=1&gameId=91&meetSeq=1&startTabNum=2): `getMatchRecordAllData` 함수에서 확인한 [공개 선수 기록 경로](https://www.kleague.com/api/ddf/match/getMatchRecordAllDetail.do)에 실제 폼 POST를 보냈다. 확인한 필드는 `meetYear`, `meetSeq`, `gameId`, `teamId`, `playerId`, `playerPos`, `goal`, `assist`, `minutePlay`, `shot`, `shotOnTarget`이다. API가 HTTP 200을 반환했다는 이유만으로 성공 처리하지 않고 `resultCode`, 일정·경기·팀·선수 ID, 실제 HTML의 선발 11명을 함께 검사했다.
- HTML의 `lineup away` 클래스가 홈팀에도 붙어 있어 클래스로 홈·원정을 정하지 않는다. `div.ground` 안의 실제 팀 이미지 경로와 `ul.player_ID`를 연결한다. 사진이 없는 선수도 `ul`의 ID를 보존한다. 잘못된 11명·누락 통계는 제외한다.
- [공식 선수 집계 페이지](https://www.kleague.com/record/player.do), [선수 상세 표본](https://www.kleague.com/record/playerDetail.do?playerId=20130248), [이용약관](https://www.kleague.com/about/terms.do)도 확인했다. 현재 시즌·경력 집계는 과거 경기 특징에 사용하지 않았다.
- [데이터 포털 robots](https://data.kleague.com/robots.txt)는 `Disallow: /`여서 포털 데이터 요청을 하지 않았다. 수집기 자체도 robots 이외의 포털 요청을 거부한다.
- [공식 홈페이지 robots](https://www.kleague.com/robots.txt)는 404였다.
- [Naver 기존 라인업 경로](https://api-gw.sports.naver.com/schedule/games/2023022501051/lineup), [기존 기록 경로](https://api-gw.sports.naver.com/schedule/games/2023022501051/record), 해당 호스트 robots는 연결 실패했다. 인증·우회·재시도 없이 실패를 보존하고 기존 로컬 원본만 사용했다. 로컬 원본 URL은 저장소 수집 코드 `src/game_detail.py`에서 확인한 경로이며 과거 HTTP 수신 로그를 확보했다는 뜻은 아니다.

원본 HTTP 바이트는 작업트리의 무시된 `raw-cache/`에만 있다. 커밋에는 최소 통계·신원 필드 투영과 실제 수집 시각·원본 SHA256을 남긴다. `adapter-source-projection.json`은 실제 응답의 필드·HTML 토큰을 줄인 테스트 자료로, 원본 HTTP 해시와 투영 자체의 해시를 혼동하지 않는다. 전체 기사·로그인·쿠키·인증정보는 사용하지 않았다.

## 시점과 부모 결합 계약

`inputs/cases.jsonl.gz`에서 `cohort`, `event_id`, `league`, `kickoff`, `home`, `away`, `decision_at`만 선택한다. 사례의 기존 `decision_at`을 그대로 보존하고 게시 시각으로 바꾸지 않는다. 대상 경기·같은 날·미래 경기·다른 시즌은 특징에서 제외한다. 역사 기록은 KST 달력 일자 종료에 24시간을 더한 가정 경계가 의사결정 시각 이하일 때만 사용한다. 가장 최근 과거 XI가 30일보다 오래되면 전력 특징을 생략한다.

두 특징 파일은 PR424 계약대로 `cohort/event_id/league/kickoff/decision_at/features/feature_evidence/source_ids/source_meta`를 갖는다. 필드별 증거는 `max_fact_time: null`, 실제 `max_fact_date`, `fact_date_timezone: Asia/Seoul`, `selected_fact_dates`, 실제 로컬 수집·파일 접수 시각 `observed_at`, 원본 `source_sha256`, URL·원본 필드 경로, 기준 XI ID·평가 기록 ID·평가 기록의 최종 일자를 포함한다. 원본 발표 시각은 `publication_at: null`이다.

부모 게이트가 지원하는 메타데이터 값 `availability_tier: retrospective_lagged`를 사용하지만, 실제 허용 뷰는 **`retrospective_date_lagged`**뿐이다. 실제 경기 완료 시각이나 과거 수신 시각을 만들지 않았다. `captured_pregame`으로 통과하는 필드는 0개다. 현재 확정 선발 XI라고 주장하는 특징도 없다. 가격·결과·ROI·예측 확률·모델 파일은 특징 코드의 입력이 아니다. 부모는 같은 신원·킥오프·결정 시각으로 가격 이동 자료와 결합할 수 있다.

## 실행과 검증

```powershell
python scripts/rd_kleague_strength.py collect
python scripts/rd_kleague_strength.py build --local-raw C:/Users/user/Documents/ChatGPT/sportstoto/data/raw/detail/kleague_soccer_2023_2026.json
python -m pytest tests/test_rd_kleague_strength.py tests/test_rd_soccer_features.py tests/test_rd_asof_evidence.py -q -p no:cacheprovider
```

결과는 **42 passed, 8 subtests passed**다. 새 테스트 19개는 미래·대상 경기 제외, 기준 XI 경기 통계 제외, 실제 득점 민감도, 도움 결측 보존, 팀·선수·시즌·리그 신원, 날짜 경계, 실제 공식 응답 투영 어댑터, 해시 변조·잘못된 경기/팀 거부, 원본 캐시 검증, 요청 수 제한, 데이터 포털 수집 거부, 부모 필드 게이트를 검사한다. `validation.json`에 전체 특징·신원·해시 감사 결과도 남긴다.

`collect`를 다시 실행하면 저장된 성공·실패 캐시를 재사용한다. 새 수집을 계속하는 명령이 아니다. 캐시가 없는 다른 체크아웃에서는 제한 내 새 공개 수신이 발생할 수 있으며 시각·해시는 달라질 수 있다. `build`는 공식 파싱 산출물과 고정 사례·로컬 원본으로 재생한다. 특정 로컬 원본을 다른 체크아웃에서 찾을 수 없으면 명시적으로 제공해야 한다.

새 K리그2 자료가 두 번 이상 이어지는 팀은 적어 9개 대상 사례 중 1개에만 특징이 붙는다. 통과하지 못한 6경기를 완전한 기록으로 간주하지 않는다. 전력 효과나 ROI는 부모의 공통 평가 전까지 미확인이다. 푸시·개별 PR·병합·배포·운영·스케줄러·DB 변경은 하지 않는다.

## 정확한 변경 경로

```text
scripts/rd_kleague_strength.py
tests/test_rd_kleague_strength.py
docs/research/2026-10-30-source-link-kleague.md
experiments/source-link-kleague-20261030/.gitattributes
experiments/source-link-kleague-20261030/.gitignore
experiments/source-link-kleague-20261030/adapter-source-projection.json
experiments/source-link-kleague-20261030/archive-features.jsonl.gz
experiments/source-link-kleague-20261030/audit.jsonl.gz
experiments/source-link-kleague-20261030/collection-plan.json
experiments/source-link-kleague-20261030/features.jsonl.gz
experiments/source-link-kleague-20261030/http.jsonl
experiments/source-link-kleague-20261030/input-identities.jsonl.gz
experiments/source-link-kleague-20261030/local-ingestion.json
experiments/source-link-kleague-20261030/manifest.json
experiments/source-link-kleague-20261030/official-raw-stats.jsonl.gz
experiments/source-link-kleague-20261030/official-stats-fields.jsonl.gz
experiments/source-link-kleague-20261030/parent-protocol-reference.json
experiments/source-link-kleague-20261030/parsed-player-history.jsonl.gz
experiments/source-link-kleague-20261030/protocol.json
experiments/source-link-kleague-20261030/raw-stats.jsonl.gz
experiments/source-link-kleague-20261030/schedule-identities.jsonl.gz
experiments/source-link-kleague-20261030/source-discovery.json
experiments/source-link-kleague-20261030/summary.json
experiments/source-link-kleague-20261030/validation.json
```
