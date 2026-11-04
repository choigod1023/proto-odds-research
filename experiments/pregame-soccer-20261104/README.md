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
