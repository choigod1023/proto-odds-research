# 해외 무료 픽스터 원문 가격 수집

조사일: 2026-10-28. 담당 범위: Tony's Picks NPB, Covers MLB, Pickswise MLB·NBA. 부모 작업이 프로토 조인과 최종 적중률·ROI 집계를 담당한다. 이 수집기는 모델 비교나 운영 변경을 수행하지 않는다.

## 전달 표본

- `candidates.jsonl`: 47행. Tony's Picks 11행, Covers 6행, Pickswise 30행. 원문 게시 메타데이터, 수정 메타데이터, 응답 수신 시각, 기사에 쓰인 팀명, 추천 시장·선택·라인, 원문 American 가격과 환산 소수 배당을 보존했다.
- 중복과 제외 항목을 뺀 경기 단위 가격 후보는 **34개**다. Tony's Picks NPB 10개, Covers MLB 3개, Pickswise MLB 9개·NBA 12개다.
- 위 34개 중 **22개**에 공식 날짜·양 팀·최종 점수를 정확히 연결했다. Tony's Picks 10개, Covers 3개, Pickswise MLB 9개다. NBA 12개는 공식 결과 미확인이다.
- 제외 13행: 같은 작성자의 동일 NPB 추천 중복 1개, 선수 개인 기록 시장 10개, Polymarket 가격 1개, 원문 추천 가격 미추출 1개. 이 중 개인 기록 시장 5개는 저장한 추천 제목만으로 상대 팀까지 특정하지 못했다. 제외 행도 감사용으로 남겼다.
- 승패·스프레드·언오버는 별개다. NBA Lakers −3.5를 단순 승으로 바꾸지 않았다. Covers Cubs–Pirates는 추천 **Under 8 (−105)**를 저장했으며 같은 페이지의 배당표 **Under 8.5 (−120)**로 대체하지 않았다.

`status=candidate`는 원문 가격 기술통계용 수집 후보를 뜻한다. 경기 전 인증이나 정산 적격 판정이 아니다. 모든 행에 `cutoff_proven=false`, `strict_backtest_eligible=false`, `settlement_rules_verified=false`를 표시했다. 승패의 연장·무승부 및 취소 처리와 프로토 정산 조건은 별도 확인해야 한다. 과거 원문 가격으로 계산한 기술통계를 인증된 사전 백테스트나 실제 구매 수익으로 부르면 안 된다.

## 원문 및 날짜 품질

