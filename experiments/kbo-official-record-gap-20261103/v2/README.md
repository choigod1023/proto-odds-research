# KBO 공식 일자별 기록 보강 v2

고정된 공식 v1 256경기의 선수별 AB·H에 실제 경기 BB가 없어 기존 모델의 `(H+BB)/(AB+BB)` 이력에 사용할 수 없는 문제를 별도 supplement로 보강한다. v1, 원문, 726경기 명단과 기존 모델 입력은 수정하지 않았다. 동일한 256경기를 결과·손익과 무관하게 대상으로 삼았다.

## 실제 원천 확인

기존 네이버 공개 `schedule/games/{gameId}/record` 경로에 2026-07-28, 08-29, 10-27 세 경기를 조회했으나 모두 읽기 시간 초과였다. 첫 경기는 한 번 재시도해 총 4회였고 HTTP 응답이나 원문은 받지 못했다. `naver-probe-v2/samples/`의 세 JSON은 후속 시도의 오류와 URL을 남긴다. 처음 요청의 별도 오류 메타데이터는 남기지 못했다. 시간 초과를 원천에 기록이 없다는 뜻으로 해석하지 않는다. 네이버 값은 이 결과에 사용하지 않았다.

대안으로 [KBO 공식 타자 일자별 기록](https://www.koreabaseball.com/Record/Player/HitterDetail/Daily.aspx)의 실제 경기별 `AB`, `H`, `R`, `RBI`, `BB`, `HBP`, `HR`, `SO`, `2B`, `3B` 열을 확인했다. 세 경기·선수 표본에서 v1의 동일 경기 타수·안타·득점·타점과 일치했고, 정규 시즌과 포스트시즌 선택 상태도 검증했다. 결과는 타석 문자 추정이나 시즌 최종 BB·OPS가 아니라 이 공식 일자별 숫자다. 일자별 페이지에는 경기 점수가 없어 점수를 이 페이지로 독립 검증했다고 주장하지 않는다.

선수 ID와 공식 프로필 이름, 시즌·시리즈 선택, 날짜·상대, 원본 공식 경기 일정의 유일성, AB·H·R·RBI가 일치할 때만 숫자를 연결했다. 같은 날짜에 동일 양 팀이 홈·원정을 바꿔 두 경기인 경우를 포함해 더블헤더가 모호하면 보류한다. 현재 프로필의 소속팀으로 과거 이적 선수를 배제하지 않는다. 음수나 불가능한 PA·AB·H 관계도 보류한다. 값이 충돌하면 원래 v1 값과 공식 일자별 행을 모두 남기고 결합용 BB 등은 `null`로 둔다.

## 범위와 결과

- 선수·시리즈 작업 306개 중 **153개**의 공식 일자별 페이지를 확보했다. 정규시즌 선수 ID 오름차순을 먼저 처리하고, 이후 기존 캐시로 읽을 수 있는 다른 시리즈를 처리했다. 결과나 수익률로 선별하지 않았다.
- 전체 타자 6,965행 중 **3,939행**에서 공식 일자별 BB 등을 연결했다. **2,799행**은 요청 한도 내에서 해당 일자별 페이지를 받지 못했고, **227행**은 v1 선수 ID가 없어 연결하지 않았다. 값 충돌로 선택을 보류한 행은 0건이다. 같은 팀의 모든 선수가 확보된 사례는 없어 상대 투수 SO·4사구·홈런 합계 전체 대조는 **512팀 모두 미검증**이다.
- 3,939행 중 `AB+BB=0`인 537행은 분모가 없어 scalar 계산에 기여하지 않는다. `prior_batter_history`는 누락·충돌 3,026행과 이 537행을 제외해, 2026-11-03 기준 **3,402행**을 반환한다. 256경기 중 252경기에서 일부 타자의 BB가 연결됐지만 양 팀 전원 완전한 경기는 없다.
- 투수 2,577행 중 v1에서 이미 ID가 있던 행은 1,760행이다. 공식 경기기록의 숫자 ID 주장과 같은 경기·팀·역할의 이름을 공식 선수 프로필과 대조해 **53행**을 추가했고, **764행**은 식별 근거가 없어 비웠다. 현재 프로필 팀을 과거 팀으로 강제하지 않는다.
- 공식 일자별 원천은 수집 당시의 KBO 페이지다. 과거 경기 전에 실제로 수신했다는 증거는 없다. 다음 날 완료는 회고 가정이며 동일 날짜·미완료 경기는 과거 이력에 넣지 않는다. 경기 점수나 대상 경기의 결과를 피처로 내보내지 않는다. 원래 고정 모델에 자동 주입하거나 학습하지 않았다.

## 요청과 제외한 초기 표본

직접 원천 요청은 네이버 4회, KBO 초기 표본 GET 3회·POST 2회, 본 수집 290회로 **총 299회**였다. 본 수집은 요청마다 별도의 익명 요청을 만들고 쿠키를 지속하지 않으며, 1초 이상 간격·timeout·403/429 중단·단일 수집기 잠금·캐시를 적용했다. 별도의 공개 웹 검색은 이 원천 요청 횟수와 구분한다.

초기 KBO 표본 GET 세 건은 세션 객체로 실행했다. 당시 `Set-Cookie` 응답 헤더를 보존하지 않아 서버가 쿠키를 발급하고 후속 GET에 전달됐는지 확인할 수 없다. 이에 초기 세 선수의 GET·그 입력에 의존한 POST 등 **6개 응답을 최종 보강값에서 제외**했다. 원문은 삭제하지 않고 raw 묶음에서 `eligible_for_supplement=false`로 표시했다. 최종 수집에 사용한 데이터는 세션 재사용 없는 요청에서만 왔고, 출력된 값이나 POST의 폼 원본이 제외된 응답에 의존하지 않음을 별도로 검사했다. 공개 GET의 폼 상태 원문은 raw로 보존하며 POST 메타데이터는 그 원문 SHA와 선택값을 가리킨다. 쿠키나 계정 정보를 출력하거나 저장하지 않았다.

## 파일·결합 규약

- [보강 결과](artifacts/supplement-v2.jsonl.gz): SHA-256 `0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84`, 압축 해제 SHA-256 `ffee5a9197861fbea847310e0a2543170c5fdc0582632cbf6cc7fe005c221451`.
- [공식 응답 원문 묶음](artifacts/official-daily-raw.jsonl.gz): SHA-256 `53ed7b68ee3c8c2f642f114bff7138d28d80c01932b1841911e19032a543ac39`. 295개 KBO 응답의 URL·메서드·쿼리·상태·현재 수신 시각·SHA·원문 바이트를 포함하며, 위 6건은 제외 표시다. 4개의 네이버 요청은 응답을 받지 못했다.
- [독립 감사](audit-final.json)는 3,939행의 원문·프로필·날짜·대진·AB/H/R/RBI와 53개 투수 ID를 다시 확인하고 target-day 차단을 검사했다. `scripts/research_kbo_daily_merge_contract.py`의 `load_verified_sources`는 v1과 supplement의 압축 SHA를 확인하고, `prior_batter_history`는 경기·팀·선수·행 번호와 공식 원본값을 확인한 뒤 과거 완결 경기의 유효한 AB/H/BB만 반환한다. `verified_pitcher_ids`는 원래 비어 있던 ID, 동일 숫자 주장과 공식 프로필 근거를 확인한다. 어느 함수도 결과 점수나 현재 경기 통계를 반환하지 않는다.
- v1 정규화 SHA-256 `e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334`, v1 원문 묶음 SHA-256 `8aeaf579f7ac84a642c3017810f8cf42c101bda2f6290a7fa3779a1d6fa72500`, 726경기 명단 SHA-256 `f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3`은 전후 동일하다.

이 결과는 부분 수집이다. 후속으로 더 수집할 때는 아래에서 `--offline`을 빼고 새 snapshot 이름을 지정하며 별도 요청 한도를 적용한다. 기존 snapshot은 덮어쓰지 않는다.

```powershell
python scripts/research_kbo_daily_supplement.py `
  --v1 experiments/kbo-official-record-gap-20261103/artifacts/normalized-records.jsonl.gz `
  --lineups C:/Users/user/Documents/Codex/klu/experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl `
  --lineup-raw C:/Users/user/Documents/Codex/klu/.research-state/kbo-lineups-v5/raw `
  --output .research-state/kbo-daily-cookiefree-v3 --snapshot next-snapshot.jsonl.gz --offline --max-requests 1
python scripts/audit_kbo_daily_supplement.py `
  --v1 experiments/kbo-official-record-gap-20261103/artifacts/normalized-records.jsonl.gz `
  --v1-raw experiments/kbo-official-record-gap-20261103/artifacts/raw-responses.jsonl.gz `
  --supplement experiments/kbo-official-record-gap-20261103/v2/artifacts/supplement-v2.jsonl.gz `
  --daily-raw experiments/kbo-official-record-gap-20261103/v2/artifacts/official-daily-raw.jsonl.gz
```
