# 2026-10-30 야구 불펜 피처 추출

## 부모 작업 인계

브랜치 `codex/rd-baseball-bullpen-20261030`. 이 작업은 피처·근거 추출만 수행했다. 모델 학습, 실제 배당/결과를 이용한 적중률·ROI 비교는 부모 작업 범위다. 운영 코드·설정·DB는 변경하지 않았다. 월 손실 제한 OFF라는 연구 조건을 운영에 적용하지 않았다. 두 번째 레그를 강제하지 않았다.

산출물 루트: `experiments/pregame-rd-baseball-20261030/`.

- `features.jsonl.gz`: 실제 기준 로스터 10행, 5개 MLB 경기, 홈·원정 각 4개 피처. 정확한 roster `cohort/event_id/league/kickoff/available_at`을 유지했다. `decision_at=available_at`; 없을 때만 kickoff−30분.
- `calendar-features.jsonl.gz`: 5,800행. ARCHIVE `historical_market_proxy` 5,556행, `retrospective_year_strict` 108행, `saved_pregame` 28행, `retrospective_year_strict__claimed_time_only` 108행. 추가 피처는 홈·원정 각 1개뿐이다.
- 두 파일은 같은 키에서 피처 이름이 겹치지 않는다. `(cohort,event_id,decision_at)`으로 합칠 수 있다. claimed-time과 원래 로스터는 독립 표본이 아니므로 중복 학습하면 안 된다. ARCHIVE와 실제 픽스터 표본도 같은 공식 경기가 겹칠 수 있어 시각 분리와 경기 중복 제거가 필요하다.
- `audit.jsonl.gz`, `summary.json`, `calendar-summary.json`: 누락·범위.
- `source-manifest.json`, `calendar-source-manifest.json`: 입력 파일 SHA256, 원본 응답 SHA256, 실제 수집 시각, 외부 로컬 경로 및 URL. 선택 로그만 `calendar-selected-inputs.jsonl.gz`에 압축했다. 전체 ARCHIVE 원본은 복사하지 않았다.
- `protocol.json`, `calendar-protocol.json`: ROI를 읽기 전 고정한 선택·정의. 실제 결과/배당/타깃값은 피처에 포함하지 않는다.

## 1. 실제 시각을 사용한 소규모 추출

공식 MLB Stats API의 target schedule은 경기 ID, 양 팀, kickoff 검증에만 썼다. 대상 경기 최종 라인업·투구 결과를 피처로 읽지 않는다. 이전 경기 live feed의 최종 플레이 `endTime`이 의사결정 시각보다 엄격히 빠른 경우에만 해당 경기 boxscore의 개별 구원투수 투구 수를 사용한다. 정규시즌·포스트시즌만 사용하며 시범경기는 제외한다.

홈·원정 각각 다음 4개 피처를 추출했다.

1. `relief_pitches_48h`: 최근 48시간 구원투수 투구 수 합.
2. `relief_pitches_72h`: 최근 72시간 합.
3. `relievers_ge30_pitches_72h`: 같은 72시간에 누적 30구 이상 던진 구원투수 수.
4. `relievers_used_ge2_dates_72h`: 같은 기간 UTC 날짜 2개 이상에 등판한 구원투수 수. 더블헤더 두 경기는 합산하되 같은 날을 2일로 세지 않는다.

`max_fact_time`은 실제 최종 플레이 시각이며 `observed_at`은 이번 HTTP 응답의 실제 수집 시각이다. 과거로 소급한 first-capture는 없다. 전부 `retrospective_lagged`이며 `captured_pregame`은 0행이다. 마지막 플레이는 공식 경기 종료 공시와 같다는 보장은 없고 정정·사후 갱신 위험도 남는다. 현재 불펜 명단·건강·선발 확정 여부를 알 수 없으므로 가용성의 직접 증거로 부르면 안 된다.

첫 5개 서로 다른 MLB 경기만 대상으로 API 사용량을 제한했다. 결과는 MLB:813025, MLB:824541, MLB:824539, MLB:823159, MLB:824458이다. 기본+claimed-time 두 기준을 각각 추출해 10행이다. 엄격한 스냅샷 이력 없이 선발 예상 이닝이나 대상 경기 타자 splits를 사후 확정 라인업에서 복원하지 않았다.

기준 로스터의 야구 1,037행 중 MLB 338행, KBO 451행, NPB 248행이다. 소규모 추출에서 MLB 328행은 대상 선택 범위 밖, KBO·NPB 699행은 확인한 로컬 자료에서 검증 가능한 투구 로그 미확보다. 이 숫자는 시장별/로스터별 행 수이며 고유 경기 수와 다르다.

## 2. 넓은 아카이브를 위한 구조적 날짜 지연 피처

