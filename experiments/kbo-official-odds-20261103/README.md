# KBO 공식 과거 회차 배당 필드 조사

상태: `ready_for_join` (소급 자료 결합에 한함). 2026-11-03에 베트맨의 공개 마감회차 API와 회차 상세 API를 실제 조회했다. 원본 `rows.jsonl` 및 `labels.jsonl`은 수정하지 않았다. `restored-prices.jsonl`은 별도의 보조 자료다.

## 결과

시장 정보가 비어 있는 454경기 가운데 373경기를 공식 회차 상세의 2-way 승패 배당 필드에 보수적으로 연결했다. 기존 무승부 60경기 중 56경기가 포함된다. 연도별 연결 수는 2023년 12, 2024년 10, 2025년 22, 2026년 329다. 발매기간이 경기일을 포함하는 회차가 없는 47경기와 다른 검증 조건을 통과하지 못한 34경기는 제외했다. 취소·판매 거부 표시 또는 점수 불일치를 무시하고 복원하지 않았다.

373건 중 249건은 기존 입력에 경기 시작 시각이 없어 공식 경기 시각과 기존 시각을 비교하지 못했다. 이 경우에도 날짜·팀·스코어가 같고 같은 날 같은 홈·원정 경기 중복이 없으며 공식 후보가 하나인 경우만 연결했다. `source_kickoff_matched: false`로 구별하므로 결합 시 필요한 신뢰도 조건에 따라 제외해야 한다.

세 표본의 회차·경기번호는 기존 입력과 공식 응답에서 팀, 일시, 결과 및 일반 승패 유형을 대조했다. 값 순서는 홈·원정이다.

| 경기 (KST) | 프로토 승부식 | 공식 `winAllot`, `loseAllot` | 결과 |
| --- | --- | --- | --- |
| 2023-04-14 18:30 KT–한화 | 43회 28번 | 1.36, 2.49 | 7:7 |
| 2024-03-27 18:30 LG–삼성 | 39회 1번 | 1.57, 2.05 | 2:2 |
| 2026-03-31 18:30 삼성–두산 | 40회 192번 | 1.61, 1.89 | 5:5 |

기존 결과 CSV에는 위 세 항목의 배당이 각각 `1.00,1.00`으로 저장돼 있다. 공식 상세의 별도 승·패 배당 필드는 이 값보다 유용한 소급 자료지만, 응답은 **이 배당의 과거 관측 시각, 실제 구매 시점, 판매 중 변경 이력 또는 T-30 시점 적용 여부를 증명하지 않는다**. 해당 필드가 최초 배당인지 최종 발매 배당인지도 확정하지 않았다. 모든 결과의 `source_observed_at`은 null, `execution_price_verified`는 false다. `received_at`은 과거 시각이 아닌 이번 응답 수신 시각이다. 이 자료만으로 실거래 가능성, 정시 예측, 수익률을 주장하지 않는다.

## 조회와 검증

- 공개 목록: `POST https://www.betman.co.kr/buyPsblGame/closedList.do`, JSON `{"gmId":"G101","draw":1,"start":0,"length":20,"_sbmInfo":{"debugMode":"false"}}`. 응답의 `schedules.data`와 회차 발매 시작·종료 날짜를 사용했다.
- 상세 예: `POST https://www.betman.co.kr/buyPsblGame/gameInfoInq.do`, JSON `{"gmId":"G101","gmTs":"230043","gameYear":"2023","_sbmInfo":{"debugMode":"false"}}`. 해당 엔드포인트는 [공개 클라이언트 구현](https://github.com/peterdokim/betman-predictor/blob/HEAD/betman_predictor/client.py)에서 발견했고 실제 공식 응답을 받았다. 공식 [마감게임 페이지](https://www.betman.co.kr/main/mainPage/gamebuy/closedGameList.do)도 수신했다.
- 각 요청 URL, JSON, HTTP 상태, 응답 수신 시각과 원본 SHA-256은 `evidence/attempts.json`에 있다. `*.body.gz`는 실제 응답 바이트를 압축한 것이며 기록된 SHA-256은 **압축 해제한 원본**의 해시다. 상세 102회차 모두 HTTP 200이었다. 회차 상세 요청 사이에는 1초 이상 간격을 두고, 15초 timeout, 최대 110회차, 403/429 즉시 중단을 적용했다. 기존 응답은 해시 검증 후 재사용한다.
- 공식 `compSchedules.keys`와 `datas`를 결합해 확인한다. 구 형식은 시장 ID가 없고 `handi=0`, 신 형식은 `betId=2`, `betTypId=2`가 일반 야구 승패다. 신 형식의 `handi=21`은 경기 핸디캡 수치가 아닌 유형 코드다. 3-way, 다른 종목/상품, 취소·판매 거부, 결과 점수 불일치, 복수 후보를 제외했다.
- 네 건의 미복원 무승부 중 하나는 발매기간 기준 후보 회차가 없고, 나머지는 점수 불일치 또는 판매 거부 등 검증을 통과하지 못했다. 발매기간으로 고른 회차 밖에 해당 경기가 존재할 가능성까지 부정한 것은 아니다.

기준 데이터 SHA-256: `rows.jsonl` `66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c`, `labels.jsonl` `ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca`. 결과와 경기 원본 해시는 `manifest.json`을 따른다.

로컬에서 `python -X utf8 experiments/kbo-official-odds-20261103/restore.py`로 캐시된 원문을 재검증해 결과를 재생성하고, `python -X utf8 -m unittest discover -s experiments/kbo-official-odds-20261103 -p 'test_*.py' -v`로 표본, 상품 분류, 거부 조건, 원문 해시와 결과 출처를 검사한다. 원격 생성기, 운영 DB, 원장, 서비스 배포는 변경하지 않았다.
