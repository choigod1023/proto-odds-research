# 실제 원천 연결과 종목별 새 모델 조사

2026-11-08 조사. 공식 원문과 무료 보조 사이트를 소량 읽어 제공 열·선수/경기 ID·시각·가격 연결을 확인했다. 원천을 발견한 것, 실제 경기와 연결한 것, 모델 학습에 쓸 수 있는 것, 성능 개선을 입증한 것을 구분한다. 새 적중률이나 ROI는 산출하지 않았다. 운영 원장·추천 정책·DB·지갑·서버·스케줄을 변경하지 않았다.

## 사용할 수 있는 원천과 이번 실제 연결

### KBO — 공식 보존 원문 + MyKBO Stats

기존 KBO 일정·박스 원문에는 경기 ID·팀 ID·선발·투수별 투구수 등이 있다. 이번에 [MyKBO 선수 1990](https://mykbostats.com/players/1990)의 공식 프로필 링크가 KBO `playerId=50030`을 가리키는 것을 확인했고, [경기 14037](https://mykbostats.com/games/14037)의 2026-10-12 KT 원정·삼성 홈 투구수 84구·6이닝·8삼진을 보존 공식 박스와 대조했다. 서로 다른 사이트의 숫자 ID를 같은 ID라고 취급하지 않는다. 이 검증은 1경기·1선수이며 과거 당시 수신 증명이나 전 시즌 매핑의 보증이 아니다.

MyKBO의 현재 시즌 합계는 과거 매 경기 직전의 통계가 아니다. `?year=2025` 요청도 실제 로그는 2026으로 남아 2025년 경기 로그 수집에 성공했다고 세지 않았다. 경기 상세에는 투구수가 있으나 선수 로그의 NP가 비어 있는 사례가 있어 경기 상세를 확인해야 한다.

KBO 공식 robots의 사전 승인 없는 자동 수집 제한을 확인해 신규 공식 API 자동 수집을 하지 않았고 기존 원문을 재검증했다. MyKBO의 조사 경로와 5초 지연 조건을 확인해 소량 조회했다. STATIZ·KBReport는 이번 환경에서 robots/본문 경로를 충분히 확인하지 못했다. 확대 수집·재배포 권리가 확보됐다는 뜻은 아니다. 상세 영수증과 CLI는 KBO 워커의 별도 연구 PR에 있다.

### NBA — 공식 부상 PDF·게임 페이지, CDN/API는 별도 상태

[NBA 공식 부상 보고서](https://official.nba.com/nba-injury-report-2026-27-season/)는 시각별 PDF 링크를 제공한다. 이번 직접 HTTP 수신에서 현재 시즌은 HTTP 200, 문서 제목의 2026-27 시즌과 PDF 링크 11개를 확인했다. 그러나 **2025-26 시즌 URL도 HTTP 200으로 현재 2026-27 URL에 이동했다.** 과거 시즌 주소라고 해서 과거 자료를 받았다고 가정하면 안 된다.

이번 `scripts/discover_nba_injury_sources.py`는 기존 HTTP 캡처/해시 캐시를 재사용해 다음을 확인한다.

- 요청 시즌·최종 URL·본문 시즌이 일치해야 PDF 링크를 반환한다. 실제 2025-26→2026-27 이동 사례는 `blocked`, 종료 코드 2로 남긴다.
- 공식 PDF 호스트·파일 형식을 확인하고 문서 날짜와 동부시간을 UTC로 변환한다. 미국 서머타임을 반영하며 애매하거나 존재하지 않는 DST 시각은 거부한다.
- 중복 링크·다른 날짜·미래 시각·비공식 호스트를 제외하고 최대 64개까지만 반환한다. HTTP 수신 시각과 URL에 적힌 작성 시각 주장을 별도로 보존한다.
- 링크 발견은 PDF 수신·선수 식별·경기 전 적격성이 아니다. `pdf_body_captured=false`, `training_eligible=false`를 유지한다. 실제 PDF는 기존 `collect_basketball_comparison_sources.py`의 명시적 `--injury-pdf` 경로에서 별도 수신·파싱해야 한다.

[nba-current-discovery.json](nba-current-discovery.json)에는 실제 11개 링크, [nba-old-season-blocked.json](nba-old-season-blocked.json)에는 시즌 불일치 영수증이 있다. 원문은 별도 로컬 캐시에 보존했다. 이 목록은 해당 시점의 index에 있는 보고서만 다루며 전체 과거 아카이브가 아니다.

농구 워커는 [공식 부상 PDF](https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-04_12_15AM.pdf) 8쪽을 실제로 받았고, 보존된 [NBA 박스스코어 0022500274](https://www.nba.com/game/0022500274/box-score)의 선수·팀 ID·출전시간과 보관 승패 가격 1행도 대조했다. 모두 과거 경기 후 수신본이다. 이번 NBA CDN 직접 조회는 403, stats API는 시간 초과여서 그 경로를 작동하는 자동 수집으로 표시하지 않았다.

### KBL·WKBL — 공식 일정과 경기별 선수기록

[KBL 공식 일정 API](https://api.kbl.or.kr/match/list?fromDate=20250101&toDate=20250107&tcodeList=all)에서 경기 ID를 얻고 [선수기록 API](https://api.kbl.or.kr/match/S45G01N116/player-stat)를 연결할 수 있다. 이번에 24행을 받아 공식 선수·팀 ID, 출전시간, 득점, 야투·자유투·리바운드·턴오버를 확인했다. 양 팀 200분씩, 개인 득점 합계 86–68이 공식 결과와 맞았고 보관 승패 가격 1행과 연결했다. 포제션·효율 필드도 있지만 선수별 값을 더해 팀 포제션으로 쓰는 것은 별도 검증 없이 허용하지 않는다. 과거 선수기록이 경기 전 명단이라는 뜻은 아니다.

WKBL은 [공식 경기 페이지](https://www.wkbl.or.kr/game/result.asp?game_no=72&game_type=01&gun=1&season_gu=046&viewType=2&ym=202602)를 조사했으나 이번 직접 선수 원문 요청은 403이었다. 검색에 보이는 결과와 가격이 같아도 원문 수신에 성공한 경기로 세지 않았다. 선수 ID·출전시간의 이번 신규 검증 연결은 0건이다.

### 배구 — KOVO 공개 경기 결과·경기 후 PDF

DBBANK 첫 화면은 [로그인 페이지](https://dbbank.kovo.co.kr/login.asp)로 이동하지만 조사한 개별 공개 결과·PDF는 읽을 수 있었다. [2025-01-09 여자 132번](https://dbbank.kovo.co.kr/game/11110_pop_result.asp?PID=gIzMxoTMwIjOxIDM), [2025-12-27 남자 120번](https://dbbank.kovo.co.kr/game/11110_pop_result.asp?PID=gAjMxoTMwIjOyIDM), [2026-01-15 여자 153번](https://dbbank.kovo.co.kr/game/11110_pop_result.asp?PID=gMTNxoTMwIjOyIDM)을 파싱해 보관 가격의 날짜·팀·시작 시각·승패와 연결했다. 세트 승수 3:1과 세트 원점수 25:22 등을 별도 열로 구분했다.

공식 `pcode` 선수 ID, 공격·리시브 효율, 블로킹·범실과 사후 출전 표를 확인했다. HTML의 리시브 효율 분모, 당시 수신 시각, 경기별 공식 취소/기권 상태는 아직 없다. PID의 일부 코드만 보고 남녀부를 자동 추정하지 않는다. 경기별 수집 CLI는 개별 URL·원문·수신 영수증·파싱 JSON을 보존하며, 전체 2025~2026 날짜 범위 목록과 자동 수집 권한은 확인되지 않았다. 모델 자료로 바로 넣은 상태가 아니다.

Goli·pick79 등은 기존 원문·거절 사유도 검토했다. 확인한 Goli 배구 오버 기준점은 당시 프로토와 달라 동일 상품 투표로 연결하지 않았다. 새 적격 동일 상품 픽스터 투표는 확보하지 못했다.

### 구장·기상 보강 후보

[기상청 ASOS](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36)는 공식 시간별 기상 자료의 후보 원천이다. 구장 좌표·지점·경기 전 관측/예보 발표시각을 연결해야 하며 이번에 관측 API 수신이나 새 예측 효과를 검증한 것은 아니다. [KBO 날씨 페이지](https://www.koreabaseball.com/Schedule/Weather.aspx)의 정적 HTML에는 2020년 예시 값이 있어, 화면 이름이나 HTTP 200만 보고 현재 날씨로 채택하면 안 된다. 실제 동적 응답과 발표시각 확인이 선행돼야 한다.

## 병렬로 고정한 새 모델 방향

- **KBO 대체 불펜 가용성:** 선발이 맡지 못할 것으로 예상되는 아웃 수와 경기 전 확인된 대체 불펜의 최근 투구 부하를 결합해 M0 로그오즈에 강하게 수축한 잔차로 더한다. 단순 선발 휴식일·상위 3명 불펜 부하 실험과 구분한다. [구원투수 피로 연구](https://doi.org/10.1515/jqas-2018-0007)는 MLB에서의 가설 근거이며 KBO 적중률·ROI 개선의 증거가 아니다. 사전 엔트리·선수 ID별 전원 이력과 당시 가격 공통 표본이 없어 새 적합은 하지 않았다.
- **농구 포제션·효율 잔차:** 팀의 과거 공격·수비 효율 및 최근 출전시간으로 예상 평균 점수차를 바꾸는 후보다. 분포만 정규→Student-t로 바꾸고 승패 방향이 같았던 실험과 다르다. [Four Factors 논문](https://arxiv.org/abs/2305.13032)은 효율과 요인의 비선형 관계를 다루며 후보의 설계 근거다. 이번 확인 범위에는 팀별 직전 10경기와 정확한 사전 가격이 함께 연결된 학습 표본이 없다.
- **배구 리시브·상대 전력:** 남녀부를 분리하고 직전 경기들의 리시브 전력 차이를 시장 로그오즈의 잔차로 학습하는 가설이다. 과거 세트 승수의 단순 20% 혼합과 다르다. [배구의 세트·득점 계층모형 연구](https://arxiv.org/abs/1911.01815)는 경기 고유의 점수 체계를 모델링할 근거다. 리시브 효율을 sideout 성공률로 취급하거나 분모를 만들어 넣지 않는다.

각 가설은 결과를 보기 전에 워커 브랜치에서 고정했다. 현재 확인된 추가 입력만으로 학습·평가를 끝낸 모델은 없고, 새로운 적중률·ROI 향상 수치는 없다. 이전 1픽 규칙 연구의 농구 23경기 개선을 이 새 모델들의 성과로 합산하지 않는다.

## 재현과 상태

이번 NBA index CLI는 HTTP 1회와 기존 검증 캐시를 이용하며 PDF를 대량 다운로드하지 않는다. Python CLI로 실행할 수 있어 수집 실행 자체에는 AI 호출이 없다. 새 예약 작업이나 운영 자동 수집 배포는 추가하지 않았다.

예: `python scripts/discover_nba_injury_sources.py --season 2026-27 --date 2026-11-08 --evidence <로컬캐시> --capture-id <새수신ID> --output <결과.json>`

기존 영수증 재생: `python scripts/discover_nba_injury_sources.py --season 2026-27 --receipt <nba-index-2026-27.json> --output <결과.json>`

`python -m pytest tests/test_nba_injury_source_discovery.py tests/test_basketball_comparison_sources.py -q` — 17 passed. 실제 현재 시즌 11개 발견, 과거 시즌 잘못된 이동 차단을 검증했다.

경기/선수 원문 파싱·연결·모델 가설은 [배구 #660](https://github.com/choigod1023/proto-odds-research/pull/660), [농구 #661](https://github.com/choigod1023/proto-odds-research/pull/661), [KBO #662](https://github.com/choigod1023/proto-odds-research/pull/662)에 각각의 보고서와 함께 보존했다. 각 focused 검증은 배구 53개·농구 12개·KBO 4개 통과이며 공통 기존 테스트를 중복 포함하므로 합계를 고유 테스트 수로 표현하지 않는다. 병합·운영 반영은 별도 승인·검증 대상이다.