기존 `baseball-event-form-bullpen-20261029` 작업의 공식 schedule 3개 시즌, team-season pitching logs와 공식 box repair를 읽기 전용으로 재사용했다. full-game season totals나 target lineup은 사용하지 않았다. 공식 repair가 있는 경기는 원래 로그를 전부 대체했다. 재개 경기 표식이 있는 원천 경기는 제외했다.

추가 피처는 `home_relievers_ge30_pitches_lagged_dates_2_4`, `away_relievers_ge30_pitches_lagged_dates_2_4`다. 의사결정 UTC 날짜 기준 2~4일 전 날짜에 던진 투구 수를 선수별로 합산해 30구 이상인 선수 수를 센다. 당일·전날·대상 경기·미래 날짜를 제외한다. 양 팀 중 하나라도 해당 기간 실제 구원 등판 기록이 없으면 0으로 채우지 않고 행을 누락한다.

이 자료는 정확한 종료 시각이 없으므로 `max_fact_time=null`이다. `observed_at`은 원본 로그의 실제 `collected_at`이고 evidence tier는 `retrospective_lagged`다. 날짜 지연은 구조적 가정이며 실제 시각 인증을 대체하지 않는다. 부모의 엄격한 as-of gate에서 null을 제외하거나, 명시적으로 구조적 지연 가정을 허용하는 별도 보조 실험으로만 사용해야 한다. 이 자료가 경기 전 실제 수집되었다는 주장은 하지 않는다.

ARCHIVE는 명시적 한영 팀명 교차표 + 양 팀 방향 + kickoff 30분 이내로 유일하게 연결한 경우만 내보냈다. 점수·결과로 연결하지 않았다. 실제 픽스터의 MLB ID는 공식 schedule ID로 연결했다. 원본 로스터·ARCHIVE 전체에서 MLB 8,589개 키를 검사했고, 2,617개는 schedule/유일 연결이 없고 172개는 양 팀의 해당 지연 기간 구원 로그가 부족해 제외했다. 내보낸 5,800행은 고유 경기 수나 독립 표본 수와 다르다. 선택 투구 원천은 37,955개 `(gamePk,player,team_id)` 기록이다.

ARCHIVE는 settled-only retrospective market proxy이며 서비스 모델/실제 픽스터 성과가 아니다. 넓은 보조 학습과 별도 진단에만 쓴다. 이 작업에서는 학습·ROI 산출을 하지 않았다.

## 3. 새로움과 한계

기존 `src/{kbo_frozen_candidate,mlb_frozen_candidate,pitcher_form,npb_lineups}.py` 및 과거 event-form/bullpen 작업을 조사했다. 기존 연구에 날짜 단위 relief_pitches1/3와 연속 사용 피처가 있으므로 불펜 분석 자체가 신규라고 주장하지 않는다. 이번 추가는 정확한 의사결정별 48/72시간 범위, 누적 30구 이상 투수 수, 실제 픽스터/ARCHIVE 계약 행 연결이다. 날짜 지연 보조 피처도 이 별도 정의로 고정했다. 가용성·건강·현재 명단은 미확보다.

순방향 로컬 capture CLI도 구현하고 실행했다. 공식 향후 7일 schedule과 probable pitcher를 실제 수집 시각·해시와 저장하되 미래 kickoff만 채택한다. 이번 응답에서는 조건에 맞는 미래 경기가 0개여서 캡처된 미래 fixture는 없다. 이를 성공한 confirmed lineup 수집으로 부르지 않는다. cron·배포는 없다.

공개 read-only 호출은 39개 기본 raw 응답과 순방향 1개 응답으로 총 40개다. 요청 timeout 20초, 개별 응답 12MB 한도, 기본 extraction 최대 48개 캐시/요청 한도를 둔다. 재실행 시 캐시를 읽으며 원래 수집 시각을 유지한다. 수집 실패 시 추가 기능을 꾸며내지 않고 audit를 남긴다.

## 재현·검증

`python -m unittest discover -s tests -p test_rd_baseball_features.py -v`

CLI `--roster`는 복수 gzip JSONL 기준 로스터, `--output`은 위 산출물 루트다. 기본 extraction은 `--max-events 5 --max-requests 48`. 날짜 지연 추가 추출은 같은 로스터 옵션에 `--archive <ARCHIVE/events.jsonl.gz> --local-history <baseball-event-form-bullpen-20261029/sources>`를 더한다. 순방향은 `--capture-forward --max-requests 1`이다. 정확한 원본 절대 경로와 해시는 manifest에 기록했다.

회귀 테스트는 미래·동일 cutoff·대상 경기 제외, doubleheader와 중복, 48/72시간 경계, 날짜 지연, 투구 수 누락, 9인 라인업 완전성, 시즌 최종 통계 무시, timezone 필수를 확인한다. 원본과 산출물 해시 및 실제 evidence timestamp 검증은 `validation.json`과 `artifact-hashes.json`에 남긴다. 미래 ROI 개선은 검증하지 않았고 생산 채택·병합·배포를 하지 않았다.
