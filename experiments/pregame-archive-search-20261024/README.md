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
