# 픽스터 동일 경기 비교 재현 기록

이 디렉터리는 연구 코드의 실행·입력·해석 계약이다. 운영 모델 변경이나 승격이 아니다.

## 반드시 지킬 해석

- `model`은 당시 Git에 저장된 `최종확률`이다. `baseline-provenance.json`으로 59경기 원본을 재조회한 결과 모두 `시장확률`과 같다. 118개 선택지의 근거는 `shin_market` 110개, `shin_market_live` 8개다. 별도의 학습 모델과 픽스터를 비교했다고 표현하지 않는다.
- 혼합 비중은 **저장 운영 확률 : 픽스터 작성자 투표 비중**이다. 75:25를 반대로 읽지 않는다. 투표 비중은 보정된 적중 확률이 아니다.
- MLB 59경기, 25명 작성자, 2026-08-30·08-31·09-01·09-03·09-04의 5개 날짜다. 58,995개 원본 로그 행은 경기 수가 아니다. 작성자 3명 이상인 부분집합은 30경기다.
- `results.json`은 원본 로그의 경기 전 첫 관측을 재집계하고 기존 게시자 판정으로 계산한 결과다. 원본 파일 해시와 경기별 근거를 보존했다. 경기 전 시점 증거는 로컬 저장 로그와 Git 기록이며 독립적인 타임스탬프 인증은 아니다.
- `official-results.json`은 MLB 공식 API로 59경기 모두 정산한 후속 결과다. 기존 51건은 모두 일치하고 미정산 8건을 추가 확정했다. 최신 최종 수치는 이 파일을 사용한다. 기존 결과를 덮어쓰지 않는다.
- 모든 경기에서 같은 배당으로 단폴을 비교한 결과와, 확률·배당 필터로 선택한 추천의 결과 및 2폴 결과를 구분한다. 작성자 3명 이상 30경기의 75:25 단폴 +9%를 전체 59경기나 2폴 수익률로 제시하지 않는다.
- 2폴은 기존 구매 결정·배당·선택을 동결한 뒤 공식 결과로 최종 정산했다. 과거 공식 결과 공개 시각을 알게 된 것처럼 예산 시뮬레이션을 재작성하지 않았다. 최초 재현의 월 손실 5만 원 및 미정산 원금 예약 규칙을 보존한다. 2~3장만으로 월 수익 안정성을 입증하지 못한다.
- 국내 종목 16건은 사후 수집 자료로 별도 분리했다. KBO 10·농구 3·축구 2·배구 1이며 이번 공식 MLB 결과 검증에 포함하지 않는다. 사이트 전체나 모든 픽스터를 비교한 것이 아니다.
- 여섯 고정 비중은 이미 탐색했던 작은 표본의 재검산이다. 새로운 미사용 평가셋이나 최적 비중 검증이 아니며 채택·배포하지 않는다.

## 실행

저장소 루트에서 실행한다. 입력 경로는 해당 원본 파일이 있는 위치로 바꿀 수 있지만 해시는 일치해야 한다. 기존 출력은 덮어쓰지 않으므로 재실행 시 별도 출력 폴더를 사용한다.

```powershell
python -X utf8 scripts/compare_matched_tipsters.py --selection ../selection-policy-comparison-20261025/experiments/selection-policy-20261025/results-round-checked.json --raw ../selection-policy-comparison-20261025/data/raw/picksters/tailslips_pick_events.jsonl --backfill ../multisport-hybrid-20261025/experiments/multisport-fixed-hybrid-20261025/results.json --output experiments/matched-tipsters-rerun/results.json
python -X utf8 scripts/verify_tipster_mlb_results.py --input experiments/matched-tipsters-rerun/results.json --output experiments/matched-tipsters-rerun/official-results.json
python -X utf8 scripts/audit_matched_baseline.py --input experiments/matched-tipsters-rerun/results.json --output experiments/matched-tipsters-rerun/baseline-provenance.json
python -X utf8 -m pytest -q tests/test_matched_tipsters.py
```

원본 로그는 별도 연구 작업 폴더에 있으며 이 PR에 복제하지 않는다. 따라서 새 clone만으로 원본 로그 재집계까지 수행할 수 있다고 주장하지 않는다. 공식 응답 본문과 경기별 비교 근거는 결과 JSON에 포함했다. 확률 출처 감사에는 해당 snapshot 커밋을 포함한 Git 이력이 필요하다.

공식 결과 출처: https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-08-30&endDate=2026-09-04
