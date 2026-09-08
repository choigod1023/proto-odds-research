# 야구·축구 검증 실행 기록

운영 모델 변경이 아닌 독립 연구 실행이다. 결과는 [report.md](report.md), 수치는 [summary.json](summary.json)에 있다. 기존 팀 기록 재검증과 신규 정보의 수집 검증을 구분한다. 새 데이터가 많아지면 성능이 오른다고 보장하지 않는다.

## 재현

저장소 루트에서 실행한다. Python의 numpy/scipy는 requirements.txt 의존성이다. CSV 데이터 처리에는 번들 Python을 사용했고, 해당 런타임에 scipy가 없어 모델 적합은 설치되어 있던 Python/scipy로 실행했다. 새 유료 API나 패키지는 구매하지 않았다.

아래 `ARCHIVE_DATA`, `PRIVATE_RUN`, `MLB_PILOT`, `NEW_DB`, `NEW_REPORT`, `NEW_SUMMARY`는 실제 경로로 바꾼다. 원본은 읽기 전용이고 산출물은 이 작업의 별도 경로에만 둔다. 보고서 내보내기의 세 출력은 기존 파일이면 거부한다.

```text
python src/context_archive_controls.py --games ARCHIVE_DATA/processed/games.csv --output PRIVATE_RUN/controls.json
python src/league_context_validation.py --input PRIVATE_RUN/controls.json --output PRIVATE_RUN/controls-result.json --node node
python src/soccer_context_pilot.py --games ARCHIVE_DATA/processed/games.csv --xg ARCHIVE_DATA/raw/xg_snapshots.jsonl --odds-dir ARCHIVE_DATA/raw/snapshots --since 2026-07-27 --until 2026-08-25 --output PRIVATE_RUN/soccer-rows.json --audit PRIVATE_RUN/soccer-audit.json
python src/league_context_validation.py --input PRIVATE_RUN/soccer-rows.json --output PRIVATE_RUN/soccer-result.json --node node
python src/mlb_pitch_pilot.py --start 2025-06-01 --end 2025-06-14 --max-games 30 --schedule-budget 1 --feed-budget 30 --request-budget 31 --output MLB_PILOT
python src/context_validation_report.py --run-dir PRIVATE_RUN --mlb-summary MLB_PILOT/summary.json --database NEW_DB --report NEW_REPORT --summary NEW_SUMMARY
```

MLB 파일럿은 정확히 31요청으로 제한했으며 186경기 중 날짜/gamePk 순 앞 30경기만 받았다. 14일 전체를 수집한 것으로 해석하면 안 된다. HTTP 오류/리디렉션/예산 소진 시 멈추며 인증이나 차단을 우회하지 않는다. 수집기는 새 출력 디렉터리만 허용한다. 이번 수집 후 박스스코어 대조 강화는 캐시된 응답으로 오프라인 재검증했다.

아카이브 입력 SHA256:

```text
dfe95c2ace14ee42142ea0c86912b68359bc8ebee9ad3cb566812815bdec0eb0
```

## 판정 규약

- 리그를 합치지 않는다. 기준 대비 추가 보정을 검증하는 학습/내부검증/평가를 시간순으로 분리한다.
- 학습 최소 200, 내부검증/평가 각각 최소 50, 학습 각 결과 클래스 최소 5개가 있어야 적합한다. 이 숫자는 실행 하한일 뿐 통계적 검정력 보장이 아니다.
- 과거에 반복해서 분석한 기록이므로 탐색적 재검증이다. 단 한 번의 새 독립 검증이나 확증적 사전등록이라고 주장하지 않는다.
- 배당 관측시각이 없는 장기 아카이브는 실전 T-30 재현으로 판정하지 않는다.
- 축구의 batch-start+24h는 가정이다. 행별 provenance 및 검증 여부를 보존한다. 최신 취소 상태나 오래된 배당을 버리고 이전 가격을 실전 가격처럼 재사용하지 않는다.
- 축구 고정식 20경기 점검은 학습된 모델도 추천 픽 성능도 아니다. 무승부를 포함한 H/D/A 전체 경기 예측이다.
- 실제 추천 JS 함수를 호출하지만 공급된 승패/승무패 부분집합에 한정한다. 언오버·핸디캡까지 포함한 전체 사이트 시뮬레이션은 미완료다.
- 모든 결과는 production_allowed=false. 모델 승격·운영 가중치 수정·배포·재시작은 하지 않는다.

## 검증

최종 로컬 결과: Python 797개 및 하위 테스트 58개 통과, 프론트 229개 통과, 격리 Vite 프로덕션 빌드 통과. 연구 SQLite integrity_check=ok, 저장된 7개 분석 아티팩트의 SHA256 재검산 일치.

```text
python -m pytest -q
node --test --test-concurrency=1 --test-reporter=spec  # web/ 디렉터리
npx vite build --outDir ../outputs/validation-build --emptyOutDir  # web/
```

연구 코드의 회귀 테스트는 미래 입력, 날짜 경계, 무승부, 충돌한 재발매, 비정상 배당/ID, 표본 미달, 취소된 최신 배당, 결측 xG, 수치 오버플로, MLB 불완전 피드·박스스코어 불일치·누락 투구수와 HTTP 예산을 검증한다. 프론트 최초 실행은 격리 작업트리에 React가 없어 실패했고, 잠금 파일 기준 의존성 설치 후 재실행했다. 직렬 테스트로 다른 세션의 HMR 포트와 충돌을 피했으며 다른 프로세스를 종료하지 않았다.

원본 DB·상세 피처·개별 예측·빌드 파일은 private outputs에 남기고 Git에는 넣지 않는다. 보고서·요약과 재현 코드만 커밋한다.
