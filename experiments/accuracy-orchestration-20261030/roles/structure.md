# 승부예측 Terra · 자료구조

브랜치: `codex/terra-accuracy-structure-20261030`
작성 범위: `experiments/accuracy-structure-20261030/**`, `tests/test_accuracy_structure*.py`, `docs/research/2026-10-30-accuracy-structure*.md`

자료구조는 수업 문제가 아니라 예측 데이터 구조·스키마·DB 연결 계약을 뜻합니다.
1) 경기/상품/가격revision/선발·타순revision/원문관측/동결/공식정산을 분리하는 v1 JSON 계약과 Python 검증기를 구현하십시오. canonical_game_id와 official_id의 alias bridge, doubleheader/reissue/취소를 결과값 없이 구분합니다.
2) published_at 주장, first_received_at 실제관측, decision_at, final_at, source_sha256, availability_tier, 외부인증 상태를 각각 보존합니다. 누락·순서역전·사후수신·서로다른시장/기간·변경된동결픽은 fail-closed. 내부해시체인만으로 인증true를 만들지 않습니다.
3) 공통 provisional-interface.json과 PR431/433 자료를 읽고, 유효/무효 합성사례와 동결 후 결과 추가를 실제 Python으로 검증합니다. DB migration은 실행하지 않고 인덱스/정규화 제안만 적습니다.
4) 결과는 담당 schema.json, validator.py, fixture와 고유 테스트, 보고서/hand-off에 남깁니다. 모델 개발/연결 워커가 채택할 수 있게 v0와의 호환/변경 목록을 명시합니다.
