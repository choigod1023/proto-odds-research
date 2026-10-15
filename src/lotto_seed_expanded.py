"""Exploratory, outcome-conditioned seed search; separate from seed-survival-v1.

Counter-based SplitMix64 finalizer + combinadic rank. Every round has one ticket.
The first training hit is constructed by inversion, never counted as evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import random

import numpy as np

from lotto645 import data_hash, load_draws_jsonl, sha256_json, validate_draws

VERSION = "splitmix64-combinadic-v1"
N = math.comb(45, 6)
MASK = (1 << 64) - 1
STEP = 0x9E3779B97F4A7C15
A, B = 0xBF58476D1CE4E5B9, 0x94D049BB133111EB
TABLES = {k: np.array([math.comb(n, k) for n in range(46)], dtype=np.int64)
          for k in range(1, 7)}


def mix(values):
    x = np.asarray(values, dtype=np.uint64).copy()
    x = (x ^ (x >> 30)) * np.uint64(A)
    x = (x ^ (x >> 27)) * np.uint64(B)
    return x ^ (x >> 31)


def _undo_xor(x, shift):
    out = x.copy()
    for offset in range(shift, 64, shift):
        out ^= x >> offset
    return out


def unmix(values):
    x = _undo_xor(np.asarray(values, dtype=np.uint64), 31)
    x *= np.uint64(pow(B, -1, 1 << 64))
    x = _undo_xor(x, 27)
    x *= np.uint64(pow(A, -1, 1 << 64))
    return _undo_xor(x, 30)


def combo_rank(numbers):
    return sum(math.comb(n - 1, k) for k, n in enumerate(sorted(numbers), 1))


def ranks(seeds, draw_no):
    return mix(np.asarray(seeds, dtype=np.uint64) + np.uint64((draw_no * STEP) & MASK)) % np.uint64(N)


def ticket_masks(values):
    rest = np.asarray(values, dtype=np.int64).copy()
    result = np.zeros(rest.shape, dtype=np.uint64)
    for k in range(6, 0, -1):
        positions = np.searchsorted(TABLES[k], rest, side="right") - 1
        result |= np.left_shift(np.uint64(1), positions.astype(np.uint64))
        rest -= TABLES[k][positions]
    return result


def popcount(x):
    x = x - ((x >> 1) & np.uint64(0x5555555555555555))
    x = (x & np.uint64(0x3333333333333333)) + ((x >> 2) & np.uint64(0x3333333333333333))
    x = (x + (x >> 4)) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return ((x * np.uint64(0x0101010101010101)) >> 56).astype(np.int64)


def anchored_seeds(draw, count, search_seed):
    target = combo_rank(draw.numbers_sorted)
    # Sample distinct preimages; each first-draw winning seed has equal probability.
    population = (MASK - target) // N + 1
    quotients = random.Random(search_seed).sample(range(population), count)
    outputs = np.array(quotients, dtype=np.uint64) * np.uint64(N) + np.uint64(target)
    return unmix(outputs) - np.uint64((draw.draw_no * STEP) & MASK)


def select(training, count, search_seed, progress=False):
    candidates = anchored_seeds(training[0], count, search_seed)
    hits = np.zeros(count, dtype=np.int32)
    for i, draw in enumerate(training, 1):
        hits += ranks(candidates, draw.draw_no) == combo_rank(draw.numbers_sorted)
        if progress and i % 100 == 0:
            print(f"training {i}/{len(training)}: two-or-more={np.count_nonzero(hits >= 2)}", flush=True)
    selected = candidates[hits >= 2]
    return selected, {str(k): int(np.count_nonzero(hits >= k))
                      for k in range(1, max(3, int(hits.max())) + 1)}


def uniform_controls(selected, repeats, control_seed):
    rng = random.Random(control_seed)
    excluded = set(map(int, selected))
    values = []
    while len(values) < len(selected) * repeats:
        seed = rng.getrandbits(64)
        if seed not in excluded:
            values.append(seed)
            excluded.add(seed)
    return np.array(values, dtype=np.uint64).reshape(repeats, len(selected))


def evaluate(selected, controls, draws):
    if len(selected) == 0:
        return {"status": "no_survivors"}
    seeds = np.concatenate([selected, controls.ravel()])
    histograms = np.zeros((len(seeds), 7), dtype=np.int64)
    indices = np.arange(len(seeds))
    for draw in draws:
        winning_mask = np.uint64(sum(1 << (n - 1) for n in draw.numbers_sorted))
        matches = popcount(ticket_masks(ranks(seeds, draw.draw_no)) & winning_mask)
        histograms[indices, matches] += 1
    size = len(selected)
    chosen = histograms[:size].sum(axis=0)
    baseline = histograms[size:].reshape(len(controls), size, 7).sum(axis=1)
    ticket_count = size * len(draws)
    means = (baseline @ np.arange(7)) / ticket_count
    observed = float(chosen @ np.arange(7) / ticket_count)
    return {"status": "evaluated", "draw_range": [draws[0].draw_no, draws[-1].draw_no],
            "tickets_per_group": ticket_count, "selected_histogram": chosen.tolist(),
            "selected_mean_matches": observed, "selected_jackpots": int(chosen[6]),
            "baseline_repeats": len(controls), "baseline_mean_matches": float(means.mean()),
            "difference": observed - float(means.mean()),
            "baseline_mean_reference_interval_95": np.quantile(means, [.025, .975]).tolist(),
            "baseline_fraction_at_least_selected": float(np.mean(means >= observed)),
            "baseline_histograms": baseline.tolist(),
            "per_seed": [{"seed": str(int(seed)), "histogram": hist.tolist(),
                          "mean_matches": float(hist @ np.arange(7) / len(draws))}
                         for seed, hist in zip(selected, histograms[:size])]}


def report_markdown(result):
    lines = ["# 로또 2회 적중 시드 확장 탐색", "", "## 실험 조건", "",
             f"- 생성기: `{VERSION}`. 이전 Python Random 생성기와 다른 별도 실험입니다.",
             f"- 학습 첫 회차를 맞히도록 역산한 시드 {result['protocol']['candidate_count']:,}개를 선별했습니다.",
             "- 무작위 시드 100만 개를 전수 탐색했다는 뜻이 아닙니다. 첫 적중은 구성한 결과입니다.",
             f"- 구간: 학습 {result['protocol']['train_end']}회까지, 검증 {result['protocol']['validation_end']}회까지, 나머지 최종 평가",
             f"- 생존 단계별 개수: {result['survival_counts']}",
             f"- 생존 시드: {', '.join(row['seed'] for row in result['training_hits']) or '없음'}", ""]
    for key, label in (("validation", "검증"), ("final", "최종 평가")):
        row = result[key]
        lines.extend([f"## {label}", ""])
        if row["status"] != "evaluated":
            lines.extend(["생존 시드 없음", ""])
            continue
        lines.extend([f"- 회차: {row['draw_range']}, 그룹별 {row['tickets_per_group']:,}티켓",
                      f"- 생존군 1등: {row['selected_jackpots']}회",
                      f"- 티켓당 평균 일치 수: 생존군 {row['selected_mean_matches']:.6f}, 대조군 {row['baseline_mean_matches']:.6f}",
                      f"- 평균 차이: {row['difference']:+.6f}",
                      f"- 무작위 대조군 {row['baseline_repeats']}회 중 생존군 이상인 비율: {row['baseline_fraction_at_least_selected']:.1%}",
                      f"- 대조군 평균의 95% 참고 범위: {row['baseline_mean_reference_interval_95']}", ""])
    lines.extend(["## 방법과 한계", "",
                  "- 시드와 회차로 64비트 순열을 계산하고 조합 순번으로 변환합니다. 회차별 한 조합이며 이후 결과를 생성 입력에 넣지 않습니다.",
                  "- 64비트 값을 8,145,060으로 나눈 나머지를 쓰므로 미세한 모듈러 편향이 있습니다(균등분포와 총변동거리 상한 4.5e-13). 대조군에도 같은 생성기를 사용합니다.",
                  "- 대조군은 학습 성적으로 선별하지 않은 64비트 무작위 시드입니다. 각 반복에서 생존군과 같은 시드 수와 회차 수를 사용합니다.",
                  "- 생성기 버전, 소스 해시, Python/NumPy 버전, 원장 해시와 설정을 JSON에 보존했습니다. 생존 시드·학습 적중 회차·개별 평가 분포도 저장했습니다.",
                  "- 이전에 확인한 평가 구간을 재사용한 후향 탐색입니다. 실시간 미래 사전등록이나 확증 검정이 아니며 대조군 비율도 탐색 지표입니다.",
                  "- 학습 구간에서만 생존 시드를 선택했습니다. 생존군 전체를 평가하며 평가 성적으로 좋은 시드만 다시 고르지 않습니다.",
                  "- 역산 가능한 생성기에서 2회 적중 시드가 존재한다는 사실은 미래 예측력의 증거가 아닙니다.",
                  "- 원자료: 저장소 `data/lotto/draws.jsonl`의 회차별 출처 및 수집 시각. 공식 출처: [동행복권](https://www.dhlottery.co.kr/).", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--data", type=Path, default=root / "data/lotto/draws.jsonl")
    parser.add_argument("--output", type=Path, default=root / "data/lotto/reports/seed-expanded")
    parser.add_argument("--candidate-count", type=int, default=1_000_000)
    parser.add_argument("--train-end", type=int, default=740)
    parser.add_argument("--validation-end", type=int, default=990)
    parser.add_argument("--baseline-repeats", type=int, default=1000)
    parser.add_argument("--search-seed", type=int, default=20261015)
    parser.add_argument("--control-seed", type=int, default=64520261015)
    args = parser.parse_args(argv)
    draws = validate_draws(load_draws_jsonl(args.data))
    if not draws or not draws[0].draw_no < args.train_end < args.validation_end < draws[-1].draw_no:
        parser.error("학습·검증·최종 구간이 각각 필요합니다")
    if args.candidate_count < 1 or args.baseline_repeats < 1:
        parser.error("후보 수와 대조 반복은 양수여야 합니다")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("기존 결과를 보존하려면 새 --output 디렉터리를 지정하세요")
    training = [d for d in draws if d.draw_no <= args.train_end]
    validation = [d for d in draws if args.train_end < d.draw_no <= args.validation_end]
    final = [d for d in draws if d.draw_no > args.validation_end]
    protocol = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    protocol.update(version=VERSION, python=platform.python_version(), numpy=np.__version__,
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    training_hash=data_hash(training), ledger_hash=data_hash(draws),
                    anchor_draw=training[0].draw_no, min_wins=2,
                    interpretation="exploratory reuse of previously inspected holdout")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    selected, counts = select(training, args.candidate_count, args.search_seed, progress=True)
    controls = uniform_controls(selected, args.baseline_repeats, args.control_seed)
    training_hits = [{"seed": str(int(seed)), "draws": [d.draw_no for d in training
                     if int(ranks(np.array([seed]), d.draw_no)[0]) == combo_rank(d.numbers_sorted)]}
                     for seed in selected]
    selection = {"training_hits": training_hits, "survival_counts": counts,
                 "control_sha256": hashlib.sha256(controls.astype("<u8").tobytes()).hexdigest()}
    (args.output / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
    print(f"selected={len(selected)}; evaluating unchanged groups", flush=True)
    result = {"protocol": protocol, **selection, "selection_hash": sha256_json(selection),
              "validation": evaluate(selected, controls, validation),
              "final": evaluate(selected, controls, final)}
    (args.output / "experiment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.output / "report.md").write_text(report_markdown(result), encoding="utf-8")
    print(args.output / "report.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
