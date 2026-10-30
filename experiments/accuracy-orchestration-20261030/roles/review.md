# 승부예측 Terra · 독립 검토

브랜치: `codex/terra-accuracy-review-20261030`
작성 범위: `experiments/accuracy-review-20261030/**`, `tests/test_accuracy_review*.py`, `docs/research/2026-10-30-accuracy-review*.md`

1) PR433 최종0ed675a0의 source/protocol/입력/선택/확률/정산/가설검정을 독립 검토하십시오. 전방수신 가용성, +4day 대용시각, frozen/prequential 구분, samefixture/price, 홈상수, 실제투표와proxy 분리, 후보 선택 시점, 공분산/적분 정합성을 확인합니다.
2) 최소 하나의 독립 Python 검산기를 작성해 실제 저장 예측과 원문 입력에서 적중률/ROI/coverage를 재계산하고 차이를 기록합니다. 같은 함수를 호출한 것을 독립 구현으로 부르지 않습니다. 확인한 결함은 작은 재현fixture/수치 영향/코드 줄과 함께 보존하고 원본을 직접 고치지 않습니다.
3) 다른 워커 PR은 첫 결과가 나온 뒤 총괄이 후속 배정합니다. 지금은 자료구조/모델 개발 코드를 기다리거나 재작성하지 말고 PR433 및 provisional 계약을 검토합니다.
4) 보고서는 확인된 문제/추정/이미 공개된 한계를 나누고, 적중률 상승 또는 운영 반영 가능 판정을 분리합니다. 결과없는 일반 체크리스트만 제출하지 않습니다.
