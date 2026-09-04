"""KBO 선발투수 xFIP 근사 — 검증된 신호를 라이브 모델이 쓰게 만든다.

배경
----
`findings/박빙과xFIP.md` 에서 **선발 xFIP 차이**가 검증됐다: Brier 개선 전체
+0.006, 박빙 구간 +0.014(2.4배). 그런데 그 계산은 `pitcher_xfip.py` 안에서만
쓰이는 오프라인 검증이었고, 화면 확률(`generate_v2.lambdas_for`)에는 선발투수
보정이 전혀 없다. 이 모듈은 **같은 xFIP 근사를 투수·날짜 단위로 재사용 가능한
형태**로 만들어, λ 보정(`generate_v2`)과 백테스트(`pitcher_backtest.py`)가 정확히
같은 값을 쓰도록 한다.

xFIP 근사 (`pitcher_xfip.py` 와 동일한 식)
----------------------------------------
    xFIP = (13·HR_adj + 3·BB − 2·K) / IP + FIP상수
    HR_adj = w·HR + (1−w)·(리그 HR/9 · IP/9),   w = IP / (IP + 40)

표본이 작을수록 피홈런을 리그 평균 쪽으로 끌어당긴다(뜬공 데이터가 없어서 쓰는
근사). **모든 값은 워크포워드**다 — 어떤 경기의 xFIP 도 그 경기 시작 전 등판만
사용한다. FIP 상수·리그 HR/9 도 그 경기 이전 1년치 선발 등판에서만 구한다.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pitcher_er import _inn                              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DETAIL = ROOT / "data" / "raw" / "detail" / "kbo_baseball_2023_2026.json"
ARTIFACT = ROOT / "data" / "processed" / "kbo_starter_xfip.json"

WINDOW = 12            # 선발별 롤링 등판 수
MIN_IP = 15.0         # 이만큼 안 쌓이면 xFIP 를 내지 않는다
SHRINK_K = 40.0       # HR 축소 강도 — 이 이닝만큼 리그 평균을 섞는다
BASELINE_DAYS = 365   # FIP 상수·리그 HR/9 를 구하는 직전 기간


def _as_date(value: object) -> str:
    """다양한 날짜 표기를 'YYYY-MM-DD' 로 정규화한다."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(".", "-").replace("/", "-")
    return text[:10]


@dataclass
class _App:
    date: str
    ip: float
    er: float
    hr: float
    bb: float
    kk: float


def _pitcher_row(raw: dict) -> dict:
    return {
        "pcode": str(raw.get("pcode") or "").strip() or None,
        "name": str(raw.get("name") or "").strip(),
        "ip": _inn(raw.get("inn")),
        "er": float(raw.get("er") or 0),
        "hr": float(raw.get("hr") or 0),
        "bb": float(raw.get("bb") or 0),
        "kk": float(raw.get("kk") or 0),
    }


