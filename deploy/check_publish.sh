#!/bin/sh
# 산출물 자동 갱신이 실제로 도는지 확인한다.
# ⚠️ '수집 데이터 자동 갱신' 커밋이 30분마다 올라와도 그건 **원본 push** 일 뿐,
#    산출물(docs/data/*.json) 갱신과는 별개다. 실제로 2026-07-30 까지
#    산출물은 사람이 로컬에서 돌릴 때만 바뀌고 있었다.
APP=${1:-proto-odds-collector}
echo "== 배포된 산출물 생성 시각 =="
for f in today picks_v2 loss_grades combo today_combo; do
  printf "  %-12s " "$f"
  curl -s --max-time 15 "https://choigod1023.github.io/proto-odds-research/data/$f.json?cb=$RANDOM" \
    | python3 -c "import json,sys;print(str(json.load(sys.stdin).get('generated_at',''))[:16])" 2>/dev/null \
    || echo "읽기 실패"
done
echo "== 머신에서 PUBLISH 1단계 직접 실행 =="
flyctl ssh console -a "$APP" -C "sh -c 'cd /data/repo && python -u src/build_dataset.py >/dev/null 2>&1; echo rc=\$?'"
echo "   rc=1 이면 캐시 없음 → collect 단계를 확인할 것"
