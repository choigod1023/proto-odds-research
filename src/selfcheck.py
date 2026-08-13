"""파이프라인 자가 점검 — "돌고 있나"가 아니라 "제대로 나가고 있나"를 본다.

2026-08 에 세 번 조용히 멈췄다. 매번 겉으로는 멀쩡해 보였다:
  · 08-06 볼륨 100% → 모든 쓰기 ENOSPC. 머신은 started, /health 는 200 "ok"
  · 08-08 재빌드 타임아웃 → games.csv 가 2025 중간에서 잘림. 크기는 19MB 로 멀쩡
  · 08-10 detached HEAD → push 167회 연속 실패. 커밋은 정상적으로 쌓임
공통점은 **밖에서 보이는 신호가 전부 정상이었다**는 것이다. 그래서 각 단계의
산출물을 직접 열어 확인한다.

    python src/selfcheck.py
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data" / "raw" / "snapshots"
DOCS = ROOT / "docs" / "data"

FAIL: list[str] = []
WARN: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    print(f"  🔴 {msg}")
    FAIL.append(msg)


def warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")
    WARN.append(msg)


def sh(*args: str) -> str:
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def _rows(p: Path) -> int:
    with p.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1        # 헤더 제외


def check_shards() -> None:
    print("\n[1] 스냅샷 샤드")
    shards = sorted(SNAP_DIR.glob("odds_timeseries_*.csv"))
    if not shards:
        bad("샤드가 없다")
        return
    total = sum(_rows(p) for p in shards)
    biggest = max(shards, key=lambda p: p.stat().st_size)
    mb = biggest.stat().st_size / 1024 / 1024
    print(f"  샤드 {len(shards)}개 · 총 {total:,}행 · 최대 {biggest.name} {mb:.1f}MB")
    if mb >= 90:
        bad(f"{biggest.name} 이 {mb:.1f}MB — GitHub 100MB 한도에 근접")
    else:
        ok(f"최대 샤드 {mb:.1f}MB (한도 100MB)")

    # 오늘 샤드에 계속 쌓이고 있나
    today = SNAP_DIR / f"odds_timeseries_{datetime.now(timezone.utc):%Y%m%d}.csv"
    if not today.exists():
        warn(f"오늘 샤드({today.name})가 없다 — 자정 직후면 정상")
    else:
        age = (datetime.now(timezone.utc).timestamp() - today.stat().st_mtime) / 60
        if age > 40:
            bad(f"오늘 샤드가 {age:.0f}분째 안 커졌다 (수집 주기 15분)")
        else:
            ok(f"오늘 샤드 {_rows(today):,}행 · {age:.0f}분 전 갱신")

    # 옛 단일 파일이 되살아나면 다시 100MB 한도에 걸린다
    legacy = SNAP_DIR / "odds_timeseries.csv"
    if legacy.exists():
        bad(f"옛 단일 파일이 다시 생겼다 ({legacy.stat().st_size/1024/1024:.0f}MB)")
    else:
        ok("옛 단일 파일 없음")

    # 백업본과 대조 — 이관에서 행이 샜는지
    bak = SNAP_DIR / "odds_timeseries.csv.bak"
    if bak.exists():
        n = _rows(bak)
        if total < n:
            bad(f"샤드 합계({total:,})가 백업({n:,})보다 적다 — 이관 중 유실")
        else:
            ok(f"백업 {n:,}행 ⊆ 샤드 {total:,}행 (이후 수집분 +{total-n:,})")


def check_dataset() -> None:
    print("\n[2] 데이터셋")
    g = ROOT / "data" / "processed" / "games.csv"
    if not g.exists():
        bad("games.csv 가 없다")
        return
    years: dict[str, int] = {}
    with g.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            years[row["year"]] = years.get(row["year"], 0) + 1
    cur = str(datetime.now().year)
    print(f"  연도 분포: { {k: v for k, v in sorted(years.items())} }")
    if years.get(cur):
        ok(f"올해({cur}) {years[cur]:,}행 — 최근폼 계산 가능")
    else:
        bad(f"올해({cur}) 0행 — build_forms 가 비어 해설이 '기록 부족'으로 떨어진다")
    # 잘린 파일이면 마지막 줄이 중간에서 끊긴다
    tail = g.read_bytes()[-200:].decode("utf-8", "replace")
    if not tail.endswith("\n"):
        warn("games.csv 가 개행으로 끝나지 않는다 — 잘렸을 수 있다")


def check_site() -> None:
    print("\n[3] 사이트 산출물")
    p = DOCS / "picks_v2.json"
    if not p.exists():
        bad("picks_v2.json 이 없다")
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    gen = d.get("generated_at", "")
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(gen)).total_seconds() / 3600
    except ValueError:
        age_h = 999
    games = (d.get("live") or []) + (d.get("past") or [])
    print(f"  생성 {gen} ({age_h:.1f}시간 전) · {len(games)}경기")
    if age_h > 3:
        bad(f"산출물이 {age_h:.1f}시간 낡았다 (갱신 주기 1시간)")
    else:
        ok("산출물 신선도 정상")

    have_form = sum(1 for x in games if x.get("form_src"))
    pct = have_form / max(1, len(games)) * 100
    if pct < 80:
        bad(f"최근폼 부착 {have_form}/{len(games)} ({pct:.0f}%) — 80% 미만")
    else:
        ok(f"최근폼 부착 {have_form}/{len(games)} ({pct:.0f}%)")

    import re
    pat = re.compile("기록이 충분히|기록이 부족|표본이 충분히|쌓이지 않")
    poor = sum(1 for x in games if x.get("해설") and pat.search(x["해설"]))
    if poor > len(games) * 0.15:
        bad(f"'기록 부족' 해설 {poor}건 ({poor/len(games)*100:.0f}%)")
    else:
        ok(f"'기록 부족' 해설 {poor}건 ({poor/max(1,len(games))*100:.0f}%)")


def check_live() -> None:
    print("\n[4] 실시간 파일 (머신이 직접 서빙)")
    for name, limit_min in (("live_scores.json", 15), ("live_odds.json", 20)):
        p = DOCS / name
        if not p.exists():
            bad(f"{name} 이 없다")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            gen = datetime.fromisoformat(d["generated_at"])
            age = (datetime.now(timezone.utc) - gen).total_seconds() / 60
        except (ValueError, KeyError):
            bad(f"{name} 을 읽을 수 없다")
            continue
        extra = f" · 배당 {d.get('n')}건 · 회차 {d.get('rounds')}" if "odds" in d else ""
        if age > limit_min:
            bad(f"{name} 이 {age:.0f}분째 그대로 (상한 {limit_min}분){extra}")
        else:
            ok(f"{name} {age:.0f}분 전 갱신{extra}")


def check_git() -> None:
    print("\n[5] git 상태")
    br = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if br == "HEAD":
        bad("detached HEAD — 커밋은 쌓이는데 push 가 안 나간다")
    else:
        ok(f"브랜치 {br}")

    sh("git", "fetch", "-q", "origin", "main")
    ahead = sh("git", "rev-list", "--count", "origin/main..HEAD")
    if ahead and int(ahead) > 3:
        bad(f"origin 대비 {ahead}커밋 앞섬 — push 가 막혀 있다")
    else:
        ok(f"origin 대비 {ahead or 0}커밋 앞섬")

    last = sh("git", "log", "-1", "--format=%ct", "--author=proto-collector")
    if last:
        age_h = (datetime.now(timezone.utc).timestamp() - int(last)) / 3600
        if age_h > 2:
            bad(f"수집기 커밋이 {age_h:.1f}시간째 없다")
        else:
            ok(f"마지막 수집기 커밋 {age_h*60:.0f}분 전")

    loose = sh("git", "count-objects", "-v")
    for line in loose.splitlines():
        if line.startswith("count:"):
            n = int(line.split()[1])
            if n > 4000:
                bad(f"loose object {n:,}개 — gc 가 안 돌고 있다")
            else:
                ok(f"loose object {n:,}개")


def check_llm() -> None:
    print("\n[6] 해설 LLM 덧씌우기")
    b = ROOT / "data" / "raw" / "llm_cache" / "budget.json"
    c = ROOT / "data" / "raw" / "llm_cache" / "commentary.json"
    if not c.exists():
        warn("캐시가 없다 — GEMINI_API_KEY 미설정이면 정상(템플릿으로 동작)")
        return
    n = len(json.loads(c.read_text(encoding="utf-8")))
    used = 0
    if b.exists():
        bud = json.loads(b.read_text(encoding="utf-8"))
        used = bud.get("used", 0)
        if bud.get("date") != datetime.now().strftime("%Y-%m-%d"):
            used = 0
    ok(f"캐시 {n:,}개 · 오늘 호출 {used}건 ≈ {used*0.9:,.0f}원")
    if used >= 700:
        warn("하루 상한(700건) 도달 — 남은 해설은 템플릿으로 나간다")


def main() -> int:
    print(f"=== 자가 점검 {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z")
    for fn in (check_shards, check_dataset, check_site, check_live,
               check_git, check_llm):
        try:
            fn()
        except Exception as e:                          # noqa: BLE001
            bad(f"{fn.__name__} 점검 자체가 실패: {type(e).__name__}: {e}")

    print("\n" + "=" * 50)
    if FAIL:
        print(f"🔴 문제 {len(FAIL)}건")
        for m in FAIL:
            print(f"   - {m}")
    if WARN:
        print(f"⚠️  주의 {len(WARN)}건")
    if not FAIL:
        print("✅ 이상 없음")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