def load_starter_boxscores(path: Path = DETAIL) -> list[dict]:
    """박스스코어에서 선발(각 팀 첫 투수)만 뽑아 날짜순으로 돌려준다."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows: list[dict] = []
    for game in raw.values():
        data = game.get("data") or {}
        home, away = data.get("home") or [], data.get("away") or []
        if not home or not away:
            continue
        day = _as_date(game.get("date"))
        if not day:
            continue
        rows.append({
            "date": day,
            "home_team": str(game.get("home") or "").strip(),
            "away_team": str(game.get("away") or "").strip(),
            "home_sp": _pitcher_row(home[0]),
            "away_sp": _pitcher_row(away[0]),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


@dataclass
class StarterForm:
    """워크포워드 선발 xFIP 조회기."""

    window: int = WINDOW
    min_ip: float = MIN_IP
    shrink_k: float = SHRINK_K
    baseline_days: int = BASELINE_DAYS
    _apps: dict[str, list[_App]] = field(default_factory=lambda: defaultdict(list))
    _name_pcodes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    _starter_apps: list[_App] = field(default_factory=list)
    _lg_cache: dict[str, float | None] = field(default_factory=dict)

    # ---- 구성 ---------------------------------------------------------------
    def add_appearance(self, pcode: str | None, name: str, app: _App) -> None:
        key = pcode or f"name:{name}"
        self._apps[key].append(app)
        self._starter_apps.append(app)
        if name and key not in self._name_pcodes[name]:
            self._name_pcodes[name].append(key)

    @classmethod
    def from_boxscores(cls, rows: list[dict], **kwargs) -> "StarterForm":
        form = cls(**kwargs)
        for row in rows:
            for side in ("home_sp", "away_sp"):
                sp = row[side]
                if sp["ip"] <= 0 and sp["er"] == 0 and sp["bb"] == 0 and sp["kk"] == 0:
                    continue
                form.add_appearance(sp["pcode"], sp["name"], _App(
                    row["date"], sp["ip"], sp["er"], sp["hr"], sp["bb"], sp["kk"]))
        for key in form._apps:
            form._apps[key].sort(key=lambda a: a.date)
        form._starter_apps.sort(key=lambda a: a.date)
        return form

    # ---- 조회 ---------------------------------------------------------------
    def _baseline(self, as_of: str) -> tuple[float, float]:
        """그 경기 직전 ``baseline_days`` 일치 선발 등판에서 FIP 상수·리그 HR/9."""
        try:
            floor = (datetime.fromisoformat(as_of) - timedelta(days=self.baseline_days)
                     ).date().isoformat()
        except ValueError:
            floor = ""
        window = [a for a in self._starter_apps if floor <= a.date < as_of]
        if sum(a.ip for a in window) < 300:      # 너무 얇으면 이전 전체로 확장
            window = [a for a in self._starter_apps if a.date < as_of]
        if not window:
            window = self._starter_apps
        ip = sum(a.ip for a in window) or 1.0
        er = sum(a.er for a in window)
        hr = sum(a.hr for a in window)
        bb = sum(a.bb for a in window)
        kk = sum(a.kk for a in window)
        fip_c = er / ip * 9 - (13 * hr + 3 * bb - 2 * kk) / ip
        lg_hr9 = hr / ip * 9
        return fip_c, lg_hr9

    def _key_for_name(self, name: str, as_of: str) -> str | None:
        keys = self._name_pcodes.get((name or "").strip())
        if not keys:
            return None
        if len(keys) == 1:
            return keys[0]
        # 동명이인: 그 시점 직전에 가장 최근 등판한 쪽을 택한다.
        def last_before(key: str) -> str:
            prior = [a.date for a in self._apps.get(key, []) if a.date < as_of]
            return prior[-1] if prior else ""
        return max(keys, key=last_before) or None

    def xfip(self, key: str | None, as_of: str) -> float | None:
        if not key:
            return None
        prior = [a for a in self._apps.get(key, []) if a.date < as_of]
        if not prior:
            return None
        recent = prior[-self.window:]
        ip = sum(a.ip for a in recent)
        if ip < self.min_ip:
            return None
        hr = sum(a.hr for a in recent)
        bb = sum(a.bb for a in recent)
        kk = sum(a.kk for a in recent)
        fip_c, lg_hr9 = self._baseline(as_of)
        w = ip / (ip + self.shrink_k)
        hr_adj = w * hr + (1 - w) * (lg_hr9 * ip / 9)
        return (13 * hr_adj + 3 * bb - 2 * kk) / ip + fip_c

    def xfip_by_name(self, name: str, as_of: str) -> float | None:
        return self.xfip(self._key_for_name(name, _as_date(as_of)), _as_date(as_of))

    def league_xfip(self, as_of: str) -> float | None:
        """그 시점 기준 유효 xFIP 를 낸 선발들의 중앙값 — 리그 평균 대용."""
        day = _as_date(as_of)
        if day in self._lg_cache:
            return self._lg_cache[day]
        vals = sorted(v for key in self._apps
                      if (v := self.xfip(key, day)) is not None and 2.0 <= v <= 9.0)
        result = vals[len(vals) // 2] if vals else None
        self._lg_cache[day] = result
        return result

    def matchup_delta(self, home_starter: str, away_starter: str,
                      as_of: str) -> dict | None:
        """홈·원정 선발 xFIP 와 그 차이.

        ``xfip_diff = away_xfip − home_xfip`` — `pitcher_xfip.py` 와 부호가 같다
        (양수면 원정 선발이 더 나쁘다 = 홈 쪽에 유리).
        """
        day = _as_date(as_of)
        hx = self.xfip_by_name(home_starter, day)
        ax = self.xfip_by_name(away_starter, day)
        if hx is None or ax is None:
            return None
        lg = self.league_xfip(day)
        return {
            "home_xfip": round(hx, 4),
            "away_xfip": round(ax, 4),
            "xfip_diff": round(ax - hx, 4),
            "league_xfip": round(lg, 4) if lg is not None else None,
            "as_of": day,
        }

    # ---- 아티팩트 --------------------------------------------------------
    def to_artifact(self) -> dict:
        pitchers = {}
        for key, apps in self._apps.items():
            name = next((n for n, ks in self._name_pcodes.items() if key in ks), "")
            pitchers[key] = {
                "name": name,
                "apps": [[a.date, round(a.ip, 3), a.er, a.hr, a.bb, a.kk]
                         for a in apps],
            }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "params": {"window": self.window, "min_ip": self.min_ip,
                       "shrink_k": self.shrink_k, "baseline_days": self.baseline_days},
            "pitchers": pitchers,
        }

    @classmethod
    def from_artifact(cls, path: Path = ARTIFACT) -> "StarterForm":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        params = raw.get("params") or {}
        form = cls(window=int(params.get("window", WINDOW)),
                   min_ip=float(params.get("min_ip", MIN_IP)),
                   shrink_k=float(params.get("shrink_k", SHRINK_K)),
                   baseline_days=int(params.get("baseline_days", BASELINE_DAYS)))
        for key, entry in (raw.get("pitchers") or {}).items():
            name = str(entry.get("name") or "")
            for row in entry.get("apps") or []:
                day, ip, er, hr, bb, kk = row
                form.add_appearance(
                    None if key.startswith("name:") else key, name,
                    _App(str(day), float(ip), float(er), float(hr),
                         float(bb), float(kk)))
        for k in form._apps:
            form._apps[k].sort(key=lambda a: a.date)
        form._starter_apps.sort(key=lambda a: a.date)
        return form


def apply_xfip_lambda_adjust(lam, delta: dict, k: float):
    """검증된 선발 xFIP 신호로 야구 λ(3-튜플: λ홈, λ원정, 출처)를 보정한다.

    각 팀의 λ는 **상대 선발**의 xFIP 로 움직인다. 상대 선발이 리그 중앙값보다
    1점(9이닝) 나쁘면 그 팀의 λ를 ``k/9`` 만큼 올린다. 보정이 없으면 ``None``.
    """
    if not lam or not k:
        return None
    lg = (delta or {}).get("league_xfip")
    if lg is None:
        return None
    lh = max(0.15, lam[0] + k * (delta["away_xfip"] - lg) / 9.0)
    la = max(0.15, lam[1] + k * (delta["home_xfip"] - lg) / 9.0)
    if abs(lh - lam[0]) < 1e-9 and abs(la - lam[1]) < 1e-9:
        return None
    return float(lh), float(la), f"{lam[2]}+선발"


def write_artifact(path: Path = ARTIFACT, detail: Path = DETAIL) -> Path:
    form = StarterForm.from_boxscores(load_starter_boxscores(detail))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(form.to_artifact(), ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return path


def _coverage_report() -> int:
    rows = load_starter_boxscores()
    form = StarterForm.from_boxscores(rows)
    by_year: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        year = row["date"][:4]
        by_year[year][0] += 1
        delta = form.matchup_delta(row["home_sp"]["name"], row["away_sp"]["name"],
                                   row["date"])
        if delta is not None:
            by_year[year][1] += 1
    print(f"박스스코어 {len(rows):,}경기 · 고유 투수 {len(form._apps):,}명")
    print(f"{'연도':<6}{'경기':>8}{'양 선발 xFIP 확보':>18}{'비율':>8}")
    for year in sorted(by_year):
        total, ok = by_year[year]
        print(f"{year:<6}{total:>8,}{ok:>18,}{ok / max(total, 1):>7.1%}")
    return 0


def main(argv: list[str]) -> int:
    if "--report" in argv:
        return _coverage_report()
    path = write_artifact()
    form = StarterForm.from_artifact(path)
    print(f"선발 xFIP 아티팩트 저장: {path} · 투수 {len(form._apps):,}명")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
