# 픽스터 원문 수집과 동일 상품 정산 비교

저장한 경기 전 원문으로 [#611](https://github.com/choigod1023/proto-odds-research/pull/611)의 작성자별 최초 투표와 교차 도메인 재게시 제거 투표를 재생합니다. 동일 프로토 상품과 배당 벡터에서 두 방식의 선택을 비교합니다. 한 단위씩 걸었다고 가정하는 상품 단위 연구이며, 2폴 조합이나 운영 채택을 평가하는 실험과 범위가 다릅니다.

## 실행

저장소 루트에서 실행합니다. 로컬 Python 환경에 `requests`, `beautifulsoup4`, `lxml`이 필요합니다. 운영자가 알고 있는 G101 회차를 지정합니다. 아래 회차는 실행 예시입니다.

```powershell
$tipsterEvidence = "C:/Users/user/Documents/ChatGPT/sportstoto-research-state/tipster-forward-v1"
python -m scripts.prospective_tipster_pairs capture --round 260132 --evidence $tipsterEvidence
python -m scripts.prospective_tipster_pairs evaluate --round 260132 --evidence $tipsterEvidence
```

`capture`를 호출할 때마다 `paired-v1/<회차>/<고유 실행 ID>`에 새 증거를 보관합니다. 같은 상태 경로의 수집 호출은 순차 실행합니다. Pick79 축구 목록과 Goli 분석 목록에서 각각 상단 기사 두 건까지 살펴봅니다. 리디렉션을 포함해 기사 쪽 최대 8회, Betman은 지정 회차 상세 1회입니다. Betman 실패 시 수집한 기사가 남고, 후속 실행에서 배당을 확보하면 기사 수신 시각과 대조합니다. 기사 제공처가 403/429를 반환하면 상태 루트에 중지 표식을 남깁니다. Betman에는 기존 수집기의 403/429 중지 규칙을 적용합니다.

경기 후에는 같은 회차를 `--article-requests 0`으로 한 번 호출해 공식 결과를 저장한 뒤 `evaluate`를 실행합니다. 평가 명령은 JSON을 표준 출력으로 내보냅니다. 기존 수집물도 읽을 수 있습니다.

```powershell
python -m scripts.prospective_tipster_pairs capture --round 260132 --evidence $tipsterEvidence --article-requests 0
python -m scripts.prospective_tipster_pairs evaluate --round 260132 --evidence $tipsterEvidence --article-root "$tipsterEvidence/20261106T1135439951/article-evidence"
```

기존 Betman 수집물은 `--price-root`에 `round-<회차>.json`과 gzip 원문이 함께 있는 개별 실행 폴더를 지정합니다. `--article-root`와 `--price-root`는 반복해서 지정할 수 있습니다. `--evaluated-at`에 시간대가 있는 ISO 시각을 지정하면 당시까지 보관된 응답을 재생합니다. Python에서 직접 사용하려면 `from scripts.prospective_tipster_pairs import capture`, `from src.prospective_tipster_pairs import evaluate`로 가져옵니다.

운영 픽 비교는 `evaluate --ledger <로컬 원장 JSONL 사본>`을 사용합니다. 체인이 검증된 원장에서 가격 시각까지 저장된 최신 대표 픽을 선택하고, 경기·회차·게임번호·선택 ID·오퍼 ID·승무패 배당 세 개가 모두 맞을 때 별도 짝으로 집계합니다. 원장 입력이 없거나 대표 픽 또는 배당을 확인할 수 없으면 해당 비교는 보류합니다.

## 적격 조건과 보고서

- 상품은 G101의 축구 일반 승무패로 제한합니다. Betman의 상품 ID, 상품명, 승·무·패 선택지, 기준점, 정규 경기 여부를 확인합니다. 적용 시간은 후반 종료까지이며 연장전과 승부차기는 제외합니다. 이 조합을 입증할 수 없는 상품은 정산 규칙을 추정하지 않고 보류합니다. 리그 표기는 공식 응답의 약칭과 기존 픽스터 별칭만 사용합니다. [스포츠토토 공식 상품 안내](https://www.sportstoto.co.kr/proto_intro), [고정배당률 투표권 세부규칙](https://www.sportstoto.co.kr/terms_allocation.php)
- 가격은 회차와 개별 상품이 발매 중인 응답에서 받습니다. 수신 완료는 두 마감 시각보다 이르고 킥오프 24시간 전부터 30분 전 미만 구간이어야 합니다. 기사 역시 배당 수신 완료 이전에 원문 수신을 마쳐야 합니다. 원문 해시가 맞고 두 도메인의 본문을 검증할 수 있는 최초의 적격 가격을 사용합니다. 재게시가 없어도 두 방식이 선택했다면 짝에 포함됩니다. 한 방식이 보류한 표본은 별도로 셉니다.
- 정산은 킥오프 후 받은 동일 게임번호와 동일 경기의 최신 Betman 응답에서 확정 코드와 상품 점수를 대조합니다. 공식 결과와 점수가 다르거나 정산이 확인되지 않으면 보류합니다. 확인된 무효는 수익률 계산에 환불로 포함하고 정확도 분모에서는 뺍니다.
- 보고서는 짝을 이룬 상품 수, 보류와 무효, 방식별 적중률·순손익·ROI, 두 방식의 차이를 반환합니다. 가상 ROI는 상품당 한 단위와 저장된 배당을 기준으로 합니다. 실제 구매 체결, 세금, 조합 또는 구매 제한은 이 계산에 포함되지 않습니다. 원문과 해시는 로컬 파일 간 일관성을 확인하며 수신 시계는 로컬 환경에 의존합니다.
- 260132 수동 표본을 오프라인으로 읽어 원문 1건을 확인했습니다. 해당 실행의 Betman 요청은 TLS 연결 중 종료되어 공식 가격 응답이 없습니다. 적격 비교 쌍은 0건이고 정확도와 ROI는 미산출입니다.
