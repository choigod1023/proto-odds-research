"""devig — 배당에서 마진을 벗겨 확률을 복원한다.

배당의 역수를 다 더하면 1을 넘는다(오버라운드). 그 초과분이 업체 마진이고,
이걸 어떻게 걷어내느냐에 따라 복원된 확률이 1~2%p 갈린다.
마진 12%짜리 시장에서 1~2%p는 +EV / −EV 판정을 뒤집는 크기다.

    multiplicative  p_i ∝ 1/o_i                  마진을 확률에 비례 배분
    additive        p_i = 1/o_i − (Π−1)/n        마진을 균등 배분
    power           p_i ∝ (1/o_i)^k              강팀·약팀에 다르게 작용
    shin            내부정보 보유자 비율 z 가정     이론적 근거 있음

어느 것이 맞는지는 데이터로 정한다(Q4). 이 모듈은 계산만 제공한다.
"""
from __future__ import annotations

import math

_TOL = 1e-12
_MAX_ITER = 200


def implied(odds: list[float]) -> list[float]:
    """배당 → 원시 내재확률(합이 1을 넘는다)"""
    return [1.0 / o for o in odds]


def overround(odds: list[float]) -> float:
    return sum(implied(odds))


# ------------------------------------------------------------ 방법들

def multiplicative(odds: list[float]) -> list[float]:
    """가장 단순. 마진이 모든 선택지에 비례 배분됐다고 가정한다."""
    pi = implied(odds)
    tot = sum(pi)
    return [p / tot for p in pi]


def additive(odds: list[float]) -> list[float]:
    """초과분을 균등하게 빼낸다. 비교 기준용."""
    pi = implied(odds)
    excess = (sum(pi) - 1.0) / len(pi)
    p = [x - excess for x in pi]
    # 음수 방지 (극단적 배당에서 발생 가능)
    if min(p) <= 0:
        return multiplicative(odds)
    return p


def power(odds: list[float]) -> list[float]:
    """p_i ∝ (1/o_i)^k. 합이 1이 되는 k를 이분법으로 찾는다.

    k > 1 이면 낮은 확률(역배)이 더 크게 깎인다.
    """
    pi = implied(odds)

    def total(k: float) -> float:
        return sum(x ** k for x in pi)

    lo, hi = 0.5, 5.0
    # total(k)는 k에 대해 단조 감소 (pi < 1 이므로)
    if total(lo) < 1.0:
        return multiplicative(odds)
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2
        t = total(mid)
        if abs(t - 1.0) < _TOL:
            break
        if t > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    p = [x ** k for x in pi]
    s = sum(p)
    return [x / s for x in p]


def shin(odds: list[float]) -> list[float]:
    """Shin(1993) — 시장에 내부정보 보유자가 비율 z 만큼 있다고 가정한 모형.

        p_i = [ sqrt(z² + 4(1−z)·π_i²/Π) − z ] / (2(1−z))

    Σp_i = 1 이 되는 z 를 이분법으로 찾는다.
    """
    pi = implied(odds)
    total = sum(pi)
    if total <= 1.0:
        return multiplicative(odds)

    def probs(z: float) -> list[float]:
        if z <= _TOL:
            return [p / total for p in pi]
        out = []
        for p in pi:
            root = math.sqrt(z * z + 4.0 * (1.0 - z) * p * p / total)
            out.append((root - z) / (2.0 * (1.0 - z)))
        return out

    lo, hi = 0.0, 0.9
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2
        s = sum(probs(mid))
        if abs(s - 1.0) < _TOL:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2
    p = probs(z)
    s = sum(p)
    return [x / s for x in p]


METHODS = {
    "multiplicative": multiplicative,
    "additive": additive,
    "power": power,
    "shin": shin,
}

# 화면과 조합기가 서로 다른 마진 제거법을 쓰면 같은 배당에 서로 다른 확률이
# 표시된다. outcome-level 교정 모델이 도입되기 전까지는 프로젝트 내부 실측에서
# 오차가 가장 작았던 Shin을 공통 기준으로 사용한다.
MARKET_PROBABILITY_METHOD = "shin"


def devig(odds: list[float], method: str = "multiplicative") -> list[float]:
    return METHODS[method](odds)


def market_probabilities(odds: list[float]) -> list[float]:
    """사이트 전역에서 쓰는 일관된 시장 기준 확률.

    Shin 계산이 극단적인 입력에서 실패하더라도 화면 생성을 중단하지 않고
    multiplicative로 보수적으로 되돌아간다.
    """
    try:
        probability = devig(odds, MARKET_PROBABILITY_METHOD)
    except (ArithmeticError, ValueError, ZeroDivisionError):
        probability = multiplicative(odds)
    if len(probability) != len(odds) or any(not (0.0 < p < 1.0) for p in probability):
        return multiplicative(odds)
    return probability


def fair_odds(odds: list[float], method: str = "multiplicative") -> list[float]:
    return [1.0 / p for p in devig(odds, method)]


def ev(prob: float, odds: float) -> float:
    """내 확률 추정이 prob일 때 배당 odds에 1원 걸었을 때의 기대 손익."""
    return prob * odds - 1.0


# ------------------------------------------------------------ 자체 검증

def _selftest() -> None:
    cases = [
        ("2-way 박빙", [1.76, 1.76]),
        ("2-way 강약", [1.30, 3.60]),
        ("2-way 극단", [1.10, 6.50]),
        ("3-way 축구", [2.01, 3.00, 3.15]),
        ("3-way 핸디", [3.85, 3.35, 1.65]),
    ]
    print(f"{'케이스':<14}{'오버라운드':>10}  " +
          "  ".join(f"{m:>28}" for m in METHODS))
    for name, o in cases:
        row = f"{name:<14}{overround(o):>10.4f}  "
        parts = []
        for m in METHODS:
            p = devig(o, m)
            assert abs(sum(p) - 1.0) < 1e-9, f"{m} 합이 1이 아님: {sum(p)}"
            assert all(x > 0 for x in p), f"{m} 음수 확률"
            parts.append("  ".join(f"{x:.4f}" for x in p).rjust(28))
        print(row + "  ".join(parts))

    # 방법 간 최대 차이 — 이 크기가 곧 +EV 판정을 뒤집는 여지다
    print("\n방법 간 확률 차이 (최대, %p):")
    for name, o in cases:
        allp = [devig(o, m) for m in METHODS]
        diff = max(max(p[i] for p in allp) - min(p[i] for p in allp)
                   for i in range(len(o)))
        print(f"  {name:<14} {diff*100:>6.2f}%p")
    print("\n✅ 자체검증 통과 (합=1, 전부 양수)")


if __name__ == "__main__":
    _selftest()
