"""기존 ``picks_v2.json``에 최신 경기 전 근거만 덧붙인다.

프로토 원천이 일시적으로 발매 중 회차 0개를 반환할 때 전체 생성기를 돌리면 기존
예정 경기를 잃을 수 있다. 이 도구는 대진·배당은 건드리지 않고, 같은 날짜·양 팀에
매칭되는 무료 컨텍스트와 해설만 원자적으로 갱신한다.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import commentary_llm
from recommendation_context import ContextStore, narrative

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "docs" / "data" / "picks_v2.json"


def enrich(doc: dict, store: ContextStore) -> tuple[dict, int]:
    matched = 0
    for game in [*(doc.get("live") or []), *(doc.get("past") or [])]:
        evidence = store.evidence_for(game)
        if not evidence:
            continue
        matched += 1
        extra = narrative(evidence)
        # 같은 source event를 이미 붙였다면 LLM을 다시 부르지 않는다. 기본 프리뷰와
        # 분리 저장해 새 관측이 와도 이전 추가 문장이 중복될 여지를 없앤다.
        old = game.get("경기근거") or {}
        changed = old != evidence
        if changed or not game.get("근거해설"):
            game["근거해설"] = commentary_llm.polish(
                extra, protected_terms=evidence.get("protected_entities", []))
            game["근거해설방식"] = commentary_llm.last_status()
        game["경기근거"] = evidence
    doc["context_enriched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["context_games"] = matched
    return doc, matched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        # 실제 파일을 쓰지 않는 최소 회귀검사는 recommendation_context가 담당한다.
        assert narrative({"internal": [{"text": "선발 확인"}], "external": [], "crowd": []})
        print("✅ enrich_picks_context selftest 통과 (근거 문장 생성)")
        return 0
    doc = json.loads(PICKS.read_text(encoding="utf-8"))
    year = int(str(doc.get("generated_at") or datetime.now().year)[:4])
    result, matched = enrich(doc, ContextStore(year=year))
    tmp = PICKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(PICKS)
    commentary_llm.flush()
    print(json.dumps({"games": matched, "output": str(PICKS)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
