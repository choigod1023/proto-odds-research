# KBO 2024·2025 회고 가격과 결과 연결

상태: `ready_for_model_worker_frozen_inputs_only`. 공통 계약 `0fe05367fc85e71f2df2784c6ddf50fb017bcba2`의 실제 명단 대상에 기존 원본 가격과 결과를 연결했다. 새 스포츠 데이터 요청, 모델 성능 계산, 튜닝, 운영 변경은 하지 않았다.

- `prices-2024.jsonl.gz`: 728경기 중 가격 확인 714경기. `labels-2024.jsonl.gz`는 공식 일정과 점수가 일치한 728경기 전부를 담고, `exclusions-2024.jsonl.gz`는 가격이 없던 14경기를 기록한다.
- `prices-2025.jsonl.gz`: 731경기 중 가격 확인 676경기. `labels-2025.jsonl.gz`는 공식 일정과 점수가 일치한 731경기 전부를 담고, `exclusions-2025.jsonl.gz`는 가격이 없던 55경기를 기록한다. 원래 실제 타순 미확정으로 제외된 5경기는 731경기 모집단에 포함하지 않는다.
- `manifest.json`: 원천 Git commit 및 SHA-256, 원본 CSV SHA, 집합별 분모·제외 사유, 보조 대조 결과, 출력 해시와 제한을 기록한다. `handoff.json`은 소비할 경로와 해시를 정리한다.

**가격이 있는 표본에는 무승부가 없다.** 전체 결과에는 2024년 무승부 9경기와 2025년 무승부 22경기가 있으나 모두 원본 `market` 결측이다. 원본 회고 파일의 가격 연결 과정은 void 행을 제외하고 최종 점수 일치도 요구했다. 즉 가격 표본은 사후 결과를 이용한 선택 조건의 영향을 받는다. 따라서 가격 보유 표본만 계산한 적중·ROI는 전체 경기의 무승부와 환급을 대표하지 못하며, 정상적인 전체 경기 수익률로 해석하면 안 된다. 다른 원천의 가격을 채워 넣지 않았다.

경기 ID·날짜·홈/원정 팀·더블헤더 순번·포스트시즌 별칭을 확인하고 원본 경기 기록, 기존 결과 라벨과 공식 일정의 완료 상태·점수를 비교했다. 가격이 있는 행은 고정 원본 CSV의 유일한 `full_game_2way` 행에서 팀·시각·점수·양쪽 배당·void 여부까지 재대조했다. 2024년 별도 보관 검증 자료와 양쪽 가격을 비교할 수 있는 712경기는 모두 일치하며 이 자료로 표본을 추가하거나 가격을 대체하지 않았다.

`price_source.quote_tier`는 보관된 원시 가격을 뜻한다. 1,390건 모두 `odds_observed_at=null`, 당시 가격 수신 증명 없음, 체결 가능성 검증 없음이다. `result_evidence`의 공식 일정 수신 시각은 현재 원문 수집 시각이며 과거 수신 증명이 아니다. 결과 라벨은 이전 원본 필드를 그대로 보존하므로 원본 `status_basis` 표현도 유지한다. 현재 공식 일정의 독립 점수 대조 결과는 manifest와 가격 행의 출처에 별도로 기록했다.

상위 2024년 공식 명단 source의 원문 수집 과정에서는 두 프로세스가 겹쳐 요청 간격 1.5초 미만이 128건 발생했다. 생산자가 중단·잠금 후 기록을 검증했으며, 해당 운영상 제한을 manifest에 보존한다.

재생성: `python experiments/kbo-m0-followup-20261103/data/build_inputs.py`. 검증: `python experiments/kbo-m0-followup-20261103/data/test_build_inputs.py` 및 `python experiments/kbo-m0-followup-20261103/data/audit_inputs.py`. 재생성에는 manifest의 고정된 로컬 checkout과 SHA가 일치하는 기존 원본 CSV가 필요하다.