- [Tony's Picks 시작 글](https://www.tonyspicks.com/2026/09/04/nippon-ham-fighters-vs-rakuten-golden-eagles-prediction-ron-crawford-september-5/): 기사 경로 날짜는 9월 4일이지만 최초 게시 메타데이터는 9월 5일 UTC, 경기 대상은 일본 현지 9월 5일이다. `match_date`와 게시 시각을 구분했다.
- [SoftBank–Orix 첫 글](https://www.tonyspicks.com/2026/09/16/softbank-hawks-vs-orix-buffaloes-best-bet-ron-crawford-9-17-26/)과 [다른 URL의 같은 추천](https://www.tonyspicks.com/2026/09/16/softbank-hawks-vs-orix-buffaloes-best-bet-ron-9-17-26/)은 Ron Crawford의 9월 17일 SoftBank −139로 겹친다. 먼저 게시된 글을 남기고 후속 글은 `duplicate_of`로 연결했다. 'around' 가격은 근사 표현을 별도 품질값으로 보존했다.
- [Seibu–Lotte 9월 1일 글](https://www.tonyspicks.com/2026/09/01/seibu-lions-vs-lotte-marines-best-bet-ron-crawford-9-1-26/)은 Seibu 홈이라고 쓰지만 공식 결과는 Lotte 홈이다. 날짜와 양 팀은 일치하므로 점수를 연결하고 원문의 장소 오류를 행에 표시했다. 기사 팀 나열 순서를 홈·원정으로 사용하지 않는다.
- [9월 26일 글](https://www.tonyspicks.com/2026/09/26/lotte-marines-vs-seibu-lions-best-bet-september-26-ron-crawford/)의 게시 메타데이터는 JST 19:17이다. 사전 추천으로 인증하지 않는다. 정확한 시작 시각은 이 수집기에서 확보하지 못했다.
- [Pickswise 2025 Wild Card 글](https://www.pickswise.com/news/mlb-wild-card-wednesday-predictions-red-sox-vs-yankees-reds-vs-dodgers-best-bets/)은 현재 갱신 표시가 2026년이어도 `datePublished=2025-10-01`과 본문의 2025 시즌이 일치한다. 해당 경기 날짜를 2026년으로 바꾸지 않았다. 다른 MLB 글은 최초 게시 날짜와 기사에 표시한 2026 경기 대상, 공식 일정을 함께 대조했다.
- [Pickswise 10월 23일 NBA 글](https://www.pickswise.com/nba/news/nba-best-bets-today-october-23-picks-predictions/)의 게시 UTC 날짜는 10월 22일이다. 대상 경기일은 제목의 10월 23일로 보존했다.
- [Covers Yankees–Twins](https://www.covers.com/mlb/yankees-vs-twins-prediction-picks-odds-monday-9-15-2025)의 현재 HTML 작성자 메타데이터는 Tony Sartori, 웹 검색 색인의 작성자는 James Bisson이다. 행의 `author`는 관측 HTML 값이며 최초 작성자 확정이 아님을 경고했다. Covers의 UTC−05:00 메타데이터를 그대로 보존했고, ET 표시와 일광절약시간의 차이를 임의로 수정하지 않았다.
- 작성자별 게시 시각, 본문 내 원래 추천 가격이라는 주장은 현재 읽힌 과거 페이지의 주장이다. 독립적인 당시 보관본이나 최초 버전은 확보하지 못했다. 수정 이후 글을 처음 게시된 원문으로 인증하지 않는다.

## 공식 결과와 접근 실패

- NPB: 2026-09-01/05/08/15/16/17/19/23/26/29의 [공식 일별 결과 형식](https://npb.jp/bis/2026/games/gm20260905.html)을 조회했다. 상단의 오늘 경기 링크를 무시하고 요청 날짜와 일치하는 `/scores/2026/MMDD/` 카드만 사용했다. URL의 양 팀 코드, 카드의 팀명·점수를 함께 보존했다.
- MLB 2026: [StatsAPI 10월 5–15일 묶음 조회](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-10-05&endDate=2026-10-15&gameTypes=R,F,D,L,W). Final 상태와 `officialDate`, 양 팀 이름이 유일하게 일치하는 경기만 연결했다.
- MLB 2025: 사용자가 제공한 `tipster-400-audit-20261026-local/official-international.json`의 존재와 해시를 확인한 뒤 읽기만 했다. 실제 내용은 MLB 2025년 등이며 NBA·NPB는 없다. KST `date` 대신 `official_date`로 연결했다. 캐시 최초 수집 시각이 없어 `official_observed_at=null`로 남겼다. 실제로 연결한 결과만 `official_matched_results.json`에 복사했다.
- NBA: `https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json`을 1회 요청했으나 HTTP 403이었다. 우회하거나 재시도하지 않았다. 부모 작업이 추가 확인한다.
- Tony's 작성자 목록: HTTP 200이어도 'One moment, please...' 확인 화면이어서 `access_challenge_no_bypass`로 분류했다. 공개 검색으로 발견한 개별 원문만 읽었다.
- Pickswise NBA 10월 20일·22일의 날짜별 URL은 각각 404였다. 없는 글을 누락 없이 수집했다고 주장하지 않는다.
- 프로토 `accuracy-formula-lab/data/processed/games.csv`는 존재하지 않았다. 새 작업 공간의 `games.csv.tmp`도 사용하지 않았다. 제공된 aliases 파일은 존재·해시만 확인했고 이 스크립트의 영문 팀·NPB 코드 사전과 섞지 않았다.

Python HTTP 요청은 **48회**, 최초 웹 원문 열기 3건과 검색 질의 8건을 합친 제한 작업은 **59회**다. 파서 보완으로 같은 공개 페이지를 다시 받은 요청도 원장에 별도 기록했다. 리다이렉트 추적·자동 재시도·인증·접근 제한 우회는 사용하지 않았다. 네트워크 요청을 병렬화하지 않았다.

## 재실행과 검증

저장 자료로 후보를 다시 만들고 검사하려면 저장소 루트에서 다음을 실행한다. 네트워크 요청은 발생하지 않는다. `build`는 제공된 외부 캐시가 여전히 존재하면 읽기만 하므로 다른 환경에서는 캐시 점수 연결 범위가 줄 수 있다. 커밋된 `candidates.jsonl`과 `official_matched_results.json`은 당시 연결 결과를 보존한다.

```powershell
python -X utf8 scripts/research_collect_overseas_tipsters.py build
python -X utf8 scripts/research_collect_overseas_tipsters.py check
```

`probe URL ...`만 새로운 공개 URL을 요청한다. 이미 요청한 URL은 저장한 구조화 자료를 재사용하고 전체 Python 요청 한도는 49회다. 원문 전체 HTML은 저장하지 않고, 응답 해시·게시 메타데이터·짧은 추천/가격 발췌·공식 점수만 남겼다. 해시만으로 최초 원문을 복원할 수는 없다.

검사는 American ±배당 환산, 잘못된 가격 거부, 시장·라인 보존, playable-to 가격 배제, 2025 최초 게시 연도 보존, 원문 가격 발췌 일치, 동일 추천 중복 제거, 공식 날짜·양 팀의 유일 연결, 잘못된 날짜 및 복수 경기 연결 거부, 요청 상한과 필수 필드를 확인한다. 이 범위를 통과한 것이 새 출처의 성과 우위나 미래 수익을 입증하지는 않는다.
