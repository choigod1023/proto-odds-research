# 베트맨 프로토 비교 가격 어댑터

공개 G101 마감 목록과 회차 상세만 조회한다. 목록의 `start` 및 상세의 `next_round_cursor`를 capture별 `state.json`에 저장한다. 한 실행에서 새 요청은 최대 20회이고 `--max-requests`, `--total-timeout`으로 더 줄일 수 있다. 완료, 제한에 따른 중단, 오류를 구분한다. 403/429는 모든 capture에 적용되는 중단 표식을 남기며 자동 재시도하지 않는다. 캐시는 압축 해제한 원문의 SHA-256을 매번 확인하고, 새 응답 본문은 최대 16 MiB까지만 읽는다.

```
python scripts/collect_proto_comparison_prices.py --offline-cache --output experiments/pregame-proto-prices-20261104/normalized-price-records.jsonl.gz
python scripts/collect_proto_comparison_prices.py --capture-id historical-001 --evidence C:/Users/user/Documents/Codex/pregame-proto-price-evidence --output C:/Users/user/Documents/Codex/pregame-proto-price-evidence/historical-001.jsonl.gz --max-requests 20 --total-timeout 120
python scripts/collect_proto_comparison_prices.py --capture-id future-unique-001 --round 260130 --start 2026-11-04 --end 2026-12-31
```

같은 capture ID는 동일 조건으로 이어서 실행한다. 새 시점의 가격은 반드시 새 capture ID를 사용한다. `--round`는 회차 목록을 거치지 않고 공식 상세를 새로 요청한다. 출력과 상태 및 새로운 gzip 원문은 `C:/Users/user/Documents/Codex/pregame-proto-price-evidence/<capture-id>/`에 있다. `--evidence`는 새 캡처의 상태·원문 디렉터리, `--output`은 처리한 회차의 통합 JSONL 또는 `.gz` 출력 경로다. 전체 수집을 완수하려면 여러 실행이 필요하며 종료 여부와 커서를 확인해야 한다. 이전 KBO·K리그 원문은 해시 확인 후 읽기 전용으로 재사용한다. 과거 캡처 코드는 응답 본문 완료 후 시각을 기록했음을 입증하지 못하므로 `timestamp_basis=legacy_unverified`, `timing_basis=retrospective`로만 표시한다.

새 응답은 본문을 모두 읽은 다음 UTC 시각을 기록한다. 발매 진행, 발매기간, 미래 경기, 상품별 제한 표시를 모두 확인한 경우만 `actual_receipt`다. 이는 수신 시점의 원문 가격을 뜻하며 실제 주문 가격이나 체결을 증명하지 않는다. 마감 회차는 `retrospective`이며 과거 예측 생성이나 경기 전 관측으로 취급하지 않는다. 핸디캡·특수 상품과 가격 누락은 보수적으로 제외한다. 현대 농구 상품 코드와 종목별 정산 규칙의 공식 근거는 아직 확인하지 못했으므로 정산 규칙은 모두 `unknown`으로 보류한다. 결과를 정산 규칙이 확인된 동일 상품의 비교에 곧바로 사용하면 안 된다.

## 검증과 한계

- `python -X utf8 -m unittest discover -s tests -p test_proto_comparison_prices.py -v`: 8개 통과. 캐시 해시, 오프라인 재생, 16 MiB 상한, 본문 완료 후 시각 기록, 제한 중단, 커서 재개, 403 중단과 상품 제외를 검사했다.
- 실제 HTTP 1회: 2026-11-04에 회차 260040 상세 요청 중 TLS 연결이 원격에 의해 끊겼다(WinError 10054). HTTP 응답과 새 원문은 없으며 재시도하지 않았다. 실패 상태는 `smoke-20261104-closed-260040/state.json`에 있다.
- 기존 KBO·K리그 근거의 해시 검증된 상세 원문 147회차를 오프라인 파싱한 검증에서는 경기 시작일 2025-02-14~2026-11-01, 축구 3,430·야구 6,324·농구 972행, 합계 10,726행이었다. 해당 행은 `normalized-price-records.jsonl.gz`에 저장했으며, `manifest.json`은 선택한 147개 원문의 경로·회차·해시·수신 시각 및 미검증 상태를 고정한다. `--offline-cache`는 이 매니페스트의 원문을 해시 검증하고 네트워크 없이 재생한다. 원본 경로가 없거나 내용이 바뀌면 실패한다. 10,726은 가격 행 수이며 독립 경기 수가 아니다. 전체 기간 census가 아니고 중복 경기·동일 상품 정산·경기 전 가격을 검증한 성능 비교 표본도 아니다. 신규 수집량은 0건이다.
- 현재 구현의 소켓 읽기는 제한시간을 반복 점검하지만 DNS 해석 또는 진행 중인 단일 소켓 연산은 운영체제 동작에 따라 총 제한시간을 약간 초과할 수 있다. 엄격한 프로세스 단위 강제 시간 제한은 제공하지 않는다.
- 모델 학습·예측·운영 DB·배포·베팅은 수행하지 않았다.
