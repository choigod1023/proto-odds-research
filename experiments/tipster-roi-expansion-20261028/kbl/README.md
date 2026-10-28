# Miky938 KBL 수집·독립 결과 대조 — 2026-10-28

지정된 연구 수집 하위 작업이다. 프로토 조인, 부모 평가 기준, 모델·하이브리드 실험 및 운영 변경은 포함하지 않는다. 확대 범위는 아래 표본으로 고정했다. 수집 요청은 초기 웹 도구 조회 2회와 Python 요청 25회를 합쳐 **27회/상한 30회**다. 자동 재시도·로그인·인증 토큰·쿠키·접근 제한 우회는 사용하지 않았다.

## 인계 파일

- `kbl-candidates.jsonl`: KBL 19행, 19경기. 2026-03-05~2026-10-28. 정산 18행의 마지막 날짜는 2026-10-25이며 미정산 1행은 2026-10-28이다.
- `nba-candidates.jsonl`: NBA 101행, **15경기**. 2026-04-08~2026-10-25. 승패 9행·총점 92행이므로 행 수를 독립 경기 수로 해석하지 않는다. 공식 결과를 대조하지 않았으므로 publisher-only로 보류한다.
- `source-observations.jsonl`: 중복 제거 전 사실 관측 142행. 출처·원문 팀명·날짜·시장·기준점·방향·소수 배당·게시자 결과와 손익·경기 시각·관측 시각·원문 행 번호·HTTP 본문 SHA-256을 담는다. 기사 분석 본문은 담지 않는다.
- `official-kbl-results.jsonl`: KBL 공식 API 138경기. 종료 135경기·미종료 3경기. `raw/official-kbl-2026-public-headers.json`은 그 공개 API의 원본 사실 JSON이다.
- `summary.json`: 기간·행 수·중복·제외·검증·게시자 표기 산술 통계.
- `requests.json`: 성공·실패 요청을 모두 기록한 원장. 요청 시각은 UTC다.
- `parser-verification.json`, `verify_parser.py`: 오프라인 검증 16개 통과 기록과 재실행 코드.

## 실제 출처와 선택 방식

