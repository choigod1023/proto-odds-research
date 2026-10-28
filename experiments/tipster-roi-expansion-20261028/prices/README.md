# 실제 수집 결과와 부모 평가기 연결

- 실제 Proto primary-market offer: **0건** (`offers.jsonl`은 의도적으로 비어 있음).
- KBO 공식 2026-09-02 종료 경기: **5건** (`official_results.jsonl`).
- HTTP 총 7회 / 상한 25회, 서로 다른 회차 2개 / 상한 10개. 최소 요청 간격 2.5초. 재시도·리다이렉트·인증·우회 없음.
- DB, `.env`, 운영 수집기 `fetch_round()`는 사용하지 않았다. 네트워크 세션의 `trust_env=False`를 적용했다.
- 원본 응답은 `source-01.bin`부터 `source-07.bin`까지 바이트 그대로 보존했다. `manifest.json`에 URL·메서드·POST 인자·응답 헤더·수집 시각·SHA-256·실패 사유가 있다. 초기 1~6번 요청 헤더는 실행 당시 로깅하지 않았으며, 이 한계와 당시 설정을 별도로 명시했다.

## 초기 수집기 실수와 확보 실패

초기 수집기는 기존 `src/wisetoto.py` 클라이언트가 설정하는 `X-Requested-With: XMLHttpRequest`를 누락했다. 회차 인덱스는 성공했지만 두 경기 목록 요청은 HTTP 403과 정확히 `잘못된 접근입니다.[code:gproto_xrw]`를 반환했다. 헤더 누락이 유력한 원인이며, 일반적인 접근권한 차단이라고 단정하지 않는다. 수정 헤더로 요청하지 않았으므로 원인 확정이나 회복 성공도 주장하지 않는다. 두 회차를 순서대로 시도한 뒤 지속적인 호스트 중단 가드를 추가했다. 동일 요청은 반복하지 않았다.

최종 스크립트에는 표준 AJAX 헤더를 복원했지만, 이 실행 기록에서 403을 감지하면 추가 와이즈토토 호출을 거부한다. 요청 제한 및 재시도 금지에 따라 수정 요청을 새 회차로 바꿔 보내지도 않았다.

- 요청 1: 2025/85 인덱스 HTTP 200, `master_seq=29277`. 발매 시작 `2025-07-21 14:10` 확인. 경기 날짜·K리그 경기 포함 여부는 확인하지 못했다.
- 요청 2: 해당 경기 목록 HTTP 403, 위 오류 본문. 배당·결과 0건.
- 요청 3: 2026/104 인덱스 HTTP 200, `master_seq=31427`. 발매 시작 `2026-09-02 08:00` 확인. 경기 날짜·리그 구성은 확인하지 못했다.
- 요청 4: 해당 경기 목록 HTTP 403, 같은 오류 본문. 배당·결과 0건.
- 요청 5: KBO 공식 `/Schedule/Schedule.aspx` HTTP 200. 인라인 JS에서 월별 일정 API 및 POST 인자를 확인했다.
- 요청 6: 공식 `/ws/Schedule.asmx/GetScheduleList`에 2026년 9월 정규시즌 조회 POST. HTTP 200이나 본문은 JSON 대신 KBO 오류 HTML이었다. 초기 구현의 Wisetoto Referer가 남아 있었으며, 이것이 오류 원인인지는 확인하지 않았다. 재시도하지 않았다.
- 요청 7: 저장소의 `src/kbo_official.py`에 있는 공식 `/ws/Main.asmx/GetKboGameList`에 `leId=1`, `srId=0,1,3,4,5,6,7,8,9`, `date=20260902` POST. KBO GameCenter Referer를 설정했고, HTTP 200 / `code=100` / 종료 경기 5건을 받았다.

10월 확장과 추가 회차 수집은 하지 않았다. 기존 실험의 과거 배당 표본을 새로 확보한 원문인 것처럼 합치지 않았다. 제3자 결과를 공식 결과로 사용하지 않았다.

