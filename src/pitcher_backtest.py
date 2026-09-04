"""KBO 선발 xFIP λ 보정 백테스트 — 팀 λ vs 팀 λ+xFIP, 대 시장.

목적
----
`pitcher_form.apply_xfip_lambda_adjust` 가 만드는 보정(generate_v2 가 KBO λ에 적용)이 **검증 구간에서 Brier 를
개선하는가, ROI 를 악화시키지 않는가**를 잰다. 채택 기준(사전등록):

    검증 Brier 개선  AND  ROI 비악화

데이터
------
1순위: `data/processed/games.csv` · `bets.csv` (build_dataset 산출물). 있으면 전 기간.
없으면: 추적 중인 `data/raw/snapshots/odds_timeseries_*.csv` (2026-07-26~).
       — 짧은 창이라 방향성만 본다. `박빙시장대조.md` 처럼 표본이 쌓여야 판정력이 는다.

선발·팀 최근 득실은 `data/raw/detail/kbo_baseball_2023_2026.json` 박스스코어에서 만든다.
모든 값은 워크포워드다.

사용:
    python src/pitcher_backtest.py           # 스냅샷 창
    python src/pitcher_backtest.py --k 0.35  # 특정 계수만
"""
from __future__ import annotations

import csv
import glob
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import multiplicative                        # noqa: E402
from matches import _away, _home, clean_team            # noqa: E402
from pitcher_form import (apply_xfip_lambda_adjust,      # noqa: E402
                          StarterForm, load_starter_boxscores)
from score_dist import joint, p_win                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "raw" / "snapshots"
PROC = ROOT / "data" / "processed"
TEAM_MAP = PROC / "team_map.json"
K_GRID = (0.0, 0.15, 0.25, 0.35, 0.5, 0.75)
TEAM_WINDOW = 15          # 팀 최근 득실 롤링 경기 수
SEED = 42


def _team_map() -> dict[str, str]:
    import json
    try:
        return json.loads(TEAM_MAP.read_text(encoding="utf-8")).get("KBO", {})
    except (OSError, ValueError):
        return {}


