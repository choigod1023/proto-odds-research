# 다음 KBO 경기 사전 검증 기록

2026-11-04 23:41 KST에 `OfficialClient.schedule`로 11월 4일부터 6일까지 3회 요청했다. 공식 응답에는 11월 6일 18:30 KST KT(원정) 대 삼성(홈), 경기 ID `20261106KTSS0`가 예정 상태로 나타났다. 응답의 선발투수와 타순은 미발표다. 공식 응답 원문과 수신 기록은 `official-state/`에 별도 보관했다. 일정은 변경될 수 있다.

`verification-status.json`은 같은 시점 이후 실제 로컬 원장을 읽은 결과다. 공식 일정 관측만 완료이고 발표·실제 M0 캡처·운영 투표/시장 동배당 비교·공식 정산은 미완료다. 부모 실행 기록의 M1 상태는 `waiting_for_eligible_inputs`, 캡처와 정산은 0이다. M1 원장 경로와 공통 원장은 아직 없다. 기존 M0 원장에는 이 경기의 캡처가 없다. 샘플 테스트는 실제 경기 예측이 아니다.

공통 원장의 `paired_capture`/`paired_settlement` 스키마와 원천 검증은 별도 작업에서 확정 중이다. 현재 검사기는 공통 파일의 해시 체인만 읽고, 형식이 있다는 이유로 비교나 정산을 승인하지 않는다. 향후 같은 경기·같은 상품·가격·공식 결과와 세 선택의 출처를 검증하도록 명세 확정 후 연결해야 한다. 별도의 발표만 있는 경우에도 이 검사기는 검증된 M1 캡처가 생길 때까지 발표 단계를 보수적으로 미완료로 표시한다.

실행 예시 (경로는 해당 설치에 맞게 지정):

```powershell
python scripts/check_forward_verification.py --game-id 20261106KTSS0 --season 2026 --since 2026-11-04T14:41:13+00:00 --schedule-check experiments/forward-next-check-20261104/schedule-check.json --schedule-state experiments/forward-next-check-20261104/official-state --m1-ledger C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-actual-forward-v1/predictions.jsonl --legacy-ledger C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1/paired-ledger.jsonl --common-ledger C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-actual-forward-v1/common-ledger.jsonl --archive-root C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1/official --price-archive-root C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1/prices/price-evidence --parent-report C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1/last-run.json
```

명령은 네트워크 요청과 원장 쓰기를 하지 않는다. 목표는 실제 경기 1건의 경로 검증이며 200건 성과 인증이나 실제 베팅 수익 검증이 아니다.
