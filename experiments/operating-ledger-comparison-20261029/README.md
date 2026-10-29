# 운영 원장 비교 — 2026-10-29

요약·입력 정의·제외·재현·한계는 [연구 보고서](../../docs/research/2026-10-29-operating-comparison-results.md)에 있다.

- `comparison.json`: 로컬에서 실제 실행한 정책별·리그별 지표와 경기별 비교. `policies`는 정책 문맥(대체 선택·보류 포함), `operating_versions`는 실제 선택한 운영 버전이다. 두 집계를 혼동하지 않는다. `selected_three_arm_intersection`만 같은 선택 기회의 세 모델 비교다.
- `production-ledger-readiness.json`: 전체 8,674행 해시 검증과 원문 SHA. 이 파일의 버전별 사전 감사는 해당 버전을 먼저 추린 진단값이다. 최종 비교는 전체 원장에서 최신 revision을 먼저 선택하므로 표본 수가 다를 수 있다.
- `official-results.json.gz`: proto 결과 테이블의 2026년 120회차 이후 전체 교정 이력. 현재 미확정 기록도 포함하며, 전체 원장·모든 과거 경기 결과를 보유한 것으로 부르지 않는다.
- `official-results-metadata.json`: 읽기 전용 조회 범위·조회 시각·행 수·압축 전 SHA.
- `live-recommendations-metadata.json`: 공개 운영 API의 조회 시각·원문 SHA·종료된 shadow 실험 집계.

전체 예측 원문과 공개 API의 상세 원문, 모의 지갑 원문은 로컬 `.local-test/` 또는 이 폴더에만 보관하고 공개 Git에는 추가하지 않는다. 원문이 없으면 전체 원장 해시 재검증을 재현할 수 없다는 한계를 숨기지 않는다. CLI는 전체 원문을 제공해야 실행할 수 있고 검증 실패를 무시하는 옵션은 없다.

비교안 선택은 결과 조회 전에 동결한다. 현재는 새 학습 모델을 평가하거나 자동 승격한 실험이 아니다. 실제 운영 변경 여부는 PR의 병합·배포 상태와 별도로 확인해야 한다.