[KBL 리그 목록](https://miky938-oddsparser.com/league/south-korea-kbl/)의 노출 행 19개 전부를 수집하고 연결된 날짜별 페이지 17개도 모두 확인했다. 승패·손익을 보고 선택하지 않았다. 정산 요약은 18픽이며 페이지에 추가 페이지 링크가 없어 이 경로에서 KBL 100건을 확보할 수 없었다. 이는 모든 가능한 과거 URL에 대한 완전성 주장과 다르다.

[NBA 목록](https://miky938-oddsparser.com/league/usa-nba/)의 노출 행 100개 전부와 이미 수집하던 KBL 날짜 페이지에 있던 추가 1개를 확보했다. NBA 요약의 162픽 전체가 아니다. 추가 날짜 페이지 확장은 하지 않았다.

총 19개 게시자 페이지에서 표 행 476개를 검사했다. KBL/NBA 관측은 142개이며 나머지 334개는 다른 리그라 제외했다. 날짜·홈팀·원정팀·시장·기준점·방향·원문 배당을 키로 중복 22개를 묶어 후보 120개를 만들었다. KBL 19개는 모두 리그 목록과 날짜 페이지 양쪽에 근거가 있다. 같은 키의 게시자 결과·점수·손익 충돌은 0개다. NBA의 여러 기준점과 반대 방향 행은 합치거나 지우지 않았다.

## 공식 KBL 결과 검증

[KBL 공개 API](https://api.kbl.or.kr/match/list?fromDate=20260305&toDate=20261028&tcodeList=all)에서 해당 기간 138경기를 받았다. 헤더 없는 요청은 필수 헤더 누락으로 400을 반환했다. [KBL 공식 홈페이지](https://www.kbl.or.kr/)가 공개한 클라이언트의 일반 WEB 요청을 확인한 뒤 `Channel: WEB`, `TeamCode: XX`, `X-Requested-With: XMLHttpRequest`, `lang: ko`를 적용했다. 요청 헤더·공개 클라이언트 URL·해시는 `official-public-client-evidence.json`과 요청 원장에 보존했다. 인증 헤더는 사용하지 않았다.

원문 날짜와 명시적 팀 코드의 홈/원정 순서를 정확히 맞췄다. 점수·승패로 경기를 찾지 않았고 유사 문자열 매칭도 하지 않았다. 공식 한국어 팀명과 원문 영문 팀명은 별도로 남겼다.

- 정산 KBL 18건 모두 공식 경기와 연결했고 추천 승패 18건 모두 일치했다.
- 점수까지 일치한 것은 17건이다.
- **2026-04-26 Anyang JungKwanJang–KCC Egis**: 게시자 `84:67`, 공식 `91:83`, 공식 ID `S47G03N14`. 양쪽 모두 홈승이지만 점수가 달라 `publisher_official_disagreement`로 보존했다. 임의로 원문을 고치지 않았다.
- 미정산 2026-10-28 KoGas–Wonju DB도 공식 경기 `S49G01N42`와 연결했다. 관측 시점에 공식 미종료라 `official_result`는 null이다.

## 가격·게시 시점과 통계의 한계

모든 후보의 가격 등급은 `retrospective_listed_price_claim`이다. 화면의 시각과 `data-ts`/`data-day-ts`는 경기 시각이며 게시 시각이 아니다. 표시 시간대는 WAT(UTC+1)다. 날짜별 URL의 날짜와 원문 경기 타임스탬프를 모두 보존했고, `source_reported_published_at=null`, `cutoff_proven=false`를 전 행에 적용했다. 작성자는 사이트의 Miky938 핸들이며 독립적으로 확인한 개인 신원은 아니다.

KBL 게시자 표기는 12승·6패, 손익 합계 +10.29단위이고 18건에 각 1단위를 걸었다는 가정의 산술 비율은 +57.166667%다. 실제 개별 행의 배당/손익 산술은 일치했다. **공식 승패를 확인했어도 경기 전 그 배당으로 구매 가능했는지는 확인하지 못했으므로 검증된 사전 ROI로 취급하지 않는다.** 부모의 회고적 가격 평가와 사전 검증 평가를 구분해야 한다. 사이트의 요약만으로 ROI를 인정하지 않는다.

NBA 101행의 게시자 표기는 82승·19패, 합계 +17.09단위다. 공식 결과를 대조하지 않았고 같은 경기의 총점 기준점이 다수 겹친다. 별도 설명 통계로만 남기며 KBL 결과와 합산하지 않는다.

날짜 페이지와 미래 경기의 상태는 나중에 바뀔 수 있다. 이번 관측값의 사실 JSON, 관측 시각, 본문 해시를 기준으로 재현한다. HTML 전체와 공개 클라이언트 JS 본문은 로컬 확인용으로만 남기고 `.gitignore`로 Git 추적에서 제외했다. Git에는 사실 JSON과 메타데이터만 남긴다.

## 확인한 대체 자료와 제외

현재 작업 폴더의 `data/processed/games.csv.tmp`는 2023/24 자료뿐이어서 조인하지 않았다. 부모가 알려준 `accuracy-formula-lab/data/processed/games.csv`는 존재하지 않았다. 읽기 전용으로 존재를 확인한 `official-international.json`은 EPL·K리그·MLB만 포함했다. `official-kbl-2024.json`과 `official-korean.json`은 각각 2024 및 2025 KBL 자료이므로 이번 2026 경기를 검증하는 데 사용하지 않았다. Naver 공개 일정 API의 2026년 3월 요청은 12초 제한에서 시간 초과했고 재시도하지 않았다. 2026 공식 검증은 위 KBL API를 직접 사용했다.

## 재실행

저장된 사실 JSON만으로 네트워크 없이 재생한다. HTML 파일이 없는 인계 환경에서도 같은 후보·공식 결과 JSONL을 바이트 단위로 재현하는 검증을 통과했다.

```powershell
python scripts/research_collect_kbl_tipsters.py --build
python experiments/tipster-roi-expansion-20261028/kbl/verify_parser.py
```

원문 파서는 `requests`, `beautifulsoup4`를 사용한다. 검증은 전 행 보존·패배/미정산 보존·중복 및 충돌 처리·배당/기준점·경기 시각과 게시 시각 구분·공식 조인·점수 불일치·미지 팀명·비지원 시장·본문 해시·요청 상한·사실 재생을 검사한다. 운영 코드 변경·푸시·개별 PR·병합·배포는 하지 않는다. 부모가 연구 PR로 통합한다.
