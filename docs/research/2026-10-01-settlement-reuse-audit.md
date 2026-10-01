# 기존 정산 연결·ROI 기능 재사용 감사

2026-10-01. 기존 구현과 로컬 추적 자료를 읽고 기존 경량 테스트·재생 경로만 실행했다. **정산 연결과 ROI 집계는 이미 구현돼 있다. 연구에 사용한 로컬 예측 원장의 정확한 공식 결과 연결 자료가 부족한 것이며, 새 정산 엔진을 만드는 문제로 취급하면 중복이다.**

작업 트리 `dc-incremental-validation-20261001`는 main `97ffc5da`에서 만들고 이전 PR258 연구 산출물 `0854eb49`를 포함했다. 병렬 작업으로 HEAD가 진행하므로 이 감사의 운영 코드 기준은 `97ffc5da`다. 감사 담당자는 브랜치나 다른 파일을 변경하지 않았다. 공식 결과 경량 갱신 수정 `060ba87e`는 `git merge-base --is-ancestor 060ba87e 97ffc5da`가 0을 반환해 해당 main에 포함됨을 확인했다. 포함 여부는 운영 배포 완료를 뜻하지 않는다. 이 정산 감사에서는 원격·운영 서비스·운영 DB를 조회하지 않았다.

## 이미 존재하는 구현

1. `src/ai_decision.py:104`의 `event_id`, `:116`의 `selection_id`, `:124`의 `offer_id`: 실제 경기, 시장·기준점·선택, 회차·게임번호를 구분한다. 같은 경기 결과라고 다른 발매 offer를 대신 연결하지 않는다.
2. `src/live_market_refresh.py:433`의 `settle_live_market_results`: 공식 시장 피드에서 `(event_id, selection_id, offer_id)`가 유일하게 일치하는 결과만 연결한다. timezone 있는 피드 시각, 경기 종료 시점, 최대 90일 범위, 시장별 결과·취소를 검사한다. 중복·미확정은 건너뛴다. `refresh_once`에서도 문서 변경이 없는 경우 정산을 처리한다. 이 수정과 이미 시행한 0건 재생이 `docs/LEDGER_SETTLEMENT_REPAIR.md`에 기록돼 있다.
3. `src/prediction_runtime.py:367`의 `settle_latest`: 현재 선택한 event의 revision을 `snapshot_id`로 `append_settlement`에 전달한다. `:186`의 latest 규칙은 as_of/ledger_sequence 순서다. 따라서 임의의 과거 revision 전체를 정산하는 API와 혼동하면 안 된다. 결과 해시 기반 settlement_version으로 동일 결과 중복을 막고 수정 결과는 추가한다.
4. `src/prediction_ledger.py:271`의 `append_settlement`: 기존 예측 snapshot을 참조하는 append-only 정산. `src/prediction_runtime.py:401` 부근 `ui_records`는 settlement를 같은 snapshot_id에만 붙여 원래 선택·확률·가격을 보존한다. 실제 로컬 replay는 기존 해시 체인을 검증한다.
5. `src/prediction_runtime.py:432`의 `tally_prediction_records`: hit/miss 집계와 저장된 유효 가격의 단건 동일금액 모의 ROI가 있다. 무효·미판정은 이 분모에서 제외한다. `src/prediction_performance.py:2`의 `performance_index`는 event별 canonical saved prediction을 노출한다.
6. 전체 생성 경로도 `src/generate_v2.py:1116`의 `_sync_prediction_runtime`에서 `settle_latest`를 호출한다. `_settlement_outcome`(:1099)은 저장된 selection_id에 대응하는 options의 공식 적중값을 사용한다. 경량 피드 경로의 exact-offer 검사와 전체 생성기의 선택지 결과 경로를 동일 구현이라고 뭉뚱그리지 않는다.
7. `scripts/replay_ledger_results.py:15`: 기존 원장을 **새 파일**로 복사하고 보관된 공식 JSON/JSON.gz 피드를 시간순으로 기존 정산 함수에 전달한다. 기존 출력 덮어쓰기와 PROODD_DB_PATH DB 모드를 거부한다. 새 connector나 별도 정산 매퍼 없이 재사용할 경로다.

별도 paper 비교도 이미 있다. `src/per_event_shadow.py`의 `eligibility/proposals/capture`는 검증된 확률·불확실성·decision provenance 조건으로 후보를 만들고, 신선한 입력/T−30/28일 창/최대 2,000 events 조건으로 첫 관측을 고정한다. baseline/challenger의 단건 순손익, ROI, 둘 다 정산된 event의 paired 손익 차이와 abstention을 저장한다. 이 경로는 `src/recommendation_history.py:77`의 `settle_history`를 사용한다. 회차 내 home/away/date/market/label/game_no 유일 일치와 공식 결과를 쓰지만 **prediction ledger의 snapshot_id 기반 append-only 정산과는 별도 상태 구조**다. paper ROI는 무효도 settled 분모에 포함하며 손익 0으로 처리하므로 위 tally와 분모가 다르다. 실제 구매/두폴 실현 ROI로 해석하지 않는다.

