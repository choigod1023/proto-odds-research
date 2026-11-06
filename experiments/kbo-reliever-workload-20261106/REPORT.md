# KBO 투수별 불펜 부하를 추가한 M0 비교

같은 상품과 배당을 확인한 2026년 30경기에서 보정 모델의 적중률은 56.67%에서 63.33%로, 1단위 모의 ROI는 −10.77%에서 +0.17%로 바뀌었다. 픽이 바뀐 두 경기가 이 차이를 만들었다. 핵심 불펜 연투가 있는 가격 표본 3경기에서는 두 모델이 모두 2경기를 맞혔고 ROI는 0.00%였다.

| 2026년 평가 표본 | 경기 수 | 기존 M0 적중 | 보정 모델 적중 | 기존 M0 모의 ROI | 보정 모델 모의 ROI |
| --- | ---: | ---: | ---: | ---: | ---: |
| 동일 상품·배당 | 30 | 17/30 (56.67%) | 19/30 (63.33%) | −10.77% | +0.17% |
| 동일 상품·배당, 핵심 불펜 연투 | 3 | 2/3 (66.67%) | 2/3 (66.67%) | 0.00% | 0.00% |
| 공식 점수·예측 연결 전체 | 57 | 32/57 (56.14%) | 35/57 (61.40%) | 미산출 | 미산출 |
| 공식 점수·예측 연결, 핵심 불펜 연투 | 6 | 3/6 (50.00%) | 4/6 (66.67%) | 미산출 | 미산출 |

KBO 공식 일정과 박스스코어를 개막 직후 25일씩 확보했다. 2025년 박스 92경기와 2026년 박스 97경기가 정상 파싱됐다. 롯데 투수 김태혁의 개명은 KBO 선수 페이지에서 ID를 확인했다. 이름이 같은 선수는 동일 경기의 이닝 아웃 수·실점·삼진으로 유일하게 일치할 때만 연결했다. 투구 수는 공식 박스스코어에서만 가져왔다. 2025년 공식 연기 공지와 일정 코드가 일치하는 8경기는 등판 이력에 넣지 않았고, 그 밖의 미확정 박스 1건은 결측으로 남겼다.

2025년 53경기로 M0 로그오즈에 더할 계수만 학습했다. 보정 모델에 들어간 값은 핵심 불펜 3인의 최근 3일 평균 투구 수, 전날과 그 전날 모두 등판한 비율, 마지막 등판까지 평균 경과일의 홈·원정 차이다. 핵심 불펜은 부하 계산보다 앞선 7일의 구원 등판 빈도로 정의했다. 당일 등판 기록은 피처에 쓰지 않았다. 가격 비교에서 공식 점수·예측 연결 표본 57경기 중 27경기는 과거 배당이나 정산용 라벨이 없어 빠졌다.

동일 가격 표본의 로그손실은 0.64619에서 0.64038로 낮아졌다. 연투 가격 표본에서는 0.71885에서 0.73127로 올라갔다. 전체 가격 표본은 2주, 연투 가격 표본은 1주뿐이다. 전체 모의 ROI 차이 +10.93%p는 두 경기에서 발생했고, 2주짜리 주 단위 재표집으로 안정적인 우위를 판단하기 어렵다. 배당과 당시 명단 수신이 인증되지 않은 회고 평가다.

공식 원천은 [KBO 경기 리뷰](https://www.koreabaseball.com/Schedule/GameCenter/ReviewNew.aspx), [김태혁 선수 정보](https://www.koreabaseball.com/Futures/Player/PitcherDetail.aspx?playerId=76430), [3월 30일 연기](https://www.koreabaseball.com/MediaNews/Notice/View.aspx?bdSe=11439), [4월 1~3일 연기](https://www.koreabaseball.com/MediaNews/Notice/View.aspx?bdSe=11443)다. 이름과 ID를 잇는 보관 자료는 KBO 동적 상태 연구의 커밋 7d3ee09128add31f2331b8dbc8ea2cfd0b4fdf0f에 있는 kbo/sources/pitching.jsonl.gz다. 모델·과거 가격의 해시와 배제 건수는 results.json에 있으며, 공식 일정·박스스코어의 원문 해시 및 박스 수신 시각은 official-workload.json.gz에 보존했다. 전체 원문과 HTTP 수신 기록은 이 작업 폴더의 .local-test/kbo-reliever-workload에 있다.

```powershell
python -X utf8 experiments/kbo-reliever-workload-20261106/run.py --source experiments/kbo-reliever-workload-20261106/official-workload.json.gz --output experiments/kbo-reliever-workload-20261106/results.json
python -m pytest -q experiments/kbo-reliever-workload-20261106/test_workload.py experiments/kbo-m0-followup-20261103/test_study.py
```
