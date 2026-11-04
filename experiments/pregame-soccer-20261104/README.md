# 축구 원천 감사와 수집 인계 (2026-11-04)

요청은 동일 경기·동일 프로토 배당으로 시장, 투표, 9:1, 선수모델을 비교하는 것이다. 이 작업은 자료 확보 및 변환이며 비교 결과가 아니다. 축구 선수모델의 존재·동결 예측과 적용 리그를 이번에 확정하지 못했다. 새 모델을 M0라고 부르지 않는다. 해당 입력은 이 실행에서 `not_applicable` 또는 미확보로 처리해야 하며 기존 모델이 없다고 단정하지 않는다.

## 실제 확보와 시점

- 기존 `C:/Users/user/Documents/Codex/klprice` 커밋 `4f3ee6b2`의 `joined-prices.jsonl.gz` 919개를 읽어 공통 형식 가격 919개로 변환했다. 2025 K1 219, K2 270, 2026 K1 193, K2 237. 리그 공식 ID, 베트맨 회차·경기번호, 가격과 수신 시각, 원문 해시를 유지한다. 출처는 기존 작업의 원문·교차검증이며 이 실행에서 919건 전부를 독립 재수집하거나 재검증하지 않았다. 응답 수신 시각은 과거 경기 후 시각이므로 `retrospective`; 실제 당시 구매 가능 배당 증명 0건이다.
- 공식 K리그 경기정보 API를 순차 4회 신규 요청(동시 1, timeout 15초, 재시도 0)하여 2025/2026 K1/K2 각각 한 경기의 종료 점수가 해당 기존 가격 연결의 점수와 일치하는 것을 확인했다. 결과 레코드 4개, 2026-11-04 실제 수신이다. 요청 파라미터·시각·SHA와 gzip 원문은 `C:/Users/user/Documents/Codex/pregame-soccer-evidence`에 있다. 기존 공식 자료와 중복되는 네 경기만 독립 표본 검증 목적으로 재조회했다. 이후 같은 인자는 해시 검증 캐시를 재사용한다. 60회 상한 대비 4회.
- 총 교환 레코드 923개(가격 919, 결과 4), 픽스터 0, 라인업 0, 경기 전 수신이 입증된 가격·라인업·픽스터 0. 나머지 915경기는 이 실행에서 별도 공식 결과 레코드를 만들지 않았다. 기존 가격 연결 내부의 점수는 교차검증 정보이며 독립 신규 결과 수집으로 세지 않았다. K리그 외 리그 교환 자료는 이번 실행에서 확보하지 못했다.
- 공식 원본 이전 작업의 분모: 955 종료 경기 중 36 미연결(회차 후보 없음 19, 선택지 판매제한 17); 48 미종료. 발매 기간 바깥 회차 가능성도 남는다. 919건은 최종 전 기간 완전 수집이나 경기 전 가격 증명이 아니다.

## 기존 자료 감사

- `src/lineup_soccer.py`는 로컬 `data/raw/detail/kleague_soccer_2023_2026.json`을 읽고 분석 데이터를 만든다. 코드에 `runtime_db` 의존성이 있어 이 작업에서는 실행하지 않았다. `context_features.py`와 `generate_v2.py`가 데이터를 참조하지만 이 사실만으로 해당 수집이 현재 자동 실행되거나 당시 경기 전 스냅샷이 존재함을 증명하지 않는다.
- `origin/codex/kleague1-official-lineup-20261103`의 기록은 2026 종료 204경기 선발 22명 및 결과 확보, 2025는 1경기 시범 확보, 과거 경기 전 라인업 수신 증명 0으로 보고한다. 별도 `origin/codex/kleague2-official-lineup-20261103`에는 2025/2026 코호트와 가격 브리지가 있다. 양쪽 `lineup-challenger` 브랜치도 확인했으나 이번 교환 파일에 라인업이나 모델 예측을 연결하지 않았다. 과거 자료의 주장 시각을 실제 당시 수신으로 취급하지 않는다.
- `tipster-year-validation-20261028`에 종목별 원문 및 수신·게시 주장 필드가 존재한다. 원문 픽과 이번 919경기 간 일치 및 경기 전 수신은 이번 실행에서 검증하지 못했고 픽스터 레코드는 내보내지 않았다. 자동 운영 수집 연결도 확인하지 못했다.
- `src/sports_history_collect.py`는 축구 소스 어댑터 호출 경로이나 스케줄 실행의 존재와 현재 동작은 검증하지 않았다. 운영 접근은 하지 않았다.

## 재현 및 한계

`python -X utf8 scripts/collect_soccer_comparison_sources.py --prices C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/joined-prices.jsonl.gz --evidence C:/Users/user/Documents/Codex/pregame-soccer-evidence --output experiments/pregame-soccer-20261104/records.jsonl --spot-check`

