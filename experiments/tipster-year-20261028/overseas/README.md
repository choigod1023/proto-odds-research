# 해외 무료 픽스터 과거 자료 수집 — 2026-10-28

대상 경기일은 2025-10-28~2026-10-27이다. 공개 자료 679개에서 추천 후보 1,517개, 고유 경기 1,301개를 추출했다. 작성 주체 표기는 23개이며, 이 중 개인 이름 표기 22개·Staff 표기 1개다. 이름은 출처의 주장으로, 실명 인증이나 서로 다른 실제 인물임을 확인한 것은 아니다. WordPress 게시 작성자와 실제 추천 전문가가 다를 수 있다. 알 수 없는 작성자는 추정하지 않는다.

## 실제 확보 범위

- 전체 후보: NBA 967개, MLB 516개, NPB 34개. 파싱이 끝나지 않은 항목·선수 기록·배당 누락 후보도 포함한다.
- 경기 전체의 추천 방향을 해석한 비중복 후보: 1,349개.
- 정확한 명시 배당·경기 전체 시장·선택을 갖추고 제외 사유가 없는 후보: 1,010개. 근사 배당, 누락 배당을 -110 등으로 대체하지 않았다.
- 공식 결과 연결: 493개 추천, 462개 공식 고유 경기.
- 정확한 배당까지 갖춘 공식 결과 연결: 401개, 적중 210개·실패 187개·적특 4개. MLB 387개, NPB 14개. 배당 없는 추천의 승패와 배당 검증까지 끝난 표본을 구분한다.
- 공식 결과 풀: MLB StatsAPI 2,959개 최종 경기와 NPB 공식 날짜별 결과 131개. 전부 추천과 연결된 경기는 아니다.
- NBA 공식 CDN과 nba.com 일반 페이지는 403 응답을 확인했다. 이 작업의 NBA 공식 연결은 0개이며 부모 작업이 별도로 확보한 공식 자료로 연결한다. 픽스터 사이트 점수판을 공식 결과로 사용하지 않았다.
- Covers의 공개 editorial sitemap은 최근 일부 날짜만 제공했다. Covers 후보 30개에서 정확한 원문 추천 배당까지 확인한 적격 항목은 0개다. 사이트 전체 과거 기록 확보라고 해석하면 안 된다.

## 월별 범위

| 경기 월 | 추천 후보 | 고유 경기 | 배당 적격 후보 | 배당+공식 결과 |
|---|---:|---:|---:|---:|
| 2025-10 | 45 | 29 | 24 | 1 |
| 2025-11 | 233 | 147 | 122 | 1 |
| 2025-12 | 137 | 115 | 102 | 0 |
| 2026-01 | 153 | 137 | 109 | 0 |
| 2026-02 | 99 | 99 | 89 | 0 |
| 2026-03 | 150 | 150 | 114 | 9 |
| 2026-04 | 164 | 162 | 115 | 75 |
| 2026-05 | 236 | 222 | 144 | 141 |
| 2026-06 | 81 | 55 | 50 | 47 |
| 2026-07 | 39 | 39 | 34 | 34 |
| 2026-08 | 46 | 42 | 42 | 42 |
| 2026-09 | 57 | 50 | 41 | 41 |
| 2026-10 | 77 | 54 | 24 | 10 |

기간 양 끝의 부분 월을 포함해 13개 달에 후보가 있다. 배당과 공식 결과가 함께 있는 2026-04~10월 표본은 7개 달에 분포한다. 일부 달에 표본이 없거나 적은 이유를 통계적으로 보정한 연구가 아니다.

## 수집·표본 규칙

Tony’s Picks 공개 WordPress posts API에서 달별 MLB/NBA category와 NPB 검색을 사용했다. 각 요청은 주장된 게시일 오름차순 최초 25개 글이며, 검색 결과 총수와 요청 URL을 discovery.json에 남겼다. 일별 모음 글 안에서는 여러 경기와 여러 추천을 추출한다. 2026년 2~3월 Tony archive가 비어 있는 구간은 Pickswise 공개 news sitemap에서 추가된 후보와 구분한다.

Pickswise MLB/NBA/news sitemap, Covers editorial sitemap의 공개 URL을 수집하고 출처·종목·달별로 날짜와 URL 오름차순 최초 18개를 선택했다. parlay·홍보·명시적인 선수 prop 전용 URL은 이 단계에서 제외했다. 동일한 URL이 여러 sitemap에 있으면 한 번만 읽었다. 검색·표본 선택에는 경기 결과, 적중 여부, 작성자 성적, 배당 가격을 사용하지 않았다. discovery.json의 일부 반복 실행 로그는 당시 checkpoint를 포함한다. 실제 고정 입력은 documents.json과 selected_urls.json이다.

