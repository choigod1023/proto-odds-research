# 동결 야구 후보 연결 실행 근거

[전체 보고서](../../docs/research/2026-10-29-baseball-candidate-integration.md)를 먼저 읽는다.

- `deployment-414-verification.json`: 병합·배포 뒤 다음 자동 판정이 실제로 쓴 비교 원장 3건과 8,678건 해시 재검증. 야구 후보 실행 결과가 아니다.
- `replay.json`: 모델을 재학습하지 않은 과거 1,220행 재현. 평가/검증과 시장/연구9:1 기준을 분리한다.
- `official-kbo-*`: 공식 일정 원문과 실제 HTTP 수신 시각·SHA. 휴식일도 포함한다.
- `kbo-live-source-example.json`: 실제 지금 확보한 원천으로 만든 29/30일 상태 예시. 과거 seed 가용 시각은 증명되지 않았다.
- `local-resource-sample.json`: 로컬 합성 입력의 warm capture 측정. 운영 peak/RSS 측정이 아니다.

새 후보는 수익성 검증 통과·실제 추천 승격이 아니다. 실제 모의 지갑의 월 손실 5만 원 제한을 유지한다. 회고 ROI는 해당 과거 입력·가격·시점 한계를 가진 가상 단폴 계산이며 사용자의 실제 수익이 아니다.
