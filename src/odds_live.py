"""실시간 배당 — 화면의 배당이 한 시간씩 낡지 않게 한다.

왜 필요했나
-----------
배당은 `generate_v2` 가 **산출물 갱신 때만** 긁는다. 그 주기를 6시간에서 1시간으로
줄였지만 여전히 최대 한 시간 낡는다. 2026-08-13 실측: 화면에 뜬 배당 231건 중
**73건(32%)** 이 원천과 달랐다(예: 토론블루vs보스레드 승패 화면 2.17/1.48 → 원천 2.37/1.40).

`snapshot.py` 가 15분마다 배당을 모으고는 있지만 그건 연구용 아카이브
(`odds_timeseries_*.csv`)로 가고 화면에는 오지 않는다.

이 프로젝트엔 이미 같은 문제를 푼 전례가 있다 — **실시간 점수**다.
git push(30분)로는 3분 주기를 못 나르니 머신이 그 파일만 직접 서빙한다.
배당도 같은 경로로 뺀다. 3분마다 커밋하면 하루 300커밋으로 레포가 망가진다.

무엇을 하나
-----------
발매 중인 회차의 배당만 긁어 **작은 JSON 하나**로 떨군다. CSV 를 읽지 않고
계산도 하지 않으므로 가볍다(무거운 PUBLISH 와 분리한 이유가 그거다).

    docs/data/live_odds.json   {"generated_at":…, "odds": {"95": {"1923": [1.89, 1.95]}}}

화면은 picks_v2.json 의 배당 위에 이 값을 덮어쓴다. 없으면 원래 값을 쓴다.

    python src/odds_live.py             # 1회
    python src/odds_live.py --loop 300  # 5분마다
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot import find_live_rounds, _fetch          # noqa: E402
from wisetoto import CACHE, _session                   # noqa: E402
from runtime_db import load_artifact, persist_artifact  # noqa: E402
from live_market_refresh import refresh_once           # noqa: E402
from devig import market_probabilities                  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "live_odds.json"
PICKS = ROOT / "docs" / "data" / "picks_v2.json"
LINE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _clean_team(value: str) -> str:
    text = re.sub(r"^\s*-?\d+(?:\.\d+)?\s+", "", str(value or "").strip())
    return re.sub(r"\s+-?\d+(?:\.\d+)?\s*$", "", text).strip()


def _start_hint(season: int) -> int:
    """훑기 시작할 회차. 아카이브에 있는 최신 회차에서 조금 앞으로 물러선다."""
    d = CACHE / str(season)
    have = sorted(int(p.stem.replace(".html", "")) for p in d.glob("*.html.gz")) \
        if d.exists() else []
    return max(1, (max(have) - 3) if have else 1)


def collect(previous_picks: dict | None = None) -> dict:
    sess = _session()
    season = datetime.now(timezone.utc).year
    hint = _start_hint(season)
    # 운영에서는 회차 HTML 캐시를 쓰지 않는다. 캐시가 비었다고 1회차부터
    # 12개만 훑으면 100회차대의 현재 발매에 영원히 도달하지 못한다.
    known_rounds = [int(value) for value in (previous_picks or {}).get("rounds", [])
                    if str(value).isdigit()]
    if hint == 1 and known_rounds:
        hint = max(1, max(known_rounds) - 3)
    rounds = find_live_rounds(sess, season, hint)

    odds: dict[str, dict[str, list[float]]] = {}
    markets: dict[str, dict[str, dict]] = {}
    n = 0
    for rnd in rounds:
        try:
            rows = _fetch(sess, season, rnd)
        except Exception as e:                          # noqa: BLE001
            print(f"  [{season}-{rnd}] 오류 {type(e).__name__}: {e}", flush=True)
            continue
        if not rows:
            continue
        # ⚠️ 배당이 아직 안 붙은 행(odds=[])은 넣지 않는다. 넣으면 화면이
        #    멀쩡한 값을 빈 값으로 덮어써 오히려 나빠진다.
        priced = [r for r in rows if r.odds]
        bucket = {str(r.game_no): [round(o, 2) for o in r.odds] for r in priced}
        if bucket:
            odds[str(rnd)] = bucket
            markets[str(rnd)] = {
                str(r.game_no): {
                    "game_no": str(r.game_no), "date": r.date_text,
                    "sport": r.sport, "league": r.league,
                    "home": _clean_team(r.home), "away": _clean_team(r.away),
                    "market": r.market_family, "label": r.market_label or "",
                    "n_way": r.n_way, "odds": [round(o, 2) for o in r.odds],
                    "result": r.result,
                }
                for r in priced
            }
            n += len(bucket)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rounds": rounds,
        "n": n,
        "odds": odds,
        # 가격만 보내면 기존 picks 문서가 options=[]인 경기를 복구할 수 없다.
        # 최소 경기·마켓 메타데이터를 함께 보내 브라우저와 경량 게시기가 현재
        # 발매 행에서 선택지를 재구성할 수 있게 한다.
        "markets": markets,
    }


def _line(label: object) -> float | None:
    match = LINE.search(str(label or ""))
    return float(match.group()) if match else None


def _history_entry(observed_at: str, market: object, label: object,
                   odds: list[object]) -> dict:
    prices = [round(float(value), 2) for value in odds]
    probabilities = None
    if len(prices) >= 2 and all(value > 1 for value in prices):
        probabilities = [round(value, 4) for value in market_probabilities(prices)]
    return {
        "observed_at": observed_at, "market": str(market or ""),
        "label": str(label or ""), "line": _line(label),
        "odds": prices, "probabilities": probabilities,
    }


def _entry_signature(entry: dict) -> tuple:
    return (entry.get("market"), entry.get("label"), entry.get("line"),
            tuple(entry.get("odds") or []))


def merge_market_history(current: dict, previous: dict | None = None,
                         picks: dict | None = None) -> dict:
    """현재 발매 변경만 보존한다. 매 폴링 중복은 넣지 않고 마켓별 최근 50건을 둔다."""
    history = json.loads(json.dumps((previous or {}).get("history") or {}))
    pick_rows: dict[tuple[str, str], list[dict]] = {}
    for game in (picks or {}).get("live") or []:
        round_no = str(game.get("round") or "")
        for option in game.get("options") or []:
            number = str(option.get("게임번호") or "")
            if number:
                pick_rows.setdefault((round_no, number), []).append(option)

    for round_no, markets in (current.get("markets") or {}).items():
        round_history = history.setdefault(str(round_no), {})
        for game_no, row in (markets or {}).items():
            entries = round_history.setdefault(str(game_no), [])
            if not entries:
                baseline = pick_rows.get((str(round_no), str(game_no))) or []
                if baseline:
                    seed = _history_entry(
                        str((picks or {}).get("generated_at") or current.get("generated_at") or ""),
                        baseline[0].get("market"), baseline[0].get("label"),
                        [option.get("배당") for option in baseline],
                    )
                    entries.append(seed)
            latest = _history_entry(
                str(current.get("generated_at") or ""), row.get("market"),
                row.get("label"), row.get("odds") or [],
            )
            if not entries or _entry_signature(entries[-1]) != _entry_signature(latest):
                entries.append(latest)
            round_history[str(game_no)] = entries[-50:]
    current["history"] = history
    return current


def main(argv: list[str]) -> int:
    loop = 0
    if "--loop" in argv:
        loop = int(argv[argv.index("--loop") + 1])

    while True:
        try:
            previous_picks = load_artifact("picks_v2", PICKS)
            data = merge_market_history(
                collect(previous_picks), load_artifact("live_odds", OUT),
                previous_picks,
            )
            if not data.get("rounds") or not data.get("n"):
                raise RuntimeError("no published rounds or priced markets discovered")
            persist_artifact("live_odds", data, OUT, indent=None)
            # 같은 수집 결과로 즉시 picks_v2까지 갱신한다. 독립 5분 루프에 맡기면
            # 두 주기가 엇갈릴 때 발표된 배당이 화면에 늦게 나타난다.
            refresh_once(data)
            print(f"실시간 배당 {data['n']}건 · 회차 {data['rounds']} → runtime artifact live_odds",
                  flush=True)
        except Exception as e:                          # noqa: BLE001
            # 여기서 죽으면 화면이 낡은 값을 쓸 뿐이다. 다음 주기에 다시 한다.
            print(f"실시간 배당 실패: {type(e).__name__}: {e}", flush=True)
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
