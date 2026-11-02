# K리그·KBO 보존 입력 연결 — 2026-11-02

운영 모델과 원장을 변경하지 않은 오프라인 복원이다. 모델 학습이나 적중률 비교는 실행하지 않았다. 자료의 SHA-256은 보존 바이트의 무결성만 확인하며 과거 수신 시각을 증명하지 않는다.

## 복원 범위와 분모

- K리그1 2025 공식 일정 원문 228경기 중 227경기에서 홈·원정, 날짜, 시간, 점수와 전체 경기 3방향 배당 원자료를 대조했다. 미연결은 원자료 1경기, 중복 배당 키는 0건이다. 228경기에 대해 네이버 보존 명단 원문을 날짜·양 팀·점수로 연결했다. 선수 ID는 공급자 ID이고 공식 등록 ID와의 일반적 동치는 입증하지 않았다. 명단과 가격의 당시 수신 시각, 운영 동결 revision은 확인되지 않았다.
- KBO 2026-03-28~04-04: #425의 55개 후보 중 같은 실제 경기 20개가 1차·보조 양쪽에 반복돼 공식 ID 기준 35경기다. 원문 VIEWSTATE에서 경기 ID, 홈·원정 팀 ID, 최종 점수 및 선수 ID를 검산했고 35경기 중 34경기의 2방향 원배당·같은 경기 결과를 대조했다. 나머지 1경기 삼성–두산은 5–5 무승부로 기록됐으며 원본의 1.00/1.00은 취소 정산 값으로 보존하고 사전 배당으로 취급하지 않았다. 역사적 가격 관측 시각과 동결 revision의 증거는 없다. `source_case_rows`는 원본 후보의 중복 수다.
- KBO 2025 보존 증거 4건은 별도 파일이다. 사후 관측 1건, 언론의 과거 발표 시각 주장 2건, 시간 미확인 기사 1건이며 실제 경기 전 수신의 증거가 아니다. 현지 시간 기사 주장과 독립 수신·버전 증명은 다르다.
- 완전한 사전 확정 선수 명단·정확한 동결 revision·전 선택지 가격·공식 정산을 모두 입증한 표본은 **0건**이다. 위 회고 연결 건수는 예측 성능 검증 표본 수가 아니다.

## 추가 운영 스냅샷: 2026-11-01 삼성–KIA

실제 보존 공식 응답 바이트에서 `20261101HTSS0`, 삼성 홈 `SS`, KIA 원정 `HT`, 14:00 KST와 예고 선발 홈 후라도 `53375`, 원정 올러 `55633`을 확인했다. 공식 응답 수신 기록은 10월 31일 10:25:18 KST다. 당시 운영 선수 입력의 동일 경기 ID·두 선수 이름·관측시각과 경기 전 revision `dec_3af20241522c0154`를 연결했다. 입력은 예고 선발이며 확정 9인 타순은 없다.

부분 원장 256행에는 해당 경기의 사전 revision 1행이 있다. 2026-10-31 14:13:11 KST에 저장됐고 `market_reference`, 홈 선택과 같은 revision 내부 양측 가격 1.73/1.75가 보존돼 있다. 이 값은 모의 거래나 최종 동결 증명이 아니다. 별도 배당 696행은 8개 상품 각 87개 관측이며, 같은 값의 경기 후보 1856이 87건이다. 해당 자료만으로 상품의 전체 경기 여부 및 revision의 배당 관측시각과 정확히 같은 원행은 확인하지 못했다. 다른 상품 1860도 2방향이므로 가격만으로 동일 상품이라 확정하지 않는다.

운영 공식 일정 캐시는 11월 1일 삼성 6–1 KIA를 기록하지만 결과 응답 원문은 이번 입력에 없어 독립적으로 검증하지 못했다. 이 revision의 정산은 전달된 부분 원장에서 발견하지 못했다. 전달된 256행은 각 해시 256/256, 연속 연결 255/255가 일치하고 일련번호는 8766~9021이나 그 이전 체인은 확인하지 않았다. 로컬 수신 로그와 바이트가 있어도 제3자 수신 인증은 없다. canonical_game_id는 소급 생성하지 않았다.

## 재현 및 자료

- `python experiments/klkbo-restoration-20261102/reconcile.py --worktrees C:/Users/user/Documents/ChatGPT/sportstoto-worktrees --lineup-raw C:/Users/user/Documents/ChatGPT/sportstoto/data/raw/detail/kleague_soccer_2023_2026.json --out experiments/klkbo-restoration-20261102/output`
- `python experiments/klkbo-restoration-20261102/forward_link.py --snapshot C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/recover-inputs-new-model-20261102/experiments/input-link-recovery-20261102/production-snapshot.json --capture experiments/verified-fact-capture-20261031/capture-with-bytes.json --out experiments/klkbo-restoration-20261102/output/kbo-forward.json`
- `python -m pytest tests/test_klkbo_restoration.py -q`

복원·원장 관련 `python -m pytest tests/test_klkbo_restoration.py tests/test_prediction_ledger.py -q`는 24건 통과했다. 전체 `python -m pytest -q`는 초반 진행 후 출력 없이 오래 대기하여 수동 중단했으므로 전체 통과가 아니다.

`output/cohort.jsonl`은 263개 고유 경기, `output/kbo-2025-evidence.jsonl`은 별도 4건이다. `output/manifest.json`은 원본 경로·해시와 사유별 건수를 남긴다. 사유는 중복 집계되며 서로 더해 전체 분모로 취급하지 않는다. 신규 운영 스냅샷 해시는 `608d4cea5efbe3ce847dcd1850c281abb3025bae823db6458bcb7df2210155c4`다.

참조 PR #424, #425, #440, #451, #455, #456, #459, #482, #484, #485, #486의 head를 확보해 살펴봤으며, 위 산출물은 특히 #425 원자료와 #485 일정·증거 원문을 직접 다시 읽는다. 2025 K리그 결과 원문은 수집 당시의 사후 조회이고 원본 국내 배당 CSV의 소스 수신 근거가 없다. KBO 원문도 2026년 10월 30일의 사후 수집이다. 추가 공식 정산 원문과 전체 동결 체인, 동일 시점·동일 상품 가격 출처가 필요하다.