## 원장 1,326행과 보관 피드의 0건 재생을 직접 재현

기존 `python -B scripts/audit_local_prediction_inputs.py data/raw/prediction_ledger/pregame.jsonl` 출력:

- SHA256 `77c224f03c28217f552cb98b96a02e350a1e0e79d12a7145a81a3296a693d4e0`.
- 1,326 prediction revisions, 고유 event 207개, 고유 snapshot 1,326개, settlement/result 필드 0개.
- 관측 2026-08-28T12:12:14Z~2026-09-01T23:01:31Z. captured/observed 모두 T−30 이전 조건은 1,299행/206경기, 제외 27행.
- `ui_records`의 market_reference 대상은 163 event다. 전체 207 event와 같은 분모가 아니다. 해당 코드 경로의 계산 결과이며 다른 action을 임의로 이 경로에 편입하지 않았다.

기존 replay 명령에 아래 두 **로컬 추적 피드**를 함께 전달했다. 자식 프로세스에서 PROODD_DB_PATH를 제거해 DB 모드를 쓰지 않았고 원본 대신 유일한 임시 경로에 출력했다.

```powershell
python -B scripts/replay_ledger_results.py data/raw/prediction_ledger/pregame.jsonl <new-nonexisting-output.jsonl> docs/data/live_odds.json findings/pick-performance-audit-20260906/live-odds.json.gz
```

출력은 `original_records=1326, appended_settlements=0, retrospective_recovery=true, production_changed=false`였다. 임시 결과는 `C:/Users/user/AppData/Local/Temp/settlement-reuse-audit-xdupbl2q/replayed-ledger.jsonl`에 보존했다. 기존 원장은 변경하지 않았다.

추가로 기존 정산 함수의 offer_id 호출을 **프로세스 메모리에서만** 계측하고, 쓰기 없는 runtime 대역으로 일치 키 수를 확인했다. 후보는 함수의 종료시각·결과·시장 검사 후 만들어지는 키다.