def _snapshot_games() -> list[dict]:
    """스냅샷 CSV에서 KBO 승패 2-way 정산 경기를 경기당 1건(최신 관측)으로."""
    tmap = _team_map()
    latest: dict[tuple, dict] = {}
    for path in sorted(glob.glob(str(SNAP / "odds_timeseries_*.csv"))):
        with open(path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if (row.get("league") != "KBO" or row.get("market_family") != "승패"
                        or row.get("n_way") != "2"):
                    continue
                if row.get("result") not in ("홈승", "홈패"):
                    continue
                home_team, home_score = _home(row.get("home"))
                away_score, away_team = _away(row.get("away"))
                if not home_team or not away_team:
                    continue
                try:
                    o_home, o_away = (float(v) for v in row["odds"].split(","))
                except (ValueError, KeyError):
                    continue
                if not (o_home > 1 and o_away > 1):
                    continue
                mm = str(row.get("date_text") or "")[:5].replace(".", "-")
                day = f"{row.get('year')}-{mm}"
                key = (row.get("year"), row.get("round"), row.get("game_no"))
                latest[key] = {
                    "date": day, "ts": row.get("ts") or "",
                    "home": clean_team(tmap.get(home_team, home_team)),
                    "away": clean_team(tmap.get(away_team, away_team)),
                    "home_score": int(home_score), "away_score": int(away_score),
                    "o_home": o_home, "o_away": o_away,
                    "y": 1.0 if row["result"] == "홈승" else 0.0,
                }
    rows = sorted(latest.values(), key=lambda r: (r["date"], r["ts"]))
    return rows


def _full_games() -> list[dict]:
    """build_dataset 산출물이 있으면 전 기간 KBO 승패 경기를 쓴다."""
    if not (PROC / "games.csv").exists():
        return []
    try:
        import pandas as pd
        from matches import load_matches
        from model_v2 import attach_odds
    except Exception:                                    # noqa: BLE001
        return []
    tmap = _team_map()
    m = load_matches(sports=("bs",))
    m = m[(m["league"] == "KBO") & (m["outcome"] != 0.5)].copy()
    try:
        m = attach_odds(m)
    except Exception:                                    # noqa: BLE001
        return []
    rows = []
    for r in m.itertuples():
        try:
            oh, oa = float(r.o_home), float(r.o_away)
        except (AttributeError, TypeError, ValueError):
            continue
        if not (oh > 1 and oa > 1):
            continue
        rows.append({
            "date": pd.Timestamp(r.date).date().isoformat(), "ts": "",
            "home": clean_team(tmap.get(r.home_team, r.home_team)),
            "away": clean_team(tmap.get(r.away_team, r.away_team)),
            "home_score": int(r.home_score), "away_score": int(r.away_score),
            "o_home": oh, "o_away": oa, "y": float(r.outcome),
        })
    return sorted(rows, key=lambda x: x["date"])


class _TeamForm:
    """팀별 득점·실점 롤링 평균 (KBO 박스스코어, 워크포워드)."""

    def __init__(self) -> None:
        self._history: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
        for r in _kbo_score_rows():
            self._history[r["home_team"]].append(
                (r["date"], r["home_score"], r["away_score"]))
            self._history[r["away_team"]].append(
                (r["date"], r["away_score"], r["home_score"]))
        for team in self._history:
            self._history[team].sort()

    def lambdas(self, home: str, away: str, as_of: str) -> tuple[float, float] | None:
        def roll(team: str):
            prior = [(s, a) for d, s, a in self._history.get(team, []) if d < as_of]
            if len(prior) < 8:
                return None
            recent = prior[-TEAM_WINDOW:]
            gf = sum(s for s, _ in recent) / len(recent)
            ga = sum(a for _, a in recent) / len(recent)
            return gf, ga
        h, a = roll(home), roll(away)
        if h is None or a is None:
            return None
        lam_home = (h[0] + a[1]) / 2 * 1.05
        lam_away = (a[0] + h[1]) / 2
        return float(lam_home), float(lam_away)


def _kbo_score_rows() -> list[dict]:
    """박스스코어에서 팀·날짜·득실만."""
    import json
    raw = json.loads((ROOT / "data" / "raw" / "detail"
                      / "kbo_baseball_2023_2026.json").read_text(encoding="utf-8"))
    out = []
    for g in raw.values():
        day = str(g.get("date") or "")[:10]
        hs, as_ = g.get("home_score"), g.get("away_score")
        if not day or hs is None or as_ is None:
            continue
        out.append({"date": day, "home_team": clean_team(g.get("home")),
                    "away_team": clean_team(g.get("away")),
                    "home_score": int(hs), "away_score": int(as_)})
    return out


def _p_home(lam_home: float, lam_away: float) -> float:
    h, _, a = p_win(joint(lam_home, lam_away, "bs"))
    return h / (h + a) if h + a > 0 else 0.5


def _bootstrap(values: np.ndarray, stat=np.mean, n: int = 5000) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(values), size=(n, len(values)))
    draws = stat(values[idx], axis=1)
    return tuple(np.quantile(draws, [0.025, 0.975]))


