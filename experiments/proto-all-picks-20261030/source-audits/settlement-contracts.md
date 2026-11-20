# Proto 전체 선택지 정산 계약 감사 (읽기 전용)

기준 HEAD `421544799673a38eb7cb32f70397476bc1f507c2`, 브랜치 `codex/proto-all-pick-profitability-20261030`. `AGENTS.md` 및 `docs/research/2026-10-25-research-scope-correction.md`를 읽었다. 수집·데이터 합치기·ROI 계산은 부모 작업의 범위다. 이 감사는 합성 입력과 기존 테스트로 코드 계약만 검증했다. 실제 운영 결과 84,537행이나 추적 CSV 78,403행을 이 작업에서 읽거나 독립 인증하지 않았다. 해당 수치·revision9·생성일은 부모가 전달한 상태이며 전체 최신 자료라는 주장은 하지 않는다.

읽기 전용 범위: 추적 파일 수정·commit·push·PR·merge·배포·운영 DB/API 변경 없음. 테스트 임시 DB/JSONL과 감사 산출물은 `.local-test/SETTLEMENT` 아래에만 생성했다. `PROODD_DB_PATH`는 자식 프로세스에서 제거했고 Python bytecode·pytest cache·외부 pytest plugin 자동 로드를 비활성화했다. 규칙 조회를 위한 공식 웹 문서 GET만 별도로 수행했다.

## 부모 evaluator에서 먼저 적용할 조건

`winner_index`는 family를 검증하지 않는다. **family와 선택지 수로 허용 결과 라벨을 검사한 뒤** 해당 함수로 인덱스를 얻어야 한다. 다음은 현 아카이브 코드의 결과 라벨 계약이다. Proto 화면의 모든 신규 유형까지 지원한다는 뜻은 아니다.

```text
승패       2 → 홈승, 홈패
승무패     3 → 홈승, 무승부, 홈패
핸디캡     2 → 핸디승, 핸디패
핸디캡     3 → 핸디승, 핸디무, 핸디패
언더오버   2 → 언더, 오버
홀짝       2 → 홀, 짝
승①패      3 → 홈승, ①, 홈패
승⑤패      3 → 홈승, ⑤, 홈패
```

`무승부`를 `①` 또는 `⑤`의 아카이브 별칭으로 추가하면 안 된다. 승①패의 실제 무승부는 규칙상 중앙 구간에 속하지만, 이 코드가 기대하는 해당 시장의 결과 라벨은 `①`다. `홈승`/`홈패`의 의미도 일반 시장과 점수차 시장이 서로 다르다.

전반 시장은 라벨이 `전반무승부`로 바뀌지 않는다. `전반승무패`도 결과는 `무승부`, `전반핸디캡`도 결과는 `핸디무`다. 위 허용 목록을 기초 시장에 적용하더라도 **기간은 원래 family/label/selection에 계속 보존**해야 한다. `SEL_NAMES`에 실제 지원되는 전반 조합은 `전반승패|2`, `전반승무패|3`, `전반언더오버|2`, `전반핸디캡|2/3`뿐이다. 파서는 `전반승①패`도 만들 수 있지만 이름 표에는 없어 `to_bets`가 `sel0/sel1/sel2`로 내보낸다. 미지원 조합은 별도 누락 사유로 집계하고 전체 경기 점수로 채우지 않는다.

파서 `is_void`와 미정산을 구분해야 한다. 권장 구분은 명시적 무효/특례 증거, family·n_way에 맞는 정상 결과, 2-way 일반 무승부, 미정산·미지원·오류의 별도 상태다. 모르는 라벨을 패배나 환불로 바꾸면 안 된다. CSV 문자열 `'False'`에 `bool()`을 적용하면 참이 되므로 자료형도 명시적으로 확인한다.

## 재사용 함수와 계약