- `docs/data/live_odds.json`: generated_at `2026-09-05T07:25:57+00:00`, 정산 후보 event 50개/선택 키 575개. 로컬 대상과 event 일치 2개, event+selection 일치 2개, **event+selection+offer 일치 0개**.
- `findings/pick-performance-audit-20260906/live-odds.json.gz`: generated_at `2026-09-06T02:48:24+00:00`, 후보 event 134개/선택 키 1,556개. 같은 순서의 교집합 2/2/**0**.
- 압축 해제 원문 SHA256은 각각 `2eb98a069667e290e3ee790ff4ea34e98eab035c6e14f67ffd6986a94a4c4046`, `e24079339758b978d9b15b4dd064f2ad67b7b9556eddec76103ea92ca850013e`다.

두 피드에서 공통으로 보인 불일치:

- event `evt_0d5c28c11887da32`, selection `sel_cb2d6e8b1071c8ca`: 원장 offer `off_d39aace07d94eef7`, 피드 offer `off_8611d0dcc81a7e49`(회차105/게임8817).
- event `evt_9e296d861e2ce549`, selection `sel_fbc186a5284d8fac`: 원장 offer `off_969a48d5760999af`, 피드 offer `off_987991e5fdf69f9a`(회차105/게임8821).

따라서 단순 경기명/선택 일치로 연결하면 exact-offer 계약을 우회한다. 원래 offer의 회차·게임번호를 이 감사에서 역추정하지 않았고, 다른 피드에 필요한 결과가 있을지까지 단정하지 않는다. 확인한 두 피드가 정확한 과거 offer 정산을 제공하지 않는다는 결론이다. **이미 문서에 있던 0건을 재확인한 것으로, 새로운 정산 기능 구현이 필요한 증거가 아니다.**

## 계산 가능한 기존 역사 자료와 계산할 수 없는 것

`findings/pick-performance-audit-20260906`에는 picks/live-odds/live-scores/today-recommendations 압축 스냅샷과 report.json/README가 있다. 기존 `scripts/audit-pick-performance.mjs`의 export `extract`와 `metrics`를 import해 메모리에서 재계산했다. CLI `--input`은 report.json을 쓰므로 이번 감사에서는 실행하지 않았다.

- 저장 사전 픽 355건: hit 93, miss 68, pending 193, void 1. hit/miss 161건 적중률 57.7640%, 원래 저장 배당으로 단건 1단위 모의 ROI **−5.7702%**.
- 공식/ledger 판정만 제한하면 104 hit/miss(55/49), void 1, 적중률 52.8846%, 모의 ROI **−13.6250%**.
- 나머지 판정 57건은 기존 서비스의 종료 점수 기반 임시 판정이며 공식 결과와 구분한다. 재실행 결과 중복/충돌 검사도 0이었다.
- 이 스냅샷 표본의 모의 ROI는 재현 가능하다. 다만 원장 전체와 동일 표본인지, 모든 추천 노출 이력인지, 미판정 누락이 무작위인지, 실제 구매가 있었는지는 입증하지 못한다. 현재 운영 전체의 실제 ROI/완전한 누적 성과로 보고하면 안 된다. 해당 구분은 기존 README에도 있다.

다른 로컬 추적 역사 자료도 확인했다:

- `data/q0_result.txt`: 과거 배당 검증의 집계 보고서다. 첫 부분에 353,047 betting records/540회차/2023~2026 및 이론·실측 ROI 집계가 있다. 해당 prediction revision별 공식 정산 자료는 아니다.
- `data/processed/bets.csv.tmp`: CSV 직접 계수 결과 **178,526행**, 2023년85,584/2024년92,942. odds/won/profit 컬럼이 있고 won=0 101,599행, won=1 76,927행. `games.csv.tmp`는 **78,403행**, 2023년37,960/2024년40,443, is_void=False76,940/True1,463. Q0 보고서의 353,047행·2023~2026과 같은 완전 자료라고 가정할 수 없다.
- `src/q0_scan.py`는 `data/processed/bets.csv`를 기대한다. 추적 목록에는 그 정식 파일 대신 위 `.tmp` 파일들이 있다. 배당/결과 역사 분석의 후보 자료지만, 2026년 8~9월 prediction snapshot_id와 연결된 선택 원장이 아니며 당시 AI 추천을 재현하지 않는다. 파일을 정식 이름으로 복원하거나 수집/빌드를 실행하지 않았다.
- `data/raw/recommendation_revisions.jsonl`은 262행이며 changed_at/current/previous/previous_revision/reason/revision 키를 가진다. 존재만 확인했으며 이를 262개 독립 경기 또는 공식 정산으로 간주하지 않았다.
- PR258의 D1/SP1 토너먼트는 해외 가격을 사용한 후향 대리실험이다. 원장 정산 결측을 대신 채우거나 실제 Proto ROI를 입증하는 자료가 아니다.

## 검증과 재사용 판단

기존 `tests/test_live_ledger_settlement.py`와 `tests/test_per_event_shadow.py`를 `-k 'not database'`로 실행했다. **18 passed, 1 deselected**. PROODD_DB_PATH를 자식 Python 프로세스에서만 제거하고 `-B`, `-p no:cacheprovider`를 사용했다. 합성 정산 테스트는 pytest 임시 JSONL에만 기록했고 DB 쓰기 테스트는 제외했다. 동결 가격 보존, 동일 정산 재처리, 공식 수정 append, 정확하지 않은 offer 거부, 변경 없는 갱신과 재시도 복구, paper abstention/공식 정산을 검사했다. 운영 DB/생성기/수집기/외부 API는 실행하지 않았다.

재사용할 순서는 기존 자료 감사 → `replay_ledger_results.py`의 **새 로컬 사본** 복원 → snapshot_id 기준 `ui_records/tally_prediction_records` 또는 목적에 맞는 기존 snapshot 감사다. 비교 실험은 이미 있는 `per_event_shadow`의 고정 관측·공식 정산·paired 집계를 참고하되, 무효 분모와 snapshot ledger와의 다른 데이터 구조를 보존해야 한다. 이 감사는 추가 구현이나 수집을 승인하지 않는다.

지금 확인한 1,326행 원장과 두 보관 피드만으로 exact-offer actual ROI를 계산할 수 없다는 점과, 9월6일 API 스냅샷의 부분 **모의 ROI는 이미 계산 가능**하다는 점을 구분해야 한다. 새 connector·정산 엔진·별도 ROI 공식을 다시 만드는 대신 기존 경로와 부족한 exact-offer 자료를 먼저 연결하는 것이 남은 판단이다. 실제 운영 전체의 보존 범위·배포 상태는 이번 로컬 감사의 범위 밖이다.

이 문서만 신규 작성했다. 다른 코드/문서/원본은 수정하지 않았고 commit/push/PR/merge/배포는 하지 않았다.
