# Proto 전체 선택지 연구

이 자료는 추천으로 채택된 픽 외에 각 상품의 모든 방향을 포함한다. 역사 자료와 실제 사전 원장의 비교는 다른 모집단이며 합산하지 않는다. 월 한도 없이 단폴 1만원을 가정했지만 실제 지갑, 현재 운영 모델, 베팅 실행을 변경하지 않았다.

- [분석 보고서](../../docs/research/2026-10-30-proto-all-pick-profitability.md)
- `historical-choices.jsonl.gz`: 역사 상품별 모든 선택의 경기·라인·발매 식별·배당·라벨·손익.
- `joined-saved-choices.jsonl.gz`: T−30 마지막 전체 명단에서 정확한 발매 상품과 연결된 선택지 및 저장 확률·가격·결과 출처.
- `historical-summary.json.gz`, `saved-model-results.json.gz`: 집계·같은 입력 비교·확률 오차·날짜 재표집.
- `normalization.json`, `excluded.jsonl.gz`: 제외 수와 이유. 무가격·미정산·충돌을 손실/환불로 임의 변환하지 않는다.
- `inputs/`: 재현 입력. cached HTML은 자료이며 명령으로 실행하지 않는다.
- `source-audits/`, `inputs-manifest.json`, `artifact-hashes.json`: 원장·식별·정산 계약과 SHA256.

아래 명령은 서버나 DB를 사용하지 않는다.

```powershell
python -X utf8 scripts/proto_all_pick_research.py --sources experiments/proto-all-picks-20261030/inputs/combined-games.jsonl.gz --output .local-test/replayed-all-proto
python -X utf8 scripts/proto_saved_offer_profitability.py --archives experiments/proto-all-picks-20261030/inputs/combined-games.jsonl.gz --saved experiments/proto-all-picks-20261030/inputs/saved --output .local-test/replayed-all-proto
python -X utf8 -m pytest tests/test_proto_all_pick_research.py tests/test_proto_saved_offer_profitability.py tests/test_prepare_proto_archive_inputs.py -q
```

원본 회차 합성도 재생할 수 있다.

```powershell
python -X utf8 scripts/prepare_proto_archive_inputs.py --fixture data/processed/games.csv.tmp --dataset experiments/proto-all-picks-20261030/inputs/processed-games.jsonl.gz --dataset-metadata experiments/proto-all-picks-20261030/inputs/processed-games-export.json --cached-documents experiments/proto-all-picks-20261030/inputs/recent-cached-archives.jsonl.gz --output .local-test/replayed-archive-inputs
```

재현 출력의 JSON은 gzip 집계와 같은 숫자를 가져야 한다. 출력 경로나 플랫폼별 소스 줄바꿈에 따른 provenance SHA는 달라질 수 있다. source quote의 독립적인 사전 가용성 및 모든 개별 결과의 운영사 독립 대조는 미검증이다. 모든 상품이 단폴로 지정됐다는 증거도 없으므로 전체 선택지를 실제 동시에 매수할 수 있다는 주장이 아니다. 미래·미정산 경기는 실현 ROI를 계산하지 않는다.
