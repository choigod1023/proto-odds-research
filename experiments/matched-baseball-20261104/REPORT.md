# KBO 원문 투표·선수모델 교집합 회고 진단

발췌문 상품 재검증 뒤 독립 경기 103개, 가격행 103개, 모델 revision-경기 515개. 기간 2026-03-31T18:30:00+09:00~2026-07-23T18:30:00+09:00. 엄격 사전 공통 경기 0개.

실제 게시 주장과 사후 확정 명단 oracle을 결합한 진단이다. 상품이 섞였거나 선택한 팀의 명시적 승리 픽을 확인할 수 없는 발췌문은 제외한다. 단순 팀 승리 또는 승패(ML)는 다른 상품·기간 표식이 없을 때만 전 경기 승패로 가정하며, 원문이 연장 포함을 명시했다는 뜻은 아니다. 상세와 제외 사유는 vote-excerpt-audit.json에 남긴다. 각 선택의 게시 주장 시각은 경기 전이지만 최초 관측은 경기 후이며 미래에 기록한 본문을 당시 수신으로 승격하지 않았다. 원문 최초 관측·수정 이력, 선수명단 수신 및 배당 수신 시각이 경기 전임을 입증하지 못한다. 이미 본 기간이며 블라인드 검증이 아니다. 2025 원문 KBO 행은 이 입력에 없다. 단위 단폴 수익이며 월 손실 제한 원장이 아니다.

Explicit selected-team win with no period, handicap or totals markers in the stored excerpt; ordinary win/ML is assumed full game and overtime is not explicitly established; no fallback. Exact official game ID and identical archived home/away prices; full-game moneyline only; one vote per source+author, conflicting authors excluded; tied vote excludes game; model/market/hybrid ties no bet; draws refund; 1 unit single bets; Fixed market90_vote10_multiplicative is 0.9 multiplicative normalized inverse-price market home probability (not a claim of production Shin probability) + 0.1 author vote home fraction; separately named player90_vote10 uses each model probability. ROI includes refunds in stake denominator; hit rate excludes refunds. Source-claim and retrospective lineup oracle only, not blind or real-time.

| 전략 | 베팅 N | 적중/확정 N | 적중률 | ROI | 순손익 단위 |
|---|---:|---:|---:|---:|---:|
| market | 103 | 65/103 | 63.11% | -4.03% | -4.15 |
| vote | 103 | 62/103 | 60.19% | -3.94% | -4.06 |
| market90_vote10_multiplicative | 103 | 63/103 | 61.17% | -5.75% | -5.92 |
| kbo-actual-training-2026-M0_actual | 103 | 65/103 | 63.11% | -0.89% | -0.92 |
| kbo-actual-training-2026-M2_actual | 103 | 61/103 | 59.22% | -6.68% | -6.88 |
| kbo-player-effects-v2-2026-M0 | 103 | 62/103 | 60.19% | -5.95% | -6.13 |
| kbo-player-effects-v2-2026-M1 | 103 | 67/103 | 65.05% | 7.64% | 7.87 |
| kbo-player-effects-v2-2026-M2 | 103 | 61/103 | 59.22% | -4.91% | -5.06 |
| kbo-actual-training-2026-M0_actual:player90_vote10 | 103 | 64/103 | 62.14% | -1.59% | -1.64 |
| kbo-actual-training-2026-M2_actual:player90_vote10 | 103 | 64/103 | 62.14% | -1.79% | -1.84 |
| kbo-player-effects-v2-2026-M0:player90_vote10 | 103 | 63/103 | 61.17% | -3.10% | -3.19 |
| kbo-player-effects-v2-2026-M1:player90_vote10 | 103 | 66/103 | 64.08% | 4.16% | 4.28 |
| kbo-player-effects-v2-2026-M2:player90_vote10 | 103 | 64/103 | 62.14% | -2.06% | -2.12 |

## 고정 시장 90%·투표 10% 월별

| 월 | 경기 | 적중/확정 | 적중률 | ROI | 순손익 |
|---|---:|---:|---:|---:|---:|
| 2026-03 | 3 | 1/3 | 33.33% | -54.67% | -1.64 |
| 2026-04 | 28 | 19/28 | 67.86% | 4.68% | 1.31 |
| 2026-05 | 1 | 0/1 | 0.00% | -100.00% | -1.00 |
| 2026-06 | 30 | 20/30 | 66.67% | 3.63% | 1.09 |
| 2026-07 | 41 | 23/41 | 56.10% | -13.85% | -5.68 |

원문 한국 후보에는 KBO 294행이 있다. 작성자 수와 동률 제외 건수는 summary.json을 확인한다. 게시 주장과 사후 수신 자료는 실제 당시 수신의 증거가 아니며 수정·결과 누출 여부는 확인되지 않는다. 누락 239개 공식 경기 ID·일정 시각은 기존 원문 본문 해시를 검증하여 복구했다. 추가 6개 원문 후보 경기의 배당 연결은 day/team 장부이므로 exact-ID 비교에 편입하지 않았다. 상세는 recovered-official-metadata.json에 있다. 별도로 확인한 해외 원문에는 2025 MLB 후보 3행이 있으나 사후 관측이며 KBO 모델과 섞지 않았다. 해당 자료와 한국 후보에서 2025 KBO 원문은 찾지 못했고 다른 아카이브의 부재를 뜻하지 않는다 (archive-2025-audit.json). 월별 동일 교집합과 사유별 행 수는 summary.json, 경기별 입력·선택·가격·결과·손익은 rows.json에 보존했다. actual-training M0와 projected-training M0는 별도 고정 아티팩트다. actual-training M1은 원본에 없다. 가격은 원문에 실린 당시 가격이 아니라 두 장부가 같은 회고 보관 가격임을 확인한 것이다. 출처의 게시 날짜는 사전 수신이나 수정 이력의 증거가 아니다.