- [src/wisetoto.py:385](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/wisetoto.py:385) `parse_rows(html: str, year: int, rnd: int) -> list[GameRow]`: 순수 HTML 파싱. `li.a9`에서 유효하게 읽힌 배당만 순서대로 보존하며 `-`는 제거한다. 이 길이가 `n_way`다. `is_void = result in {취소, 연기, 중단, 무효} or any(odds <= 1.0)`. `odds=[]`만으로 무효가 되지는 않는다. 홈·원정 문자열에는 점수가 포함될 수 있다. `fetch_round`는 캐시/DB 기록 경로가 있으므로 이 감사에서 실행하지 않았다.
- [src/wisetoto.py:86](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/wisetoto.py:86) `GameRow.is_first_half`: 라벨에 `전반`이 있거나 소문자 `h` 다음 공백이 있으면 전반. `H -1.5`의 대문자 H만으로 전반이 되지 않는다. [src/wisetoto.py:105](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/wisetoto.py:105) `market_family`는 기초 family에 `전반`을 붙인다. `_base_family`는 태그·배당 개수·종목·핸디캡 라벨로 분류하며 결과 라벨로 검증하지 않는다. 농구/배구 일반 3-way를 `승⑤패`로 추정하고 빈 태그 2-way를 `홀짝`으로 추정한다. 신규 상품이나 malformed 행에 그대로 일반화할 수 없다.
- [src/bets.py:31](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/bets.py:31) `_WINNER`: `(n_way, result) → 0-based index` 전역 표. [src/bets.py:98](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/bets.py:98) `winner_index(n_way: int, result: str) -> int | None`는 그 표의 `.get()`이며 점수·종목·family·line·기간을 검사하지 않는다. [src/bets.py:50](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/bets.py:50) `SEL_NAMES`가 family별 선택지 이름의 정본이다. 2-way `[홈, 원정]`, 3-way `[홈, 무, 원정]`, U/O `[언더, 오버]`, 홀짝 `[홀, 짝]` 순서다.
- [src/bets.py:102](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/bets.py:102) `to_bets(rows) -> list[Bet]`: `is_void`를 통째로 제외하고, overround가 없거나 `[1.0, 1.40]` 밖이면 제외한다. 미인식 승자도 제외한다. 각 남은 배당마다 `Bet`를 생성한다. `Bet.profit`은 적중 `odds-1`, 나머지 `-1`이며 환불 레코드 표현이 없다. 따라서 모든 선택지·환불·미정산을 보존해야 하는 evaluator의 전체 입력으로 그대로 사용하면 분모가 바뀐다. **이 함수 자체에도 family whitelist가 없다.**
- [src/matches.py:69](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/matches.py:69) `actual_game_year(year, round_no, month)`: pandas Series 입력을 숫자로 변환한 뒤 `year - ((round_no == 1) & (month == 12)).astype(int)`를 반환한다. 임의의 월/회차 기준은 추가하지 않는다. 발매 연도는 offer 키에 보존하고 물리 경기 연도만 별도로 보정한다. [src/runtime_datasets.py:80](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/runtime_datasets.py:80) `result_row`도 같은 1회차·12월 보정을 쓴다.
- [src/ai_decision.py:121](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/ai_decision.py:121) `event_id(game) -> str`: `source_event_id`/`match_id`가 있으면 sport+source ID, 없으면 sport/year/date/league/home/away의 hash. 회차·게임번호는 제외한다. [src/ai_decision.py:133](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/ai_decision.py:133) `selection_id(game, option) -> str`: event+market+label+line+선택. [src/ai_decision.py:141](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/ai_decision.py:141) `offer_id(game, option) -> str`: selection+round+게임번호. `_stable_id`는 JSON 타입을 보존하므로 round 정수와 문자열은 다른 ID다. 게임번호의 선행 0도 임의 제거하지 않는다.
- [src/live_market_refresh.py:591](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/live_market_refresh.py:591) `settle_live_market_results(live_odds: dict, runtime: PredictionRuntime) -> int`: timezone 있는 `generated_at`, `markets[round][key]`의 game_no/date/sport/league/home/away/market/label/n_way/result를 읽는다. 결과 시각이 kickoff 이후이고 최대 90일 범위여야 한다. `SEL_NAMES`+`winner_index`로 각 선택지를 만들고 `(event_id, selection_id, offer_id)`가 정확히 **1개**인 저장 선택지만 정산한다. 같은 결과의 중복행도 건너뛴다. line 시장은 라벨의 숫자를 사용한다. 회차는 int, year는 피드 관측 시각의 KST 연도를 사용하므로 연도 경계 피드에 주의한다. 무효 라벨 4종 및 정확히 `승패|2 + 무승부`는 void. `전반승패|2 + 무승부`는 이 예외에 포함되지 않는다. **여기도 family whitelist가 없어 잘못된 결과 라벨을 적중 처리할 수 있다.**
- [src/recommendation_history.py:92](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/recommendation_history.py:92) `settle_history(history, odds, now)`: history를 제자리 갱신한다. 같은 회차, home/away/date/market/label/game_no가 유일하게 일치해야 한다. 취소 라벨은 void. 이름 표에 전반 시장이 없고, `승패|2 + 무승부`도 처리하지 않아 미정산으로 남는다. 범용 모든 선택지 정산기로 가져오면 안 된다.
- [src/prediction_runtime.py:404](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/prediction_runtime.py:404) `PredictionRuntime.settle_latest(event_id, *, outcome, settled_at, source) -> AppendResult | None`: event의 최신 사전 prediction revision만 대상으로 하며 outcome hash로 정산 version을 만든다. [src/prediction_runtime.py:462](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/prediction_runtime.py:462) `settle_latest_batch(entries)`는 같은 계약의 batch다. [src/prediction_ledger.py:318](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/prediction_ledger.py:318) `PredictionLedger.append_settlement(snapshot_id, *, outcome, settled_at, source, settlement_version='official-v1') -> AppendResult`는 특정 snapshot에 append한다. 같은 version 충돌을 거부하며 kickoff 이전/현재 capture 이후의 settled_at도 거부한다. 이들은 외부 결과의 진위를 인증하는 함수가 아니다.
- [src/runtime_datasets.py:54](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/runtime_datasets.py:54) `result_row(row, source, observed_at)`: 오직 일반 `승패/승무패`의 무보정 정수 점수를 확인한다. handicap/UO/전반은 제외한다. 정상승패와 점수가 안 맞거나 무효이면 같은 source 키의 `unconfirmed` invalidation을 만들 수 있다. [src/runtime_datasets.py:217](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/runtime_datasets.py:217) `DatasetStore.match_history(sports=None, before=None)`는 최신 source revision을 골라 physical kickoff/league/sport/teams가 같은 점수만 합치며 점수 충돌은 제외한다. offer별 가격/무효 상태를 보존하는 자료가 아니므로 모든 Proto 선택지 정산을 대체하지 않는다.

