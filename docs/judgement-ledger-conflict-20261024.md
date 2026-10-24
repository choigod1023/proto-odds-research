# 판정 원장 충돌 복구 — 2026-10-24

## 운영 증거와 재현 범위

부모 작업이 확보한 로그에서 load/decision/settlement가 진행된 뒤 ledger 단계가
`LedgerConflictError: conflicting rewrite for prediction dec_e887fc53f50cde9f`로
실패했다. 감독자는 저장된 배당으로 판정만 재시도해 새 원천 수집도 생략했다.
이번 관측은 수집 hang이 아니다. 별도 PR #344의 timeout 수정과 구분한다.

부모의 인덱스 기반 읽기 전용 조회 결과 해당 EPL 첼시–토트넘 기록은
as_of/market_observed_at=2026-10-23T10:08:23Z,
captured_at=2026-10-23T10:11:36Z,
research_player_inputs.observed_at=2026-10-23T10:10:57.173670Z,
source_updated_at=2026-10-23T18:40:31+09:00이다.
이 작업에서는 운영 DB를 직접 조회하거나 변경하지 않았다.

`refresh_document`는 재시도 때 연구용 capture의 observed_at을 새 실행 시각으로
찍는다. 이 필드는 decision_id에 포함되지 않지만 원장 features 변경 판정에는
포함됐다. 동일 결정 ID에 관측 시각만 달라져도 새 원장을 추가하려다 충돌한다.
운영 원장 전체와 실패 후보 전체를 비교한 것은 아니므로 다른 필드 차이의 동반
여부는 미확정이다. 이 결함 자체는 로컬 단건/배치 경로에서 재현했다.

## 수정

1. `pregame-player-capture-v1`이면서 affects_probability=false,
   training_eligible=false인 연구용 capture에 한해 observed_at만 revision 비교에서
   제외한다. 기존 원장에는 최초 값 그대로 남긴다. source_updated_at, 상태, 이유,
   선수·라인업 등 실제 내용은 여전히 비교한다. 과거 dedup 키나 해시는 수정하지
   않고 저장된 features에서 비교 서명을 다시 계산한다.
2. 진짜 LedgerConflictError가 남으면 원자적 배치 실패 후 개별 기록으로 격리한다.
   하나의 검증된 원장 세션을 재사용한다. 충돌 경기는 새 추천·스냅샷·표시용 원장
   연결을 제거하고 ledger_conflict 이유로 보류한다. 정상 경기는 계속 게시한다.
   기존 예측·정산 기록은 삭제하거나 덮어쓰지 않는다.
3. 원장 손상, 잠금 실패, 시간 역행과 그 외 오류는 성공으로 숨기지 않는다.
   충돌 건수는 live_market_refresh.ledger_sync.conflicts와 로그로 남는다.

## 검증

- 수집 시각만 달라지는 단건·배치 재시도: 변경 전 실패, 수정 후 통과.
- unavailable/captured 연구 자료 모두 재시도 후 원장 바이트 불변 확인.
- source_updated_at 또는 실제 상태 변화는 기존 ID의 충돌로 계속 거부.
- JSONL 및 SQLite에서 정상 경기 다음에 충돌 경기가 있는 배치 검증:
  기존 원장 전체 불변, 정상 경기 1건만 추가, 충돌 경기만 보류.
- 실제 refresh_once orchestration이 충돌 격리 후 persist와 rc0에 도달하고
  반복 실행으로 원장이 추가 변조되지 않는지 검증.
- Python 전체 및 프론트 테스트/빌드 결과는 PR에 기록한다.

## 운영 한계

별도로 확인된 디스크 여유 약 50MB 문제를 해결하는 변경은 아니다. ENOSPC 등
쓰기 장애가 남으면 이 수정 뒤에도 게시가 실패할 수 있다. 병합·배포·운영 재시작은
이 작업에서 수행하지 않았다. 실제 다음 자동 주기에서 원장 충돌, 판정 게시 시각,
새 배당 수집 재개, 충돌 보류 개수, 디스크 상태를 각각 확인해야 한다.
