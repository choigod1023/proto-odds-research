"""배당 이동이 결과를 예고하는가 — 두 소스를 합쳐서 본다.

가설
----
사용자 질문: "시장이 사지 않아 배당이 높아진 것을 고르면 적중률이 오르나."

프로토는 **고정배당**이라 파리뮤추얼처럼 물량이 배당을 직접 만들지는 않는다.
그러나 발매사가 물량·정보에 반응해 배당을 조정한다면, 그 **이동 방향**에
정보가 담길 수 있다. 문헌의 CLV(closing line value)와 닮았지만 실제 판매
마감시각이 없어 여기서는 고정 T-30 대리값만 측정한다.

두 소스가 있다 — 해상도가 달라 나눠서도 보고 합쳐서도 본다.

1. **회차 겹침** (`games.csv`)
   프로토는 회차를 겹쳐 발매해 **같은 경기가 여러 회차에 다른 배당으로** 걸린다.
   이른 회차 = 먼저 매긴 가격, 늦은 회차 = 나중 가격.
   ⚠️ 이 관행은 **2026년에 시작**됐다. 회차당 발매 행이 248(2023) → 427(2026)로
      늘면서 생겼다. 그래서 아카이브가 553회차여도 여기 쓸 수 있는 건 2026년분뿐이다.

2. **스냅샷** (`data/raw/snapshots/odds_timeseries.csv`)
   15분 간격 수집. 첫 관측에서 **경기 30분 전**까지의 움직임을 본다.
   대신 수집을 시작한 뒤부터만 있다.

⚠️ 판정 기준을 데이터 보기 전에 적어둔다
   · 클러스터(마켓) 부트스트랩 95%CI 가 0 을 포함하면 **기각**
   · 두 소스의 부호가 다르면 **기각** (한쪽은 잡음이라는 뜻)
   · 상위 적중 3건을 빼서 부호가 뒤집히면 **기각**

사용:
    python3 src/line_move.py
    python3 src/line_move.py --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from stack_filter import WIN_IDX  # noqa: E402
from matches import _DATETIME_RE, actual_game_year, clean_team  # noqa: E402

GAMES = ROOT / "data" / "processed" / "games.csv"
# ⚠️ 스냅샷은 2026-08-13 부터 **월별 샤드**다(단일 파일이 138MB 가 되어
#    GitHub 100MB 한도에 걸렸다). 경로를 직접 열지 말고 로더를 쓴다.
from snapshot import load_timeseries, ts_files      # noqa: E402
# 실행 가능 가격은 관측 당시 상태가 명시적으로 경기전인 행만 허용한다.
# 빈 값과 '-'는 결과 라벨 정산에서는 비결과 상태지만 판매 중 증거는 아니다.
PREGAME_RESULTS = ("경기전",)
NON_RESULT_STATES = ("경기전", "", "-", "취소")
SNAPSHOT_CUTOFF_MIN = 30
SNAPSHOT_STALE_MIN = 35


def _pair(o0: str, o1: str, n_way: int, result: str, key: str, src: str) -> list[dict]:
    """가격 두 개(이전/이후)를 선택지별 행으로 편다."""
    wi = WIN_IDX.get((int(n_way), str(result)))
    if wi is None:
        return []
    try:
        a = [float(x) for x in str(o0).split(",")]
        b = [float(x) for x in str(o1).split(",")]
    except ValueError:
        return []
    if len(a) != len(b) or len(b) != int(n_way) or any(x <= 1.001 for x in b):
        return []
    out = []
    for i, (p, q) in enumerate(zip(a, b)):
        if abs(q / p - 1) < 1e-9:
            continue                      # 안 움직인 선택지는 정보가 없다
        out.append({"key": key, "src": src, "drift": q / p, "odds": q,
                    "hit": 1 if i == wi else 0,
                    "ret": (q - 1) if i == wi else -1.0})
    return out


def from_rounds(data: pd.DataFrame | None = None) -> pd.DataFrame:
    """회차 겹침 — 같은 *실제 경기·호환 마켓*의 가격만 비교한다.

    날짜만으로 묶으면 같은 팀의 더블헤더 두 경기를 가격 이동으로 오인한다. 따라서
    연월일시분과 정규화 팀, 종목, 마켓 종류·선택지 수·라인을 모두 키로 쓴다.
    동일 판매 회차 안에서 둘 이상의 경기번호/가격/결과가 충돌하거나, 회차 사이의
    승자 라벨이 바뀐 키는 원본을 인증할 수 없으므로 전부 제외한다.
    """
    g = (pd.read_csv(GAMES) if data is None else data.copy())
    g = g[(~g["is_void"].astype(bool)) & (g["n_way"] > 0)].copy()
    g = g[~g["result"].isin(NON_RESULT_STATES)]
    g["n_way"] = pd.to_numeric(g["n_way"], errors="coerce")
    g["round"] = pd.to_numeric(g["round"], errors="coerce")
    g["game_no"] = pd.to_numeric(g["game_no"], errors="coerce")
    g["team_h"] = g["home"].map(clean_team)
    g["team_a"] = g["away"].map(clean_team)
    dt = g["date_text"].astype(str).str.extract(_DATETIME_RE).apply(
        pd.to_numeric, errors="coerce")
    game_year = actual_game_year(g["year"], g["round"], dt[0])
    g["kickoff"] = pd.to_datetime({
        "year": game_year, "month": dt[0], "day": dt[1],
        "hour": dt[2], "minute": dt[3],
    }, errors="coerce")
    g["market_label"] = g["market_label"].fillna("").astype(str).str.strip()
    g = g.dropna(subset=["kickoff", "round", "game_no", "n_way"])
    g[["round", "game_no", "n_way"]] = g[["round", "game_no", "n_way"]].astype(int)

    market_key = ["kickoff", "league", "sport", "team_h", "team_a",
                  "market_family", "n_way", "market_label"]
    g["_market_key"] = list(map(tuple, g[market_key].itertuples(index=False, name=None)))
    g["_winner_idx"] = [WIN_IDX.get((n, str(result)))
                         for n, result in zip(g["n_way"], g["result"])]
    g = g.dropna(subset=["_winner_idx"])

    # 하나의 판매 식별자가 둘 이상의 실제 경기/마켓을 가리키면 어느 행도 쓰지 않는다.
    sale_key = ["year", "round", "game_no"]
    bad_sales = set(g.groupby(sale_key)["_market_key"].nunique()
                    .loc[lambda count: count > 1].index)
    if bad_sales:
        sale_index = pd.MultiIndex.from_frame(g[sale_key])
        g = g.loc[~sale_index.isin(bad_sales)].copy()

    # 같은 마켓·회차에 둘 이상의 경기번호/가격/승자 라벨이 있으면 원본 충돌이다.
    slot_key = market_key + ["year", "round"]
    slot_variants = g.groupby(slot_key, dropna=False)[
        ["game_no", "odds", "result", "_winner_idx"]].nunique(dropna=False)
    bad_slots = set(slot_variants.loc[lambda frame: frame.max(axis=1) > 1].index)
    if bad_slots:
        slot_index = pd.MultiIndex.from_frame(g[slot_key])
        g = g.loc[~slot_index.isin(bad_slots)].copy()

    # 완전히 같은 행만 축약한다. 회차 사이 정산 결과가 다르면 그룹 전체를 버린다.
    g = g.drop_duplicates(
        subset=slot_key + ["game_no", "odds", "result", "_winner_idx"])
    market_variants = g.groupby(market_key, dropna=False)[
        ["result", "_winner_idx"]].nunique(dropna=False)
    conflicting_markets = set(
        market_variants.loc[lambda frame: frame.max(axis=1) > 1].index)
    if conflicting_markets:
        market_index = pd.MultiIndex.from_frame(g[market_key])
        g = g.loc[~market_index.isin(conflicting_markets)].copy()

    rows = []
    for values, x in g.groupby(market_key, dropna=False):
        if x["round"].nunique() < 2 or x["odds"].nunique() < 2:
            continue
        x = x.sort_values(["year", "round"])
        a, b = x.iloc[0], x.iloc[-1]
        kickoff, league, sport, team_h, team_a, family, n_way, label = values
        key = (f"{kickoff.isoformat()}|{league}|{sport}|{team_h}|{team_a}|"
               f"{family}|{n_way}|{label}")
        rows += _pair(a["odds"], b["odds"], b["n_way"], b["result"], key, "회차겹침")
    return pd.DataFrame(rows)


def from_snapshots() -> pd.DataFrame:
    """스냅샷 — 첫 관측 대비 T-30 시점에 살 수 있었을 법한 가격.

    정산 뒤 가격과 kickoff 직전 판매마감 불명 가격은 제외한다. T-30보다 늦지 않은
    35분 이내 관측 중 마지막 값만 쓰며, 실제 sale_close_at이 없어 여전히 연구용이다.
    """
    if not ts_files():
        return pd.DataFrame()
    t = load_timeseries()
    # 스냅샷에는 초 단위와 마이크로초 단위 ISO 시각이 함께 존재한다.
    # pandas 2의 단일 포맷 추론은 첫 행 형식으로 고정돼 정상 행까지 실패시킨다.
    t["ts"] = pd.to_datetime(t["ts"], format="mixed", utc=True, errors="coerce")
    # append 중 프로세스 중단으로 두 CSV 레코드가 붙은 손상 행이 과거 샤드에 1건 있다.
    # n_way가 2/3이 아닌 행은 결과와 무관한 입력 손상이므로 명시적으로 감사 후 제외한다.
    t["n_way"] = pd.to_numeric(t["n_way"], errors="coerce")
    t["year"] = pd.to_numeric(t["year"], errors="coerce")
    dt = t["date_text"].astype(str).str.extract(
        r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})").apply(pd.to_numeric, errors="coerce")
    game_year = actual_game_year(t["year"], t["round"], dt[0])
    naive = pd.to_datetime({"year": game_year, "month": dt[0], "day": dt[1],
                            "hour": dt[2], "minute": dt[3]}, errors="coerce")
    t["kickoff"] = naive.dt.tz_localize(
        "Asia/Seoul", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    t = t.dropna(subset=["ts", "kickoff", "year", "n_way"])
    t = t[t["n_way"].isin([2, 3])]
    t = t.sort_values("ts")
    # 결과는 정산 행에서 가져오되, 가격은 kickoff 이전 행에서만 가져온다. 과거에는
    # 정산 행의 가격을 '마감 배당'으로 써 경기 시작 뒤 움직임까지 섞였다.
    key = ["year", "round", "game_no"]
    t["_event_id"] = (t["league"].astype(str) + "|" + t["kickoff"].astype(str)
                      + "|" + t["home"].map(clean_team)
                      + "|" + t["away"].map(clean_team))
    family = (t["market_family"] if "market_family" in t
              else pd.Series("", index=t.index))
    label = (t["market_label"] if "market_label" in t
             else pd.Series("", index=t.index))
    t["_market_signature"] = (
        family.astype(str) + "|" + t["n_way"].astype(str)
        + "|" + label.fillna("").astype(str).str.strip()
    )
    event_bad = set(t.groupby(key)["_event_id"].nunique().loc[lambda x: x > 1].index)
    market_bad = set(
        t.groupby(key)["_market_signature"].nunique().loc[lambda x: x > 1].index
    )
    # 시각만 kickoff 이전이어도 지연/잠금/진행 상태면 구매 가능 가격으로 인증할
    # 수 없다. 관측 당시 명시적으로 경기전이던 행만 사용한다.
    pre = t[(t["ts"] < t["kickoff"]) & t["result"].isin(PREGAME_RESULTS)]
    first = pre.groupby(key).head(1)[key + ["odds"]].rename(columns={"odds": "o_first"})
    target = pre["kickoff"] - pd.to_timedelta(SNAPSHOT_CUTOFF_MIN, unit="m")
    age = target - pre["ts"]
    cutoff = pre[(age >= pd.Timedelta(0))
                 & (age <= pd.Timedelta(minutes=SNAPSHOT_STALE_MIN))]
    cutoff = cutoff.groupby(key).tail(1)[key + ["odds", "n_way"]].rename(
        columns={"odds": "o_last", "n_way": "n_way_pre"})
    settled = t[~t["result"].isin(NON_RESULT_STATES)].copy()
    settled["_winner_idx"] = [WIN_IDX.get((int(n), str(result)))
                               for n, result in zip(settled["n_way"], settled["result"])]
    settled = settled.dropna(subset=["_winner_idx"])
    label_bad = set(settled.groupby(key)["_winner_idx"].nunique().loc[lambda x: x > 1].index)
    bad = event_bad | market_bad | label_bad
    if bad:
        market_index = pd.MultiIndex.from_frame(settled[key])
        settled = settled.loc[~market_index.isin(bad)]
        for frame_name, frame in (("first", first), ("cutoff", cutoff)):
            market_index = pd.MultiIndex.from_frame(frame[key])
            if frame_name == "first":
                first = frame.loc[~market_index.isin(bad)]
            else:
                cutoff = frame.loc[~market_index.isin(bad)]
    settled = settled.groupby(key).tail(1)
    last = (settled[key + ["result"]]
            .merge(first, on=key, validate="one_to_one")
            .merge(cutoff, on=key, validate="one_to_one"))
    rows = []
    # ``itertuples``는 ``_``가 들어간 열 이름을 위치명으로 치환할 수 있어 스키마가
    # 바뀌면 n_way 자리에 sport가 들어가는 조용한 오염이 생긴다. 명시적 열 조회 사용.
    for _, r in last.iterrows():
        k = f"{r['year']}-{r['round']}-{r['game_no']}"
        rows += _pair(r["o_first"], r["o_last"], r["n_way_pre"], r["result"], k,
                      "스냅샷T30")
    return pd.DataFrame(rows)


def _boot(d: pd.DataFrame, n: int = 4000, seed: int = 42):
    """마켓 단위 클러스터 부트스트랩 — 같은 마켓의 선택지는 독립이 아니다."""
    rng = np.random.default_rng(seed)
    codes, uniq = pd.factorize(d["key"])
    groups = [np.where(codes == i)[0] for i in range(len(uniq))]
    ret, isup = d["ret"].values, (d["drift"] > 1).values
    out = []
    for _ in range(n):
        ii = np.concatenate([groups[j] for j in rng.integers(0, len(uniq), len(uniq))])
        r, u = ret[ii], isup[ii]
        if u.sum() < 10 or (~u).sum() < 10:
            continue
        out.append(r[u].mean() - r[~u].mean())
    return np.array(out)


def _report(name: str, d: pd.DataFrame) -> dict:
    up, dn = d[d["drift"] > 1], d[d["drift"] < 1]
    if len(up) < 20 or len(dn) < 20:
        print(f"  {name:<12} 표본 부족 (상승 {len(up)} · 하락 {len(dn)})")
        return {}
    obs = up["ret"].mean() - dn["ret"].mean()
    bs = _boot(d)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(f"  {name:<12} n={len(d):>5,}  상승 적중 {up['hit'].mean()*100:5.2f}% ROI {up['ret'].mean()*100:+6.2f}%"
          f" | 하락 적중 {dn['hit'].mean()*100:5.2f}% ROI {dn['ret'].mean()*100:+6.2f}%")
    print(f"  {'':12} 차이 {obs*100:+6.2f}%p  95%CI [{lo*100:+.2f}, {hi*100:+.2f}]"
          f"  {'유의' if lo > 0 else '기각'}")
    return {"obs": obs, "lo": lo, "hi": hi, "n": len(d)}


def _selftest() -> int:
    r = from_rounds()
    s = from_snapshots()
    bad = []
    print("배당 이동 자기검사")
    print(f"  회차겹침 {len(r):,} · 스냅샷 {len(s):,}")
    for nm, d in (("회차겹침", r), ("스냅샷T30", s)):
        if d.empty:
            continue
        if not ((d["drift"] > 0).all()):
            bad.append(f"{nm}: drift 가 0 이하")
        if (abs(d["drift"] - 1) < 1e-9).any():
            bad.append(f"{nm}: 안 움직인 행이 섞였다")
    print("  통과: drift 성질 (양수 · 이동한 것만)")
    if bad:
        print("\n실패: " + "\n실패: ".join(bad))
        return 1
    print("\n통과")
    return 0


def main() -> int:
    r, s = from_rounds(), from_snapshots()
    both = pd.concat([r, s], ignore_index=True)
    print("배당이 오른 쪽(안 팔림)이 더 잘 맞는가\n")
    res = {}
    for nm, d in (("회차겹침", r), ("스냅샷T30", s), ("합계", both)):
        if not d.empty:
            res[nm] = _report(nm, d)

    a, b = res.get("회차겹침", {}), res.get("스냅샷T30", {})
    if a and b:
        same = np.sign(a["obs"]) == np.sign(b["obs"])
        print(f"\n두 소스 부호 {'일치' if same else '**불일치**'} "
              f"({a['obs']*100:+.1f}%p vs {b['obs']*100:+.1f}%p)"
              f"{'' if same else ' → 한쪽은 잡음이다. 기각.'}")

    # 지금 표본으로 얼마짜리 효과를 잡을 수 있나 (검정력)
    if not both.empty:
        sd = both["ret"].std()
        n_up = (both["drift"] > 1).sum()
        n_dn = (both["drift"] < 1).sum()
        mde = 2.8 * sd * np.sqrt(1 / n_up + 1 / n_dn)     # 80% 검정력 근사
        print(f"\n지금 표본으로 탐지 가능한 최소 효과(MDE, 80% 검정력) ~= {mde*100:.1f}%p")
        for mult in (2, 4, 10):
            print(f"  표본 {mult}배 →  {mde/np.sqrt(mult)*100:5.1f}%p")
        print("  ※ 프로토 마진이 12% 다. 이걸 넘으려면 최소 그 이상을 잡아야 한다.")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
