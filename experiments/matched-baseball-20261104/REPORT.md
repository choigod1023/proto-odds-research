# KBO 원문 투표·선수모델 교집합 회고 진단

독립 경기 197개, 가격행 197개, 모델 revision-경기 985개. 엄격 사전 공통 경기 0개.

2026 실제 게시 주장과 사후 확정 명단 oracle을 결합한 진단이다. 원문 최초 관측·수정 이력, 선수명단 수신 및 배당 수신 시각이 경기 전임을 입증하지 못한다. 이미 본 기간이며 블라인드 검증이 아니다. 2025 원문 KBO 행은 이 입력에 없다. 단위 단폴 수익이며 월 손실 제한 원장이 아니다.

Exact official game ID and identical archived home/away prices; full-game moneyline only; one vote per source+author, conflicting authors excluded; tied vote excludes game; model/market/hybrid ties no bet; draws refund; 1 unit single bets; 9:1 means 0.9 model home probability + 0.1 author vote home fraction. ROI includes refunds in stake denominator; hit rate excludes refunds. Source-claim and retrospective lineup oracle only, not blind or real-time.

| 전략 | 베팅 N | 적중/확정 N | 적중률 | ROI | 순손익 단위 |
|---|---:|---:|---:|---:|---:|
| market | 197 | 116/197 | 58.88% | -10.75% | -21.17 |
| vote | 197 | 112/197 | 56.85% | -8.61% | -16.97 |
| kbo-actual-training-2026-M0_actual | 197 | 120/197 | 60.91% | -5.06% | -9.96 |
| kbo-actual-training-2026-M2_actual | 197 | 106/197 | 53.81% | -14.87% | -29.29 |
| kbo-player-effects-v2-2026-M0 | 197 | 116/197 | 58.88% | -8.18% | -16.12 |
| kbo-player-effects-v2-2026-M1 | 197 | 110/197 | 55.84% | -9.31% | -18.35 |
| kbo-player-effects-v2-2026-M2 | 197 | 108/197 | 54.82% | -11.44% | -22.53 |
| kbo-actual-training-2026-M0_actual:9model1vote | 197 | 111/197 | 56.35% | -10.90% | -21.48 |
| kbo-actual-training-2026-M2_actual:9model1vote | 197 | 113/197 | 57.36% | -8.70% | -17.14 |
| kbo-player-effects-v2-2026-M0:9model1vote | 197 | 112/197 | 56.85% | -9.74% | -19.18 |
| kbo-player-effects-v2-2026-M1:9model1vote | 197 | 110/197 | 55.84% | -10.25% | -20.20 |
| kbo-player-effects-v2-2026-M2:9model1vote | 197 | 112/197 | 56.85% | -9.76% | -19.22 |

원문 한국 후보 전체 294행이 joined 장부 294행과 일치한다. 197경기 중 190경기는 작성자 1명이며 진정한 다수 작성자 표결로 해석할 수 없다. 최초 관측이 경기 전인 원문은 0경기이고 수정·결과 누출 여부는 확인되지 않는다. 서로 다른 두 M0 선택은 16경기에서 다르다. 월별 동일 교집합과 사유별 행 수는 summary.json, 경기별 입력·선택·가격·결과·손익은 rows.json에 보존했다. actual-training M0와 projected-training M0는 별도 고정 아티팩트다. actual-training M1은 원본에 없다. 가격은 원문에 실린 당시 가격이 아니라 두 장부가 같은 회고 보관 가격임을 확인한 것이다. 출처의 게시 날짜는 사전 수신이나 수정 이력의 증거가 아니다.
