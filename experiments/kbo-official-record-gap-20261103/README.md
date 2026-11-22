# KBO 공식 경기기록 결손 구간 보충

기존 선수 기록의 마지막 일자는 2026-07-26이다. PR #514의 고정된 2026년 726경기 명단에서 경기일 `2026-07-27`~`2026-10-27`인 모든 경기 256건을 결과·적중 여부와 무관하게 선택했다. 공식 일정·선발 명단과 경기기록을 같은 공식 경기 ID·날짜·양 팀으로 대조한 결과, 256경기 모두 경기 후 기록을 확보했다. 경기기록 응답 자체에는 경기 ID가 없으므로 요청 인자와 공식 일정·양 팀 공식 타순의 이름·순서 대조에 의존한다. 실제 대상 첫 경기일은 2026-07-28이다. 나머지 연도 수집과 모델 학습·운영 반영은 하지 않았다.

## 결과 및 빠진 값

- 타자 6,965행 중 선수 ID 연결 6,738행, ID 미확인 227행. 공식 타순과 선수 이름·타순을 비교해 512개 팀 명단 각각 9명과 일치했다. 동일 타순의 교체 출전자를 포함하며, 표의 앞 9행을 선발로 간주하지 않는다.
- 투수 2,577행 중 선수 ID 연결 1,760행, ID 미확인 817행. `등판=선발`로 확인한 512명 중 10명은 ID가 없다. 승리 표시를 선발 표시로 사용하지 않는다. 이름·팀·기존 사후 식별자의 일치는 동명이인·원본 오류를 완전히 배제하는 독립적 신원 증명이 아니다.
- 타자 경기별 `AB`, `H`, `RBI`, `R`은 공식 헤더로 확인한 숫자다. 타자 `BB`, `HR`, `K`, `2B`, `3B`는 별도의 경기별 숫자 열을 찾지 못해 `null`이다. 이닝별 문자 결과나 시즌 타율로 숫자를 추정하지 않았다.
- 투수 이닝은 정확한 아웃 수로 저장한다. `5.2`와 `5 2/3`은 17아웃이며 `2/3`은 2아웃이다. `타자`는 상대 타자 수, `투구수`는 실제 투구수 열이다. 투수 `H`, `HR`, `K`, `R`, `ER`도 경기별 공식 숫자다. 공식 `4사구`는 볼넷과 사구가 합쳐진 값으로 따로 저장하고 순수 `BB`는 `null`이다. 시즌 누적 승패와 평균자책점은 경기별 숫자로 사용하지 않는다.
- 양 팀 전체 512개 기록에서 개인 아웃 수 합과 팀 아웃 수가 일치했고, 타자 득점 및 상대 투수 실점 합은 공식 점수와 일치했다. 타자 H가 AB를 초과하거나 투수 ER이 R을 초과한 행은 없었다. 개인 투구수가 상대 타자 수보다 적은 행이 한 건 있어 이를 오류라고 단정하지 않았다.
- 8경기에서는 팀 자책점과 투수 개인 자책점 합계가 달랐다. 원문대로 두 값을 모두 보존하고 `team_equals_individual_earned_runs=false`로 기록했다. [KBO 2026 야구규칙](https://6ptotvmi5753.edge.naverncp.com/KBO_FILE/ebook/pdf/2026_%EC%95%BC%EA%B5%AC%EA%B7%9C%EC%B9%99.pdf) PDF 186쪽(인쇄 162쪽), 9.16 항목의 설명은 팀 자책점과 개인별 합이 달라질 수 있음을 명시한다. 예를 들어 2026-07-29 `20260729HTSS0` 홈 팀은 공식 팀 자책 6, 개인 합계 7이다. 어떤 수치도 합을 맞추도록 바꾸지 않았다.

## 시간과 누출 방지

이 자료는 2026-11-03에 과거 기록을 다시 수신한 것이다. 당시 경기 전 또는 다음 날 실제로 수신했다는 증거는 없다. 과거 경기가 다음 날까지 완료됐다는 **회고 가정**을 별도 표시한다. `prior_only`는 완료된 `game_date < target_date`의 `statistics`만 반환하고 당일·미완료 경기와 `outcome`을 제외한다. 이 보수적 날짜 규칙도 실제 과거 입수나 연기·중단 경기의 완전한 시간 검증을 대신하지 않는다. 별도 supplement일 뿐 frozen 모델 입력에 자동 주입하지 않았다.

## 원문과 검증

- `artifacts/normalized-records.jsonl.gz`: 압축 SHA-256 `e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334`; 압축 해제 원문 SHA-256 `53d7c625614631452f84f67ccde618eae73f145bc1bcdf1bd391d347df67370a`.
- `artifacts/raw-responses.jsonl.gz`: 압축 SHA-256 `8aeaf579f7ac84a642c3017810f8cf42c101bda2f6290a7fa3779a1d6fa72500`; 압축 해제 원문 SHA-256 `4ebe9a26b03de375529c1a972fc61cd612b0bc8c4e1c3ac8049179cb873f3da8`. 각 행은 정확한 응답 바이트의 base64, 공식 URL, 메서드·요청 파라미터·HTTP 상태·현재 수신 시각·SHA-256이다. 공식 경기기록 응답 512건과 기존에 보존한 공식 일정 응답 66건을 담는다. 512건 모두 HTTP 200, content encoding 미적용이다.
- 원본 명단은 [PR #514](https://github.com/choigod1023/proto-odds-research/pull/514)의 `official-lineups-2026.jsonl`, SHA-256 `f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3`이며 읽기만 했다. 투수 ID의 일부는 2026-07-26 이전 기존 타자·투수 기록의 **투수 이름과 ID만** 공식 기록의 숫자 ID 및 동일 팀과 대조했다. 기존 투수 원본 SHA-256은 `c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66`이다. 기존 숫자를 결손 기간 기록으로 채우거나 수정하지 않았다.
- 공식 규칙 PDF의 현재 수신 바이트는 로컬 `.research-state/kbo-record-gap-v3/official-2026-rules-response.bin`, SHA-256 `deb2c0d58ef41c6631f47a3dcfbe37d853a97275b9891ad50f0a8525b302a16a`에 보관했다. PDF 전체는 이 PR에 포함하지 않는다.
- 실제 세 경기와 팀·개인 자책점이 다른 한 경기를 `samples/`에 보존했다. 공식 페이지·API는 [GetBoxScore](https://www.koreabaseball.com/ws/Schedule.asmx/GetBoxScore), [GetBoxScoreScroll](https://www.koreabaseball.com/ws/Schedule.asmx/GetBoxScoreScroll), [경기기록 화면](https://www.koreabaseball.com/Schedule/GameCenter/ReviewNew.aspx)이다. 공식 API는 POST다.

수집기는 단일 프로세스 잠금·최소 1초 간격·연결/읽기 시간 제한·600회 이하 신규 요청 상한을 두고, 403·429 등 HTTP 오류에서 멈춘다. 공식 통계 수집은 512회였고, 공식 규칙 PDF 1회와 초기 탐색용 GET 3회·POST 4회를 포함해 이번 작업의 직접 조회는 총 520회였다. **초기 페이지 GET 세 건은 1초 간격을 적용하지 않았으며 탐색 응답을 보존하지 못했다.** 이 탐색 응답은 정규화 근거로 사용하지 않았다. 본 수집 응답 512건은 원문과 메타데이터를 보존했다.

초기 수집 파서가 팀 자책과 개인 합의 차이를 오류로 보류한 8경기는 원문을 버리지 않았다. 수정 후 저장된 모든 원문을 별도 v3 체크포인트에서 `--offline`으로 재처리해 256경기를 모두 복원했다. 이 재처리는 신규 요청 0회였다. 원본 수집 캐시 v2는 유지했다. 다시 독립 v4 체크포인트에 원문 캐시를 복사해 네트워크 없이 재생성한 256행도 v3와 바이트 단위로 일치했다.

재개·재처리 경로와 정확한 명령은 다음과 같다. 결과 디렉터리는 기존 것을 덮어쓰지 않도록 별도로 선택한다. 2023~25년 수집으로 확대하지 않는다.

```powershell
python scripts/research_kbo_official_record_gap.py `
  --lineups C:/Users/user/Documents/Codex/klu/experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl `
  --lineup-raw C:/Users/user/Documents/Codex/klu/.research-state/kbo-lineups-v5/raw `
  --pitcher-archive C:/Users/user/Documents/Codex/kbodata/data/raw/detail/kbo_baseball_2023_2026.json `
  --output .research-state/kbo-record-gap-v3 --offline --max-requests 1
python scripts/audit_kbo_official_record_gap.py `
  experiments/kbo-official-record-gap-20261103/artifacts/normalized-records.jsonl.gz `
  experiments/kbo-official-record-gap-20261103/artifacts/raw-responses.jsonl.gz
```
