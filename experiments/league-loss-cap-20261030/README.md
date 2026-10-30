# 월 손실 한도 연구 스냅샷

`records.jsonl.gz`는 각 에이전트가 저장 자료를 검산한 결과를 고정한 입력이다. 모델×경기 행이며 서로 겹치는 데이터셋·모델 별칭을 포함하므로 독립 경기 수로 사용하지 않는다. 원본 해시, 모델 정의와 원천 시각의 한계는 `source-audits/`에 있다.

재현:

```powershell
python -X utf8 scripts/league_loss_cap_research.py --inputs experiments/league-loss-cap-20261030/records.jsonl.gz --output .local-test/reproduced-loss-cap.json
python -X utf8 -m pytest tests/test_league_loss_cap_research.py -q
```

실행 시각과 독립적인 결과를 반환한다. 모든 연구 한도·비한도 지갑은 동일 단폴 10,000원이며 운영 지갑과 연결하지 않는다. 실제 정산 시각은 없으므로 다음 날/이틀 뒤 가정의 민감도를 별도로 보존했다. `results.json`의 414집합은 코호트별 진단이며 전체 최고값으로 모델을 채택하지 않는다.

`paper-journal.json.gz`는 별도로 연결된 실제 **모의** 지갑의 읽기 전용 스냅샷이다. 모든 거절된 기회를 포함하지 않으므로 이 파일에서 실제 무제한 반사실 ROI를 계산하지 않았다. 원장 스냅샷에 포함된 여러 비교 계정의 손익 합계는 단일 전략의 수익이 아니다. `actual-paper-budget.json`은 이 자료의 예산 상태를 설명한다.

MLB/KBO 동결 후보의 이전 연구·실행 경로는 [PR #416](https://github.com/choigod1023/proto-odds-research/pull/416)에 있다. 현재 운영 정책을 과거 모든 경기에 완벽하게 재생한 결과로 부르지 않는다. 연구 보고서는 [리그별 월 한도 비교](../../docs/research/2026-10-30-league-loss-cap.md)를 참고한다.
