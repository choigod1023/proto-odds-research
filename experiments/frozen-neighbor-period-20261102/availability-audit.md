# K리그1 2026 입력 가용성·선행 사용 감사

2026-11-02에 보관 파일만 읽었다. 결과 라벨이나 모델 예측·성능은 계산하지 않았다. 2024 은행과 이미 평가한 2025년 227경기는 새 평가 대상으로 사용하지 않았다.

## 배당·경기 기록

- 국내 CSV `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/internal-game-factors/data/processed/games.csv.tmp`, SHA-256 `9ffddb2dcbc4297c32e6ad3826f392e1842e52e10b509e4cbe47eed1f84b9da4`: 2026-02-28~05-17 전체 경기 승무패 89행, 모두 3개 배당값 보유. 필드는 `year,round,game_no,date_text,sport,league,market_family,market_label,n_way,home,away,odds,is_void` 등이다. 날짜·방향이 같은 중복은 6경기 12행이다. 중복을 제외한 77행은 아래 공식 결과 투영과 날짜·홈/원정·시각이 유일하게 맞는다. 이는 경기 식별 후보 수이지 결과 점수 일치나 최종 상태 검증 건수가 아니다. 별도의 가격 실제 수신시각은 없다.
- 해외 자료 `C:/Users/user/Documents/ChatGPT/sportstoto/data/raw/overseas/K리그1.json`, SHA-256 `47e2b363bd779cd31739f4f595aa124534c202e1adb1ed96af78307768751dd7`: 2026-02-28~07-26 120행, 모두 3개 배당값 보유. 필드는 `home_en,away_en,date,home_score,away_score,odds`다. 120행 모두 날짜·양 팀 방향으로 공식 결과 투영에 유일하게 연결된다. 날짜의 시간은 자정으로 돼 있고 실제 킥오프 시각과 가격 수신시각은 없다. 수집 코드 `src/overseas_odds.py`는 이 자료를 BetExplorer 결과 페이지의 배당으로 설명하므로 사전 실행 가능 가격으로 간주할 수 없다.
- 명단 원자료 `C:/Users/user/Documents/ChatGPT/sportstoto/data/raw/detail/kleague_soccer_2023_2026.json`, SHA-256 `23380355d10e7a9b078a2c170611e89796b95549be19d1d783b2c7d480ac2daa`: 2026-02-28~07-26 120경기. 필드는 `gameId,date,home,away,home_score,away_score,data`다. 해외 120행과 날짜·양 팀 방향으로 연결되나 당시 명단 수신 증거는 없다. 2026-10-30 14:29:01 KST는 기존 연구의 로컬 파일 읽기 시각이다.
- 별도 `C:/Users/user/Documents/ChatGPT/sportstoto/data/raw/overseas/live_odds.csv`, SHA-256 `7da5ff86246e015006ee14feb5ea98458bdd47ccac12634ea80153aab68e5ce9`: K리그1 12,450행, 기록된 수집 시작은 2026-07-27~08-23 UTC다. 12,450행 모두 킥오프가 비어 있다. `observed_at`은 수집 코드에서 HTTP 요청 **이전**에 정한 시각이라 응답 수신 완료 시각이 아니다. 팀명만으로 특정 경기나 정확한 사전 수신을 확정하지 않았다.

## 공식 결과 보관본

`C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/official/kleague-results.json`은 K리그1 2026-02-28~10-24의 198개 공식 경기 ID와 점수 투영을 담는다. 바이트 SHA-256은 `72d2cd4981deef8ad226936b6f828fb30ed230fb8edb9dbf971aaebb1444d343`, LF 정규화 해시는 기존 #398 연구에 적힌 `e13b4f94de9018ad30bd6e015acdf158af26933d637dd42f9df38d21531ef303`과 같다. 198건에 ID·날짜·홈/원정·킥오프가 있고 중복 ID는 없다.

