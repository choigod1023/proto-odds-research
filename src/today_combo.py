"""오늘의 최적 조합 — 실제 발매 중인 배당으로 목표 배당별 조합을 짠다.

무엇을 최적화하나
-----------------
**이기는 조합은 없다.** 12개 검증이 그렇게 끝났다. 여기서 고르는 기준은 하나다 —
**같은 목표 배당을 만들 때 가장 덜 잃는 구성.**

두 단계로 고른다.

1. **어느 배당대를 몇 개 쓸 것인가** — `combo.py` 가 실측으로 푼 문제다.
   다리를 하나 더 붙일 때마다 마진이 한 번 더 물려 약 −6%p 씩 깎이므로,
   목표 배당은 다리 수가 아니라 다리당 배당으로 맞춘다.

2. **그 배당대 안에서 어느 경기를 고를 것인가** — 여기가 이 파일이다.
   같은 배당대면 과거 실측 ROI 가 **동일하다**(버킷 단위로 재니까).
   즉 "어느 팀이 이길 것 같은가" 로는 고를 근거가 없다 — 그게 이 프로젝트의 결론이다.
   남는 근거는 **경기별 환급률(overround) 차이** 하나뿐이다.
   프로토는 회차·경기마다 환급률이 86~89% 로 다르다. 같은 배당대라면
   **마진이 낮은 경기가 순수하게 유리하다.** 이건 예측이 아니라 산술이다.

규정 (https://www.sportstoto.co.kr/proto_rules.php · 2022-03 19회차 한경기구매 도입)
  · **한경기구매(단폴)**: '한경기' 로 지정된 경기만. 단위투표금액 1,000원
  · **조합구매**: 2~10경기. 단위투표금액 100원
  · 같은 경기의 다른 마켓끼리는 한 장에 못 담는다 → 모든 다리는 서로 다른 경기
  · 회차당 1인 10만원 · 투표권당 적중금 상한 1억원

⚠️ 어떤 경기가 '한경기구매' 로 지정됐는지는 우리 데이터에 없다. 단폴 칸은
   "지정돼 있다면 이게 최선" 이라는 뜻이고, 실제 가능 여부는 베트맨에서 확인해야 한다.

사용:
    python3 src/today_combo.py
    python3 src/today_combo.py --selftest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TODAY = ROOT / "docs" / "data" / "today.json"
GRADES = ROOT / "docs" / "data" / "loss_grades.json"
COMBO = ROOT / "docs" / "data" / "combo.json"
OUT = ROOT / "docs" / "data" / "today_combo.json"

# combo.py 가 쓰는 것과 같은 경계
BINS = [(1.0, 1.3), (1.3, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 5.0), (5.0, 999)]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]
BANNED = {"5.0+"}          # −33.5%. 어떤 목표에서도 쓰지 않는다.
TARGETS = [1.4, 2, 3, 5, 8, 12]


def bin_of(o: float) -> str | None:
    for (lo, hi), lab in zip(BINS, LABELS):
        if lo < o <= hi:
            return lab
    return None


def legs_today() -> list[dict]:
    """오늘 발매 중인 모든 선택지를 다리 후보로 편다.

    ⚠️ 프로토는 회차를 겹쳐서 발매한다. **같은 경기(game_no)가 두 회차에 서로 다른
       배당으로 걸린다.** 같은 결과에 더 받는 쪽이 순수하게 유리하므로 높은 배당만 남긴다.

       실측(2026-07-29): 두 회차에 겹친 마켓 60개 중 중앙값은 차이가 없고,
       평균으로는 오버라운드가 1.56%p 개선된다. 유리한 회차는 88:49 / 89:47 로
       균형이라 '한 회차가 낡은 것' 이 아니라 진짜 라인 변동이다.
       **차익거래(환급률 100% 초과)는 0개** — 양쪽을 다 사서 확정 수익을 낼 수는 없다.
    """
    d = json.loads(TODAY.read_text(encoding="utf-8"))
    out = []
    for rnd in d.get("rounds", []):
        for g in rnd.get("games", []):
            over = g.get("overround")
            if not over or not (1.0 < over <= 1.40):
                continue
            for s in g.get("selections", []):
                o = s.get("odds")
                b = bin_of(o) if o else None
                if not b or b in BANNED:
                    continue
                out.append({
                    "round": rnd.get("round"), "game_no": g.get("game_no"),
                    "date": g.get("date"), "league": g.get("league"),
                    "match": f"{g.get('home')} vs {g.get('away')}",
                    "market": g.get("market"), "market_label": g.get("market_label", ""),
                    "booking": g.get("booking_class"), "sel": s.get("name"),
                    "odds": o, "bin": b, "overround": round(over, 4),
                    "payout": g.get("payout"), "hist_roi": s.get("hist_roi"),
                })

    # 같은 (경기, 선택)이 여러 회차에 있으면 **배당이 높은 회차**만 남긴다.
    best: dict = {}
    for x in out:
        k = (x["game_no"], x["market"], str(x["market_label"]), x["sel"])
        cur = best.get(k)
        if cur is None or x["odds"] > cur["odds"]:
            if cur is not None:
                x = {**x, "beats": {"round": cur["round"], "odds": cur["odds"]}}
            best[k] = x
        elif x["odds"] < cur["odds"]:
            best[k] = {**cur, "beats": {"round": x["round"], "odds": x["odds"]}}
    return list(best.values())


def pick_legs(cands: list[dict], bins: list[str]) -> list[dict] | None:
    """요구된 배당대 구성대로 **서로 다른 경기**에서 다리를 고른다.

    같은 배당대 안에서는 실측 ROI 가 같으므로 **환급률이 높은(=마진이 낮은) 경기**를
    먼저 쓴다. 동률이면 배당이 높은 쪽 — 같은 마진이면 더 받는 게 낫다.
    """
    used: set = set()
    chosen = []
    for b in bins:
        pool = [c for c in cands if c["bin"] == b and c["game_no"] not in used]
        if not pool:
            return None
        pool.sort(key=lambda c: (c["overround"], -c["odds"]))
        best = pool[0]
        used.add(best["game_no"])
        chosen.append(best)
    return chosen


def build() -> dict:
    cands = legs_today()
    plans = json.loads(COMBO.read_text(encoding="utf-8"))["plans"]
    by_target = {p["target"]: p for p in plans}

    out_plans = []
    for t in TARGETS:
        p = by_target.get(t)
        if not p:
            continue
        legs = pick_legs(cands, p["best"]["bins"])
        if not legs:
            out_plans.append({"target": t, "ok": False,
                              "why": f"오늘 발매 중인 경기로는 {p['best']['bins']} 구성을 못 만든다"})
            continue
        odds = 1.0
        for c in legs:
            odds *= c["odds"]
        out_plans.append({
            "target": t, "ok": True, "legs": len(legs),
            "expected_roi": p["best"]["roi"],      # 실측 기반 기대 ROI
            "hit_est": p["best"]["hit"],
            "actual_odds": round(odds, 2),
            "picks": legs,
        })

    # 단폴 — 지정 경기라면 가장 덜 잃는 한 장
    solo = None
    lo = [c for c in cands if c["bin"] == "1.0-1.3"]
    if lo:
        lo.sort(key=lambda c: (c["overround"], -c["odds"]))
        solo = lo[0]

    grades = json.loads(GRADES.read_text(encoding="utf-8"))
    return {
        "generated_at": json.loads(TODAY.read_text(encoding="utf-8")).get("generated_at"),
        "basis": "같은 배당대 안에서는 실측 ROI 가 같다. 남는 근거는 경기별 환급률 차이뿐이다.",
        "n_candidates": len(cands),
        "n_better_round": sum(1 for c in cands if c.get("beats")),
        "solo": solo,
        "plans": out_plans,
        "odds_bins": grades["odds_bins"],
        "note": "이기는 조합이 아니다. 같은 목표 배당을 만들 때 덜 잃는 구성일 뿐이고, "
                "모든 칸의 기대값이 음수다. 단폴은 '한경기' 로 지정된 경기만 구매할 수 있다.",
    }


def _selftest() -> int:
    d = build()
    bad = []
    print("오늘의 조합 자기검사")
    print(f"  다리 후보 {d['n_candidates']:,}개")
    for p in d["plans"]:
        if not p.get("ok"):
            print(f"  ⏭ 목표 {p['target']}× — {p['why']}")
            continue
        gs = [c["game_no"] for c in p["picks"]]
        if len(set(gs)) != len(gs):
            bad.append(f"목표 {p['target']}× : 같은 경기를 두 번 썼다 {gs} — 규정 위반")
        if any(c["bin"] in BANNED for c in p["picks"]):
            bad.append(f"목표 {p['target']}× : 금지 배당대가 섞였다")
        if p["expected_roi"] >= 0:
            bad.append(f"목표 {p['target']}× : 기대 ROI 가 양수다 — 그런 구성은 없다")
        print(f"  ✅ 목표 {p['target']}× — {p['legs']}폴 · 실배당 {p['actual_odds']}× · "
              f"서로 다른 경기 {len(set(gs))}개")
    if d["solo"] and d["solo"]["bin"] in BANNED:
        bad.append("단폴이 금지 배당대다")
    if bad:
        print("\n🔴 " + "\n🔴 ".join(bad))
        return 1
    print("\n✅ 오늘의 조합 자기검사 통과")
    return 0


def main() -> int:
    d = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"다리 후보 {d['n_candidates']:,}개 (5.0+ 제외)\n")
    if d["solo"]:
        s = d["solo"]
        print(f"[단폴] {s['league']} {s['match']} · {s['market']} {s['sel']} @ {s['odds']} "
              f"(환급률 {s['payout']}%)  ← '한경기' 지정 경기만 가능")
    for p in d["plans"]:
        if not p.get("ok"):
            print(f"\n[목표 {p['target']}×] {p['why']}")
            continue
        print(f"\n[목표 {p['target']}×] {p['legs']}폴 · 실배당 {p['actual_odds']}× · "
              f"적중 {p['hit_est']*100:.1f}% · 기대 {p['expected_roi']*100:+.1f}%")
        for c in p["picks"]:
            print(f"   · {c['date']} {c['league']:<8} {c['match']:<22} "
                  f"{c['market']}{(' ' + c['market_label']) if c['market_label'] else ''} "
                  f"{c['sel']} @ {c['odds']}  (환급 {c['payout']}%)"
                  + (f"  ← {c['round']}회차. {c['beats']['round']}회차는 @{c['beats']['odds']}"
                     if c.get("beats") else ""))
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