소스에서 이미 확정한 정확한 연맹 경기 ID를 사용한다. 팀명 fuzzy join이나 새 교차 매칭은 하지 않는다. `source_fixture_id`는 payload에 별도 보존한다. 입력은 기존 작업의 원천 해시 및 연결 규칙을 신뢰하므로 완전 독립 재현은 해당 작업의 원문과 join 스크립트가 필요하다. 단일 요청 실패는 즉시 중단하며 접근 제한 우회나 자동 재시도는 없다. 검증: `python -X utf8 -m unittest discover -s tests -p test_soccer_comparison_sources.py -v`.

메인 통합에 필요한 남은 작업은 실제 경기 전 수신 증명 있는 원문 픽·라인업·가격과 동결 모델 출력을 동일 공식 ID 및 동일 상품에 연결하고, 공개 시각 주장만 있는 레코드를 별도 분리하는 것이다. 경기 전 검증 가능한 자료가 없으면 수익률 비교를 생성하지 않는다.

## 후속: 시즌 목록과 명단 재개 수집 (2026-11-04)

`--season-collect`는 2025·2026 K리그1/2의 월별 공식 일정 48페이지를 페이지 단위 캐시로 확보한다. 기존 46페이지는 이전 작업의 원문 해시와 실제 수신 시각을 검증해 그대로 가져왔고, 2025 K1의 1월·12월 두 페이지는 새로 받았다. 페이지 원문과 메타데이터는 Git 밖 evidence/season에 있다. 1,003 일정과 그중 종료 955경기 결과를 내보냈다. 리그·시즌별 일정/종료는 2025 K1 228/228, K2 275/275, 2026 K1 228/204, K2 272/248이다. 기존 919 배당과 조인하지 않았으므로 두 파일은 source_fixture_id를 기준으로 메인에서 대조해야 한다.

시즌별·리그별로 순환 선택해 각 한 경기의 공식 페이지에서 팀 ID와 날짜, 양팀 각각 11명의 고유 선수 ID를 확인했다. 완결된 기록은 2025 K1/K2 각 1, 2026 K1/K2 각 1이다. 모두 현재 받은 경기 후 원문으로 `retrospective`; 나머지 종료 경기 951건은 이 출력에서 명단 미확보다. 다른 기존 아카이브에 이미 있는 명단도 이 컬렉터로 아직 가져오지는 않았다. 페이지가 비거나 부분 명단이면 `not_displayed_on_official_page` 또는 `partial_or_unparseable`로 구분하며, 공개되지 않았다고 단정하지 않는다.

2026 K1 경기 205의 사전 공식 페이지에는 선발명단이 표시되지 않았고, 공식 matchInfo API 응답의 data에는 lineup 필드가 없었다. 이는 해당 응답과 시점에 한정된 관측이다. 다른 공식 채널의 미공개 여부, 이후 변경 여부, 확정명단의 공표 시각은 증명하지 않는다. HTML 수신은 실제 시각만 기록하며 선발 확정이나 역사적 사전 수신을 인증하지 않는다.

이 후속 작업의 새 HTTP는 총 16회(일정 API 2회, 과거 경기 페이지 7회, 미종료 경기 페이지 3회, matchInfo API 4회)이며 순차 실행했다. 이 중 경기정보 API 1회는 응답 구조를 확인한 탐색 호출이어서 원문을 저장하지 못했고, 이후 별도 재요청한 응답 3회는 원문과 실제 수신 시각을 보존했다. 재시도 0, 개별 실행당 최대 60회, timeout 10/15초, 응답 3MB 상한, 오류 즉시 중단이다. 사전 수신 입증된 과거 명단은 0건이다.

고정 스크립트와 명시 인자로 실행:

```
python -X utf8 scripts/collect_soccer_comparison_sources.py --season-collect --evidence C:/Users/user/Documents/Codex/pregame-soccer-evidence/season --output experiments/pregame-soccer-20261104/season-records.jsonl --years 2025 2026 --leagues 1 2 --max-requests 60 --max-lineups 4 --probe-unfinished --forecast-fixture kleague:2026:1:205 --refresh --capture-id 20261104-first
```

`--max-lineups`는 4개 고정이 아니라 전체 종료 경기 중 누적 처리할 페이지 수다. 예를 들어 60까지 늘리면 이미 받은 페이지는 재사용하고 나머지는 요청 예산 안에서 수집한다. 예산이 소진되면 중단하며 다음 실행에서 캐시로 재개한다. 명단이 48페이지의 누락분과 함께 60회 예산에 다 들어가지 않으면 예산을 넘기지 않고 중단하므로 다시 실행해야 한다. 일정 캐시는 변경되지 않으며 갱신이 필요하면 새 evidence 디렉터리를 사용한다. `--refresh --capture-id <새 ID>`는 해당 ID의 경기 페이지·API 원문과 관측 레코드를 새 파일로 남기고 이전 관측을 수정하지 않는다. 같은 ID 재실행은 해시를 확인해 재개하며 내용 충돌은 중단한다. 임의의 미종료 경기 ID는 `--forecast-fixture`로 고른다. 기존 출처의 게시 주장이나 수신 시각을 새로 만들지 않는다.
