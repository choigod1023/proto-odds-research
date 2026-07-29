"""전체 자기검사 러너 — 데이터 파이프라인을 건드렸으면 이걸 돌린다.

왜 있나
-------
2026-07-28 하루에 **가짜 발견을 네 번** 만들었다. 전부 통계 문제가 아니라
**데이터 처리 문제**였다.

| 가짜 | 겉보기 | 원인 |
|---|---|---|
| KBL '승무패' | ROI +30.05% | `WIN_IDX` 에 `⑤` 가 없어 32% 누락 |
| FA컵 R1 | 라인 4/4 | 선택 효과 + 순환논리 |
| 승①패 중간 | −3.85% | 단일 연도 현상 |
| 마켓 정합성 | ROI +12.48% | 무승부 2,343건 제외 |

Bonferroni·부트스트랩·시간분리를 **다 통과해도** 이런 건 안 잡힌다.
**통계 관문은 표본이 올바르다는 전제 위에서만 작동한다.**

그리고 같은 함수(`market_family`)가 **하루에 두 번** 깨졌다 —
아침에 승⑤패를 고치고, 저녁에 홀짝을 고치다 승⑤패를 다시 깨뜨렸다.
눈으로는 못 지킨다.

사용:
    python3 src/selftest_all.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent

# (스크립트, 무엇을 지키는가)
TESTS = [
    ("wisetoto.py", "마켓 분류 (태그 × 선택지수 × 종목)"),
    ("bets.py", "결과값 → 승리 선택지 매핑 커버리지"),
    ("market_scan.py", "WIN_IDX 커버리지 + 모델 확률 정합성"),
    ("score_dist.py", "확률 성질 (합=1 · 단조성 · 밴드 포함관계)"),
    ("generate_v2.py", "사이트 렌더 가능성 (이름 + 모델확률)"),
    ("combo.py", "조합 산술 (규정 · 다리 추가는 손해 · 대수의 법칙)"),
    ("devig.py", "devig 4종 (합=1 · 양수)"),
    ("guard.py", "표본 축소 가드 (결과값 기반 누락 탐지)"),
]


def main() -> int:
    print("=" * 66)
    print("전체 자기검사")
    print("=" * 66)
    results = []
    for script, what in TESTS:
        path = SRC / script
        if not path.exists():
            results.append((script, "SKIP", "파일 없음"))
            continue
        # guard.py 는 인자 없이 self_test 를 돈다
        args = [sys.executable, str(path)]
        if script != "guard.py":
            args.append("--selftest")
        r = subprocess.run(args, capture_output=True, text=True, cwd=SRC.parent)
        ok = r.returncode == 0
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
        summary = tail[-1][:70] if tail else (r.stderr or "").strip().splitlines()[-1:][0][:70] if r.stderr else ""
        results.append((script, "PASS" if ok else "FAIL", summary))
        print(f"\n[{script}] {what}")
        for ln in tail[-6:]:
            print(f"   {ln}")

    print("\n" + "=" * 66)
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    for script, status, summary in results:
        mark = {"PASS": "✅", "FAIL": "🔴", "SKIP": "⏭"}[status]
        print(f"{mark} {script:<18} {summary}")
    print("=" * 66)
    if n_fail:
        print(f"🔴 {n_fail}건 실패 — 고치기 전에는 어떤 결과도 믿지 말 것")
        return 1
    print("✅ 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