## 결과 파일 스키마

`official_results.jsonl`은 메타데이터 헤더 행 없이 **경기당 JSON 객체 1개**다.

- 조인 후보: `kickoffKST` (ISO 8601, `+09:00`), `league`, `home`, `away`. 팀명은 공식 원문 그대로이며 별칭·퍼지 매칭을 적용하지 않았다. `game_id`, `home_code`, `away_code`도 보존했다.
- 점수: `raw_scores.B_SCORE_CN`=홈, `raw_scores.T_SCORE_CN`=원정. 정수형 `home_score`, `away_score`를 함께 제공한다.
- 결과: `winner`=`home` / `away` / `draw`; `winner_team`은 승리 팀 원문 이름이다. `final_state_evidence`에 `GAME_STATE_SC=3`, `CANCEL_SC_ID=0`, `GAME_RESULT_CK=1`을 보존한다.
- 가격 부재: `record_type=official_result_only`, `market=null`, `odds=null`. **이 파일을 배당 offer나 ROI 표본으로 세면 안 된다.**
- 근거: `result_source_url`은 공식 GameCenter 경기 URL, `result_endpoint_url`은 실제 조회 API다. `source_request_id`, `source_file`, `source_sha256`, `collected_at_utc`, `result_source_quality=official_KBO_final_result_retrospective`를 포함한다. POST 요청의 전체 인자는 manifest에서 확인한다.

실제 결과는 홈팀 기준으로 두산 1–5 LG, 삼성 8–5 롯데, KT 9–6 한화, NC 3–2 KIA, 키움 6–9 SSG다. 모두 `2026-09-02T18:30:00+09:00`이다.

`offers.jsonl`은 현재 0바이트다. 수집에 성공했을 때의 스키마는 `kickoffKST`, `league`, `home`, `away`, `raw_scores`, `home_score`, `away_score`, `market` (`1X2` 또는 `moneyline_2way`), `odds` (홈·무·원정 또는 홈·원정 전체 벡터), `winner`, `year`, `round`, `game_no`, 원문 마켓 표시, 출처 URL/해시다. 배당 품질은 항상 `retrospective_archive_not_pregame_certified`로 표시한다. 경기 전 관측·실제 구매 가능성은 증명하지 못한다.

같은 날짜·리그·홈·원정·마켓에서 시각/배당/점수/결과가 다르면 재발매 충돌 또는 더블헤더 모호성으로 전량 거부한다. 동일 값만 중복 증거를 남겨 하나로 줄인다. 불완전 배당 벡터, 전반/핸디캡, 알 수 없는 팀·점수 구분, 결과 라벨 불일치는 받아들이지 않는다. 실제 HTML 경기행을 확보하지 못했으므로 primary-market HTML 파서는 합성 fixture로만 검사했으며 실사이트 검증이 끝났다는 의미가 아니다.

## 오프라인 재현과 검증

작업 트리 루트에서 실행:

```powershell
python -X utf8 scripts/research_collect_tipster_prices.py build
python -X utf8 scripts/research_collect_tipster_prices.py selftest
```

`build`는 네트워크 없이 원문 7개의 SHA-256을 검증한 다음 결과 JSONL과 `summary.json`을 재생성한다. `selftest`는 실제 공식 5경기의 홈·원정 점수/승자, 날짜·상태·취소·누락점수·ID 불일치 6가지 거부 사례, 중복/충돌, 합성 primary-market 벡터, 요청 상한·간격을 검사한다. 합성 fixture는 산출 JSONL에 쓰지 않는다. `rejected.jsonl`도 실제 거부 경기행이 없어 비어 있다. HTTP/본문 오류 3건은 `summary.json.failures`에 따로 남긴다.

이 하위 작업은 가격·결과 근거만 제공한다. 픽스터·모델·하이브리드 비교, ROI 평가, 운영 채택이나 배포를 완료했다는 의미가 아니다.