호스트별 요청 간격은 최소 0.55초이며 단일 순차 수집과 응답 캐시를 사용했다. 인증, 유료 자료, 차단 우회, 프록시, 보호 페이지 재시도는 사용하지 않았다. 탐색 단계의 소수 공개 HTTP 시험은 requests.jsonl 이전에 시행했으며 해당 파일이 세션 전체 네트워크 요청 총수는 아니다.

## 시점과 원문 배당

source_reported_published_at와 source_reported_modified_at는 출처가 주장하는 시각이다. observed_at은 실제 이번 수집 시각이다. 모든 후보는 retrospective_source_claim_not_pregame_certified이고 cutoff_proven=false다. 게시 후 본문이 수정됐을 가능성을 배제하지 못하며 경기 전 가격의 실제 구매 가능성은 증명하지 못한다.

배당은 추천 문장의 정확한 signed American 가격 또는 minus/plus 숫자 표현을 변환했다. playable-to 가격·추천 허용 범위는 원래 추천 가격과 분리한다. around/about 등 근사 표현은 적격 표본에서 제외한다. source_url은 글 URL, provenance_request_url은 실제 응답 URL이며 WordPress API로 가져온 경우 해시는 개별 글 HTML이 아니라 해당 API 응답의 SHA-256이다. 기사 전체 HTML이나 전체 본문은 커밋하지 않는다.

period=full_game은 경기 전체 시장으로 해석한 값이다. full_game_period_explicit는 원문에 full-game/full-time 표현까지 있는지 별도로 표시한다. 표현이 없는 일반 경기 전체 추천을 명시적 구간 증거로 과장하지 않는다. 부분 경기·팀 합계·선수 기록은 공식 전체 점수만으로 정산하지 않는다.

## 연결·중복·정산

공식 리그·공식 경기일·정규화한 두 팀이 모두 일치해야 연결한다. MLB doubleheader처럼 하루에 같은 두 팀의 공식 경기가 여러 개면 임의로 선택하지 않는다. 공식 데이터에는 official_id, official_result_id, official_result_url, source_url, match_date, 영문 away/home, 점수와 관측 시각이 있다. MLB에는 gamePk도 보존한다.

같은 경기의 여러 작성자 추천은 별도 픽이지만 고유 경기 수에는 한 번만 센다. 같은 출처·작성자·경기·시장·선택·라인·배당이 여러 URL에서 반복된 6개는 duplicate_of로 표시하고 배당 적격 표본에서 제외했다. 동일 경기일의 서로 다른 URL이나 인물을 독립 경기로 세지 않는다. 배당 없는 추천의 승패는 저장하되 순손익은 null로 둔다. 동점 moneyline은 NPB 약관을 가정해 적특 처리하지 않고 unresolved_tie_rules로 둔다.

## 파일과 재현

- predictions.jsonl: 원래 추출 스키마와 제외 사유, 작성자·출처 시각·가격 근거.
- candidates-normalized.jsonl: 부모 join용 source_url/selected_team/side/reported_decimal_odds/odds_time_quality/period 필드. NBA는 이 작업에 공식 home/away가 없어 side=null일 수 있으며 selected_team으로 부모가 연결한다.
- official_matches.json: 독립 공식 결과 풀. settlements.jsonl: 추천 ID별 연결·승패·적격 여부·단위 손익.
- summary.json: 출처·종목·월별 후보, 배당 적격, 공식 연결 수.
- documents.json: 해시·메타데이터·짧은 추천 문장과 제목만 남긴 입력. cache/의 전체 응답은 .gitignore로 제외한다.
- verification.json: Python parser regression과 모든 공식 연결 재정산 검증 결과.

저장된 자료만으로 `python scripts/research_collect_year_overseas.py build`와 `python scripts/research_collect_year_overseas.py check`를 실행할 수 있다. `collect`는 네트워크 수집이고, `rehydrate`는 커밋하지 않은 로컬 응답 캐시에서 추출 규칙을 재적용하는 보조 명령이다.

확률 모델·학습/평가 분할·하이브리드 비교·통계 유의성 검증은 실행하지 않았다. 이 자료의 통합 검증은 부모 작업이 담당한다. 단위 손익은 정산 검산용이며 미래 수익, 실제 체결 또는 운영 채택 근거가 아니다. 운영 코드·DB·배포 설정은 변경하지 않았다.

주요 원출처: [Tony archive API](https://www.tonyspicks.com/wp-json/wp/v2/posts), [Pickswise MLB sitemap](https://www.pickswise.com/mlb/news/sitemap.xml), [Pickswise NBA sitemap](https://www.pickswise.com/nba/news/sitemap.xml), [Covers editorial sitemap](https://www.covers.com/editorial-sitemap.xml), [MLB StatsAPI](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-04-01&endDate=2026-04-30), [NPB 공식 결과 예시](https://npb.jp/bis/2026/games/gm20260509.html).
