# 2026년 K리그 경기 전 수동 검증

132회차의 14경기는 [원래 동결 파일](prospective-260132.json)로만 정산한다. 133회차부터는 같은 승무패 배당으로 정규화 시장 확률과 [고정 Dirichlet 보정](dirichlet-model.json)을 비교한다. 과거 세 시장 표본을 확대하던 `forward_cycle.py` 대신 아래 명령을 직접 실행한다.

```powershell
$d = 'experiments/kleague-market-information-20261106'
$state = '.local-test/dirichlet-forward'
python -X utf8 "$d/prospective_dirichlet.py" capture --round 133 --state $state
python -X utf8 "$d/prospective_dirichlet.py" settle --state $state
```

발매 중 승무패 원본을 한 번 조회한다. 구매 가능한 정규시간 3방향 상품이 한 개이고, 공식 일정과 홈·원정·시작 시각이 일치하는 경기만 고른다. 예측 발행 시점까지 판매 마감과 경기 시작이 지나지 않아야 한다. 첫 동결이 있는 회차는 덮어쓰지 않고, 다른 회차에서 다시 발견한 동일 경기와 최초 14경기는 제외한다. 발매 응답이나 새 적격 경기가 없으면 동결하지 않는다. 이후 회차도 같은 상태 폴더를 쓴다.

공식 정규시간 종료와 점수만 정산에 사용한다. 정산 증거는 매 실행마다 별도 폴더에 남긴다. 일정이 달라져 정산할 수 없는 새 경기는 대기 상태로 둔다. 묶음 전체가 정산됐을 때에만 원고의 해당 결과에 반영한다. 결과에는 두 모형의 로그손실과 차이를 표시하고, 새 표본에서는 같은 배당으로 최고 확률 픽의 적중과 단위 ROI도 기록한다.