`generate_v2._settlement_outcome`([src/generate_v2.py:1099](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/generate_v2.py:1099))도 별도 경로다. 저장 selection_id와 같은 첫 option의 `적중`을 소비하며 이 함수 안에서 offer_id 유일성·무효 판정을 다시 검사하지 않는다. 전체 generator는 앞에서 `r.is_void`를 제외한다. 위 exact-offer lightweight 정산과 동일한 검증 계약으로 보면 안 된다.

## 실행 예제와 확인 결과

기존 pytest **21 passed**. 실행 대상은 `test_live_ledger_settlement.py` 전체, `test_game_dedup.py` 전체와 아래 기존 테스트다: event/selection 재발매 ID 유지, recommendation_history exact line, DB match history 재발매·더블헤더·잘못된 점수 거부, 일반 2-way 동점 환불, runtime idempotence/tally, ledger append-only. junit에 정확한 node 목록이 있다. `--basetemp`는 허용 폴더 아래였고 pytest cache는 끄고 실행했다.

인라인 assertion **15 passed**. 새 테스트 파일은 만들지 않았다. 합성 HTML 11종, is_void 경계 9종, 쓰기 없는 runtime 대역으로 공식 라벨 정산 15종을 실행했다. 주요 실제 반환값:

```text
winner_index(3, '무승부') = 1
winner_index(3, '핸디무') = 1
winner_index(2, '핸디무') = None
언더오버|2 + 홈승 → to_bets 언더 won=True   [잘못된 family 라벨 통과 재현]
언더오버|2 + 홈승 → runtime 언더 hit      [같은 위험 재현]
핸디캡|3 + 핸디무 → 핸디무 hit / 핸디홈 miss
핸디캡|2 + 핸디패 → 핸디원정 hit
전반승무패|3 + 무승부 → 전반무 hit
전반핸디캡|3 + 핸디무 → 전반핸디무 hit
전반승①패 + ① → sel0/sel1/sel2, sel1 won=True [선택지 이름 미지원]
승패|2 + 무승부, odds=[1.80,1.80] → parser is_void=False; to_bets=[]
같은 일반 동점 → runtime void; settle_history에는 result 미생성
홈승, odds=[1.00,1.00] → parser is_void=True; to_bets=[]
적중특례, odds=[1.80,1.80] → parser is_void=False; runtime pending
동일 exact-offer 결과행 2개 → runtime pending (충돌이 없어도 중복 거부)
actual_game_year([2026,2026,2026,2024], [1,2,1,1], [12,12,1,12])
  → [2025,2026,2026,2023]
회차102/0001 → 회차103/8001 재발매:
  event 같음, selection 같음, offer 다름
round=102 정수 vs '102' 문자열 → offer 다름
```

