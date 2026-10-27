# 픽스터 예상의 추가 효과 검증 — 2026-10-25

## 실제 실행 결과

`audit_frozen_tipsters.py`로 저장된 TailSlips 이벤트 58,995행을 다시 검사했다.
서로 다른 pick_id 27,483개 중 최초 미판정 경기 전 단폴 조건 제외 17,335개,
최초 배당 부재/부적합 5,147개, 최초 배당은 있으나 같은 ID의 결과가 없는 픽
5,001개다. 검증 가능한 판정 표본은 0개다. ROI/적중률은 0%가 아니라 **측정 불가**다.
입력 SHA-256과 제외 수는 `historical-audit.json`에 저장했다.
이것은 로컬 보존 로그(2026-08-29~09-05) 검사이지 최신 운영 DB 전체 검사 결과가 아니다.

## 재현된 연결 결함과 수정

실제 로그의 `NYY ML -115 5u`(08-29 10:08:28 UTC)와
`NYY ML -115`(21:03:04 UTC)는 투자 단위 접미사가 사라져 ID가 달랐다.
기존 자체 테스트는 경기 전 투자 단위가 없는 예제라 이 결함을 잡지 못했다.
v3는 끝의 투자 단위만 제거하고 시장·라인·배당은 보존한다.
원문은 raw_text에 남긴다. v3 전환 첫 스냅샷은 baseline으로 처리한다.
기존 상태/로그를 삭제하거나 과거 자격을 새 ID로 승계하지 않는다.
기존 v2 로그는 기존 ID로만 해석한다. 실제 수집 루프의 first_observed→graded를
모의 HTML로 검증했다. **운영 배포 또는 실제 경기 후 연결 확인은 아직 아니다.**

추가로 날짜가 수집 시점 미국 동부 날짜에 의존하고 공식 경기 ID가 없다.
이전 경기 결과가 오늘 슬레이트에 남거나 더블헤더인 경우 이름/날짜만으로
사후 결합하면 안 된다. 위 수정은 이 별도 문제까지 해결하지 않는다.

## 앞으로 경기의 실제 의견 수집

robots 허용 확인 후 공개 HTML을 제한적으로 직접 가져와 아래 두 예상만 구조화했다.
관측 시각과 게시/수정 시각, 원문 해시를 분리 저장했다. 확률은 생성하지 않았다.

- Sevilla–Osasuna: 1–1 예상, `sevilla-opinion.json`.
- Paris Saint-Germain–Lyon: 2–1 예상, `psg-opinion.json`.

출처:
- https://www.sportsmole.co.uk/football/sevilla/preview/sevilla-vs-osasuna-prediction-team-news-lineups_607645.html
- https://www.sportsmole.co.uk/football/psg/preview/psg-vs-lyon-prediction-team-news-lineups_607509.html

기사상 앞으로 일정이지만 공식 경기 ID/변경된 시작 시각을 확인하지 않았으므로
temporal_eligibility는 unverified다. PSG 기사는 일정 변경 문구도 있다.
두 건을 검증 완료 표본으로 세지 않는다. 자동 반복 수집/운영 추천 반영은 비활성이다.

## 세 모델 비교의 남은 조건

동일 경기·시장·예측 시점에서 A 기존 모델, B 사실 근거 추가 모델,
C 사실 근거+픽스터 예상 모델의 확률 벡터와 모델 버전을 경기 전에 고정해야 한다.
공식 일정/경기 ID, 당시 실제 구매 가능한 배당, 최종 공식 결과가 함께 필요하다.
현재 세 모델이 모두 고정된 비교 표본은 0개이며 새 학습/세 모델 비교는 실행하지 않았다.
외부 예상 점수나 찬성 비율을 교정된 확률로 간주하지 않는다.
가중치는 이전 기간에서만 학습하고 동일 경기/복제 기사 중복을 제거해야 한다.

월 손실 50,000원, 회당 10,000원·2폴 조건은 그대로다.
주 2회/3회 모의 운용, 적중률/실현 ROI/Brier/최대낙폭/월별 손익 비교는
위 자료를 확보한 뒤 수행한다. 이 실험에서 월 수익을 추정하거나 개선을 주장하지 않는다.
추후 비교 시 미정산 투자금도 손실 한도에 예약하고 경기 종료 결과로 선택을 바꾸지 않는다.

## 재현 명령

```powershell
python scripts/audit_frozen_tipsters.py --input data/raw/picksters/tailslips_pick_events.jsonl --output new-audit.json
python scripts/capture_tipster_forecasts.py --url ARTICLE_URL --output new-opinion.json
python -m pytest tests/test_frozen_tipsters.py tests/test_tipster_forecasts.py tests/test_pickster_stake_identity.py -q
```

새 출력 경로만 허용한다. 수집 스크립트는 단일 허용 도메인, 명시적 URL, robots 검사,
timeout/1MB 크기 제한, 리다이렉트 금지로 제한한다. 유료/로그인/차단을 우회하지 않는다.

검증: Python 1,373 passed / 2 skipped / 67 subtests passed,
웹 256 passed / 0 failed, Vite 별도 출력 디렉터리 빌드 성공, git diff --check 통과.
웹 테스트에 기존 HMR 포트 24678 충돌/종료 중 dependency scan 경고가 있었으며
다른 프로세스를 종료하지 않았다. 테스트 성공과 예측 성능 검증 성공은 다르다.