같은 작업 트리의 `scripts/collect_tipster_year_official.py`는 K리그 `getScheduleList.do` 응답에서 `gameStatus=FE`와 `endYn=Y`만 고르도록 작성됐다. 다만 이 투영 JSON은 그 상태 필드와 월별 응답 원문을 보존하지 않는다. `official/requests.json`에는 2026년 월별 응답 해시가 있지만 원문은 이번 범위에서 찾지 못해 각 경기의 원문 종료 상태와 점수를 독립 검산하지 못했다. 특히 단순 0:0 표기만으로 종료 무승부라고 처리하면 안 된다. 기록된 `observed_at`도 요청 직전 시각이지 응답 수신 완료 시각이 아니다.

별도로 #425의 `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/link-new-source-evidence-20261030/experiments/source-link-kleague-20261030/schedule-identities.jsonl.gz`에 공식 일정 식별 투영 55건이 있다. 7월 30건·9월 25건이며 결과 점수·종료 상태를 포함하지 않는다. 이번 조사에서 원문 월별 응답은 확인하지 못했다.

감사 중 총괄 작업이 새로 보존한 `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/frozen-neighbor-holdout-20261102/experiments/frozen-neighbor-period-20261102/official-2026/`에는 2026년 1~11월 공식 응답 원문과 수신 메타데이터가 있다. 원문 11개는 각각 기록된 SHA-256과 일치했다. 과거 요청 해시가 있는 10개월 중 9개월은 새 원문과 같고 10월은 다르다. 이는 바이트 비교이며 10월 변경의 내용을 판정한 것은 아니다. 새 `fixtures.jsonl`의 SHA-256은 `a4b1e8e4f3658419e9be910bbc39793ae423e3abe2a67e76b0866c1f5030bd85`, 종료 상태 FE·Y인 경기 204건이다. 이 자료와 날짜·양 팀 방향이 유일하고 국내 시각까지 맞는 후보는 77건, 해외 날짜·양 팀 방향 후보는 120건이다. 점수 일치나 결과 라벨은 계산하지 않았다. 공식 응답은 2026-11-02 사후 수신이며 가격의 당시 수신 증거는 아니다.

## 이전 사용과 시간 제한

고정 이웃 모델 입력 `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/recover-inputs-new-model-20261102/experiments/neighbor-tuning-20261102/inputs/oof.jsonl`에는 2024년 228행과 2025년 227행만 있고 2026행은 없다. 기존 DC OOF 파일도 2026행이 없다. 2026 DC 벡터 생성 가능 건수는 아직 측정하지 않았고, 공식 원문 결과와 당시 가용성 검증 후 각 대상보다 최소 7일 앞선 결과만 이용해 생성해야 한다.

다른 기존 연구에서는 2026년을 이미 사용했다. #425 보존 입력에는 2026 K리그1 90행(3월 24, 4월 26, 5월 28, 7월 2, 8월 7, 9월 3)이 있고 날짜·양 팀 중복 초과 5행이 있다. 중복 없는 날짜·양 팀 기준으로 국내 후보 66경기, 해외 후보 70경기가 겹친다. `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/kleague-decay-backtest-20261029/experiments/kleague-decay-20261029/targets.jsonl`의 K리그1 2026 평가 대상은 19경기이며 국내 후보 8경기, 해외 후보 11경기와 겹친다. 따라서 이 기간이나 전체 2026을 미열람·독립 표본으로 부를 수 없다.

## 재현

독립 작업 폴더 `C:/Users/user/Documents/Codex/klkbo-restore`에서 `python audit-2026-availability/audit.py > audit-2026-availability/census.json`을 실행했다. 팀명 대조는 연도·날짜·홈/원정 방향과 명시적인 이름 매핑만 사용하며 점수, 결과 클래스, 모델 출력을 비교하지 않는다. 상세 원천 경로·바이트 해시와 분모는 `census.json`에 있다. 후속 검증 전에는 새 월별 원문의 각 경기 종료 상태와 상품별 일치, 배당의 당시 수신·중복, 과거 결과 가용 경계와 2026 DC 입력 생성을 확인해야 한다. 운영·모의 원장·스케줄·모델 설정은 변경하지 않았다.