`game_dedup.deduplicate_game_sections`([src/game_dedup.py:24](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/src/game_dedup.py:24))는 화면 카드 정리용이다. sport/league/date/home/away를 공백 제거·소문자로 비교하고 유효가격 여부·배당대기 여부·회차가 큰 순서로 하나를 남긴다. year가 키에 없고 모든 발매를 삭제·축약하므로 부모의 offer-level union 또는 과거 선택지 전체 보존 경로에 적용하면 안 된다. 기존 테스트에서 다른 시작시각의 더블헤더는 남았다.

## 공식 운영 규칙 대조와 한계

규칙은 공식 1차 자료로 확인했지만, 아카이브 **개별 경기 결과**를 Betman 결과 발표와 대조한 것은 아니다. 현재 운영 규칙을 2023/24의 모든 경기에 소급 적용했다는 주장도 하지 않는다.

[Betman 승부식 이용안내](https://www.betman.co.kr/gameguide/protoVictory.html): 홈 점수에 handicap을 더한다. 축구 정수 handicap은 3-way, 축구 반점 및 야구/농구/배구 안내 예시는 2-way다. 배구 handicap은 세트, U/O·SUM은 랠리 득점 합계다. SUM의 0은 짝이다. 승①패 중앙은 무승부를 포함한 1점차 이내다. U/O는 기준보다 작음/큼으로 정의된다. .25/.75 Asian 분할 정산과 half-win/half-loss, 정수 U/O equality의 처리 규칙은 이 자료에서 확인하지 않았으므로 적용하지 않는다. 코드의 숫자 regex가 그런 line을 읽는다고 공식 지원 근거가 되지 않는다.

[현 고정배당률 세부규칙](https://www.sportstoto.co.kr/terms_allocation.php) 1(3), 1(6)~(10), 3(2)~(5): 일반 야구·농구 승패 동점은 양쪽 1.0배다. 일부 무효는 해당 leg 1.0, 전부 무효면 환불이다. 축구는 연장/승부차기 제외, 야구·농구는 연장 포함, 야구 전반은 5이닝이다. 전반이 끝났어도 전체 경기 무효 사유가 생기면 전반도 무효일 수 있다. 정상 콜드게임은 최종 확정 결과를 인정한다. 구매 당시 가격·조건을 적용한다. 일정 변경 시 같은 경기가 다른 회차에서는 유효/무효로 다르게 처리될 수 있다. KBO 더블헤더는 두 경기 모두 대상이 될 수 있다. 이 페이지는 2026-03-27 적용 문서다. `proto_rules.php`에는 2014 적용 문구가 남아 있어 현 규칙 대조에는 이 현행 경로를 사용했다.

[체육진흥투표권 약관](https://www.sportstoto.co.kr/terms_vote.php) 16·17·19·21조: 외부 scoreboard의 연기 표시를 곧바로 공식 void로 해석하지 않는다. 발표된 투표권 결과는 나중에 경기단체가 점수를 수정해도 원칙적으로 바뀌지 않고, 운영사 귀책의 공식 재발표는 예외다. 따라서 runtime이 correction append를 지원한다는 이유만으로 외부 점수 수정마다 과거 베팅 정산을 바꾸면 안 된다. 현재 페이지는 2026-06-10 적용 문서다.

## 재현 산출물

- [.local-test/SETTLEMENT/settlement-audit.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/.local-test/SETTLEMENT/settlement-audit.json): 실제 함수 signature/절대경로/줄번호/원본 SHA256, 합성 입력별 반환값, assertion 이름 15개, 감사 시점 HEAD.
- [.local-test/SETTLEMENT/pytest-results.xml](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/proto-all-pick-profitability-20261030/.local-test/SETTLEMENT/pytest-results.xml): 21개 통과한 기존 테스트의 node 목록.

실제 자료별 허용 결과 밖의 행 수, 가격 1.00·is_void 교차분포, 중복·재발매 충돌 수, 신규/전반 시장 누락 수 및 최종 ROI는 부모 작업에서 집계해야 한다. 이 보고서는 그 수치를 추정하지 않는다.
