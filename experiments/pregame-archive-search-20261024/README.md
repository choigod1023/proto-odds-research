# 과거 사전시점 아카이브 탐색

## 확보한 가장 강한 사례

NBA 2025-04-18 보고서의 실제 Wayback PDF payload를 조회했다. [공식 원본](https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-18_05PM.pdf)과 [제3자 보관본](https://web.archive.org/web/20250418223959id_/https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-18_05PM.pdf)을 비교했다.

- 문서 본문: 2025-04-18 17:30 ET. PDF 생성 메타데이터는 17:30:02 -04:00. 실제 최초 게시시각은 별도로 입증하지 못해 published_at=null이다.
- 제3자 캡처: **2025-04-18 22:39:59 UTC**. Availability API뿐 아니라 CDX timestamp/digest와 실제 보관 PDF를 확인했다. 리디렉션으로 현재 원본이 반환된 것이 아닌지 최종 URL도 검사했다.
- 이번 실제 HTTP 관측: 2026-10-24, 개별 요청 시각은 network_probes.json에 기록했다. 이 값을 과거 시각으로 바꾸지 않는다.
- 보관본 SHA1 base32가 CDX digest와 일치하고, 보관본과 현재 공식 원본의 SHA256도 일치했다. 보관 SHA256: `8f951c82fc0c896364aa0dfb038d5cb3506d90f4f51f953e12d84bbb7374d18e`.

두 경기 사례: 다음 날 MIL@IND의 Damian Lillard Out, DET@NYK의 Jaden Ivey Out. [NBA 일정 기사](https://www.nba.com/news/starting-5-april-19-playoffs-time-go-inside-todays-4-games)와 보고서 일정으로 각각 13:00 ET, 18:00 ET 예정임을 확인했다. 캡처가 각각 **18시간 20분 1초**, **23시간 20분 1초** 앞선다. 실제 팁오프 초 단위 기록은 검증하지 않았다. 두 사례는 같은 보관본 하나에서 나온 것이므로 독립된 두 데이터셋이 아니다.

이 자료는 제3자 보관시각 이후의 예측 입력 후보로 쓸 수 있다. 더 이른 예측에 소급 적용하거나 경기 당일 최종 상태로 간주하면 안 된다. 실제 운영 이벤트 ID·배당 관측시각과의 연결은 메인 담당의 후속 검증이 필요하다. 모델 효과나 ROI를 측정한 것은 아니다.

## 반례와 실패

같은 날 [12PM 파일](https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-19_12PM.pdf)의 실제 본문 시각은 12:30 PM이다. 확인된 가장 가까운 캡처는 4월20일08:32:29 UTC로 MIL@IND 경기 후이며, 경기 전까지만 조회한 해당 정확 URL의 CDX 결과는 빈 배열이었다. URL의 12PM 또는 HTTP Last-Modified만으로 사전 보존을 인정하면 안 된다. [01PM 파일](https://ak-static.cms.nba.com/referee/injury/Injury-Report_2025-04-19_01PM.pdf) 본문은 1:30 PM이라 해당 경기 예정 시작보다도 늦다.

[MLB 도쿄 2차전 FAQ](https://www.mlb.com/news/mlb-tokyo-series-2025-game-2-faq-dodgers-vs-cubs)는 게시·수정시각이 사전이지만 확인된 캡처는 사후라 제외했다. 구체 시각은 cases.json의 negative_controls에 있다. 가장 가까운 캡처가 사후라고 다른 URL·스냅샷 전체의 부재를 주장하지 않는다.

구단 timestamp 발표 후보는 Bucks 4월18일 게시물이 인용된 기사 검색 결과까지 확인했으나 원 게시물의 독립 보관본은 확보하지 못했다. 사전 증거로 채택하지 않았다.

기존 NBA 2024-25 injury index URL은 현재 2026-27 페이지로 리디렉션됐다. 역사 링크 목록이 고정돼 있다고 가정하면 안 된다. 웹 열람 도구의 Wayback 요청과 PDF screenshot은 실패했지만, 공개 HTTP 요청은 성공했다. PDF 전문은 메모리에서만 처리하고 보관하지 않았다. PyMuPDF 텍스트 추출로 해당 선수 행을 검사했으며 시각적 페이지 검증은 하지 못했다. 접근 제한을 우회하지 않았다.

## 재현·파일

- probe.py: 공개 원본·Wayback Availability/CDX를 제한된 병렬 요청으로 조회한다. requests, PyMuPDF 필요. 전체 기사/PDF를 파일로 저장하지 않고 응답 해시·메타데이터·짧은 행 존재 여부만 남긴다.
- network_probes.json: 실제 HTTP 상태, 최종 URL, 관측시각, archive API 응답, digest, 해시.
- cases.json: 메인 검증기에 넘길 후보 2개와 제외 대조 사례 2개. source_report_at / third_party_archive_at / observed_at을 별개로 유지한다.
- validate.py: 저장된 증거의 해시·제3자 digest·시간 순서를 Python assert로 검증한다. ROI는 null.

```powershell
python experiments/pregame-archive-search-20261024/probe.py
python experiments/pregame-archive-search-20261024/validate.py
```

재조회하면 관측 로그가 바뀌므로 기존 커밋은 보존해야 한다. 이 작업은 실험 폴더만 수정하며 미래 수집 코드·운영·PR·병합·배포는 건드리지 않는다.

## 후속: 로컬 경기·배당·예측 연결 감사

`local_join.py`를 실행한 뒤 `validate.py`를 실행하면 `local_join_audit.json`과 cases의 local_join을 재생성한다. 네트워크 요청 없이 지정한 accuracy-formula-lab, roi-lineage-audit-20261018, 공유 checkout의 data 폴더만 읽는다. 공유 checkout은 수정하지 않는다.

- MIL@IND: 현지 2025-04-19 13:00 EDT → 한국시간 **4월20일 02:00**. games.csv에서 2025년 47회차 278~280번을 연결했다. 데이터의 팀 별칭은 인디페이/밀워벅스다. 일반승패 저장 배당은 홈 1.33 / 원정 2.60, 저장 점수는 117–98이다.
- DET@NYK: 현지 2025-04-19 18:00 EDT → 한국시간 **4월20일 07:00**. 47회차 284~286번을 연결했다. 뉴욕닉스/디트피스이며 일반승패 저장 배당은 홈 1.26 / 원정 2.92, 저장 점수는 123–112다.
- 각각 일반승패·핸디캡·언더오버 3행이다. 이는 6개의 독립 경기가 아니다. 각 행의 market label, 원자료 경로, 행 번호, 정규화 행 SHA256은 JSON에 남겼다. 운영 event_id는 검증하지 않아 null을 유지한다. 점수는 로컬 저장값이며 이번 작업에서 공식 결과를 새조회한 것은 아니다.
- games.csv에는 배당 관측시각이나 동결된 예측 확률 컬럼이 없다. 따라서 위 숫자는 **과거 저장 배당**이지 경기 전에 실제 이용 가능했던 배당을 입증한 값이 아니다.

검색 상한: 루트별 항목 5,000개, games.csv 250,000행, 스냅샷 파일별 앞 5,000행, 총 1,000,000행. 실제 CSV 104개에서 **687,701행**을 스트리밍 처리했다. 5개 파일은 끝까지 읽었고 99개는 상한에서 멈췄다. 전체 파일·데이터를 출력하지 않았다. 조사한 ts 표본 범위는 2026-07-26T04:54:32+00:00 ~ 2026-09-05T03:17:21+00:00이었다. 표본 밖 전체의 부재나 과거 보관소 전체의 부재를 주장하지 않는다.

선택한 세 data 폴더의 파일명 인벤토리에서 해당 경기의 동결된 자체 모델 사전 예측은 확보하지 못했다. pickster_eval 및 tailslips 이벤트, 임시 db-export 파일은 자체 모델의 사전 확률 증거로 인정하지 않았다. bets.csv의 사후 파생 손익도 동결 예측으로 간주하지 않았다. 다른 실험 폴더, 운영 DB, 원격 저장소는 검색하지 않았다.

**현재 적격 전체 연결 0건, ROI=null(계산 불가)**. 결장 사실의 사전 존재는 입증했지만 같은 컷오프에서의 배당·모델 예측이 빠져 있다. 현재 모델을 과거 결과에 다시 맞추거나 점수에서 픽을 역산해 빈칸을 채우지 않았다. 월 손실 5만원 모의 운용 역시 이번 자료만으로 계산하지 않는다.

검증: Python에서 두 경기의 EDT→KST 변환, 각 경기 시장 3행 이상, 사전 배당 관측시각 미확보, ROI null, 기존 아카이브 digest·시간 순서를 검사했다.