def run(k_values=K_GRID) -> int:
    games = _full_games()
    source = "games.csv+bets.csv (전 기간)"
    if not games:
        games = _snapshot_games()
        source = "odds_timeseries 스냅샷 (2026-07-26~)"
    if not games:
        print("정산된 KBO 승패 경기가 없다 — build_dataset 산출물이 필요하다")
        return 1
    print(f"데이터 출처: {source}")
    form = StarterForm.from_boxscores(load_starter_boxscores())
    team_form = _TeamForm()

    rows = []
    for g in games:
        lam = team_form.lambdas(g["home"], g["away"], g["date"])
        if lam is None:
            continue
        delta = form.matchup_delta(
            _detail_starter(g, "home"), _detail_starter(g, "away"), g["date"])
        p_mkt = multiplicative([g["o_home"], g["o_away"]])[0]
        rows.append({**g, "lam": lam, "delta": delta, "p_mkt": p_mkt})

    n_total = len(rows)
    n_delta = sum(1 for r in rows if r["delta"] is not None)
    print(f"검증 표본 {n_total}경기 (양 선발 xFIP 확보 {n_delta}) · "
          f"기간 {rows[0]['date']}~{rows[-1]['date']}\n")

    y = np.array([r["y"] for r in rows])
    p_base = np.array([_p_home(*r["lam"]) for r in rows])
    p_mkt = np.array([r["p_mkt"] for r in rows])
    close = (p_mkt >= 0.45) & (p_mkt <= 0.55)

    o_home = np.array([r["o_home"] for r in rows])
    o_away = np.array([r["o_away"] for r in rows])

    def brier(p, mask=None):
        m = slice(None) if mask is None else mask
        return float(np.mean((p[m] - y[m]) ** 2))

    def roi(p, mask=None):
        m = slice(None) if mask is None else mask
        pick_home = p[m] >= 0.5
        odds = np.where(pick_home, o_home[m], o_away[m])
        won = np.where(pick_home, y[m] == 1, y[m] == 0)
        return float(np.mean(np.where(won, odds - 1.0, -1.0)))

    print(f"{'구간':<8}{'n':>5}{'시장Brier':>11}{'팀λ Brier':>11}{'ROI':>9}")
    print("-" * 46)
    for label, mask in (("전체", np.ones(len(rows), bool)), ("박빙", close)):
        print(f"{label:<8}{int(mask.sum()):>5}{brier(p_mkt, mask):>11.5f}"
              f"{brier(p_base, mask):>11.5f}{roi(p_base, mask):>+9.2%}")

    print(f"\n{'k':>6}{'Brier(전체)':>13}{'Δ대팀λ':>10}{'Brier(박빙)':>13}"
          f"{'Δ대팀λ':>10}{'ROI(전체)':>11}{'채택?':>8}")
    print("-" * 74)
    base_all, base_close, base_roi = brier(p_base), brier(p_base, close), roi(p_base)
    best = None
    for k in k_values:
        p_adj = []
        for r in rows:
            if r["delta"] is None:
                p_adj.append(_p_home(*r["lam"]))
                continue
            adj = apply_xfip_lambda_adjust((r["lam"][0], r["lam"][1], "bt"), r["delta"], k)
            lam = adj[:2] if adj else r["lam"]
            p_adj.append(_p_home(*lam))
        p_adj = np.array(p_adj)
        b_all, b_close = brier(p_adj), brier(p_adj, close)
        r_all = roi(p_adj)
        ok = (b_all <= base_all + 1e-6) and (r_all >= base_roi - 1e-4)
        tag = "✅" if ok and k > 0 else ("기준" if k == 0 else "")
        print(f"{k:>6.2f}{b_all:>13.5f}{b_all - base_all:>+10.5f}{b_close:>13.5f}"
              f"{b_close - base_close:>+10.5f}{r_all:>+11.2%}{tag:>8}")
        if k > 0 and ok and (best is None or b_all < best[1]):
            best = (k, b_all, b_close, r_all)

    lo, hi = _bootstrap((p_base - y) ** 2 - (p_mkt - y) ** 2)
    print(f"\n팀λ − 시장 Brier 차 95%CI [{lo:+.5f}, {hi:+.5f}] "
          f"({'시장 유의 우위' if lo > 0 else '판정 불가'})")

    print()
    if best is None:
        print("판정: **채택 안 함** — 어떤 k 도 검증 Brier 를 개선하며 ROI 를 지키지 못했다.")
        print("      generate_v2 의 STARTER_XFIP_LAMBDA_K 를 0 으로 되돌리고 보고만 한다.")
    else:
        print(f"판정: k={best[0]:.2f} 에서 검증 Brier {best[1]:.5f} "
              f"(팀λ 대비 {best[1] - base_all:+.5f}) · ROI {best[3]:+.2%}")
        print("      단, 표본이 짧다. 시즌이 쌓이면 다시 돌려 판정력을 확인할 것.")
    return 0


def _detail_starter(game: dict, side: str) -> str:
    """그 경기 박스스코어의 실제 선발명 (백테스트는 예고 대신 실측 선발을 쓴다)."""
    return _STARTER_INDEX.get((game["date"], game["home"], game["away"]), ("", ""))[
        0 if side == "home" else 1]


def _build_starter_index() -> dict[tuple, tuple[str, str]]:
    import json
    raw = json.loads((ROOT / "data" / "raw" / "detail"
                      / "kbo_baseball_2023_2026.json").read_text(encoding="utf-8"))
    out = {}
    for g in raw.values():
        d = g.get("data") or {}
        h, a = d.get("home") or [], d.get("away") or []
        if not h or not a:
            continue
        key = (str(g.get("date") or "")[:10], clean_team(g.get("home")),
               clean_team(g.get("away")))
        out[key] = (str((h[0] or {}).get("name") or ""),
                    str((a[0] or {}).get("name") or ""))
    return out


_STARTER_INDEX = _build_starter_index()


def main(argv: list[str]) -> int:
    if "--k" in argv:
        k = float(argv[argv.index("--k") + 1])
        return run(k_values=(0.0, k))
    return run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
