"""Training-only paired 128-bit seed construction, with matched-sharing controls.

High 64 bits generate odd rounds; low 64 bits generate even rounds. Every full
seed generates exactly one ticket per round. Both halves must win >=2 times in
their own training rounds. All surviving Cartesian pairs are evaluated.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import multiprocessing
from pathlib import Path
import platform
import time

import numpy as np

from lotto645 import data_hash, load_draws_jsonl, sha256_json, validate_draws
from lotto_seed_expanded import (MASK, combo_rank, popcount, ranks, select,
                                 ticket_masks, uniform_controls)

VERSION = "splitmix64-paired128-v1"


def encode_seed(odd, even):
    if not 0 <= int(odd) <= MASK or not 0 <= int(even) <= MASK:
        raise ValueError("seed halves must fit uint64")
    return f"{int(odd):016x}{int(even):016x}"


def decode_seed(value):
    if len(value) != 32 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("128-bit seed must contain exactly 32 hex digits")
    return int(value[:16], 16), int(value[16:], 16)


def ticket(seed_hex, draw_no):
    odd, even = decode_seed(seed_hex)
    if draw_no < 1:
        raise ValueError("positive draw number required")
    mask = int(ticket_masks(ranks([odd if draw_no % 2 else even], draw_no))[0])
    return tuple(n for n in range(1, 46) if mask & (1 << (n-1)))


def select_halves(training, count, odd_search_seed, even_search_seed, workers=1):
    training = validate_draws(training)
    odd_draws = [d for d in training if d.draw_no % 2]
    even_draws = [d for d in training if not d.draw_no % 2]
    if len(odd_draws) < 2 or len(even_draws) < 2 or count < 1:
        raise ValueError("each parity needs at least two training rounds and a positive candidate count")
    if workers not in (1, 2):
        raise ValueError("workers must be 1 or 2")
    if workers == 2:
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            odd_job = pool.submit(select, odd_draws, count, odd_search_seed, progress=True)
            even_job = pool.submit(select, even_draws, count, even_search_seed, progress=True)
            odd, odd_counts = odd_job.result()
            even, even_counts = even_job.result()
    else:
        odd, odd_counts = select(odd_draws, count, odd_search_seed, progress=True)
        even, even_counts = select(even_draws, count, even_search_seed, progress=True)
    odd, even = np.sort(odd), np.sort(even)
    def hits(seeds, rows):
        return [{"half_seed": str(int(seed)), "draws": [d.draw_no for d in rows
                 if int(ranks([seed], d.draw_no)[0]) == combo_rank(d.numbers_sorted)]}
                for seed in seeds]
    oh, eh = hits(odd, odd_draws), hits(even, even_draws)
    pairs = [{"seed_hex": encode_seed(o["half_seed"], e["half_seed"]),
              "training_jackpot_draws": sorted(o["draws"] + e["draws"])} for o in oh for e in eh]
    return odd, even, {"odd_survival_counts": odd_counts, "even_survival_counts": even_counts,
                       "odd_halves": oh, "even_halves": eh, "pairs": pairs,
                       "minimum_training_hits_per_half": 2,
                       "pairing": "all Cartesian pairs; no evaluation-based selection"}


def half_histograms(seeds, draws):
    seeds = np.asarray(seeds, dtype=np.uint64).reshape(-1)
    hist = np.zeros((len(seeds), 7), dtype=np.int64)
    indices = np.arange(len(seeds))
    for draw in draws:
        target = np.uint64(sum(1 << (n-1) for n in draw.numbers_sorted))
        matches = popcount(ticket_masks(ranks(seeds, draw.draw_no)) & target)
        hist[indices, matches] += 1
    return hist


def evaluate(odd, even, odd_controls, even_controls, draws):
    a, b = len(odd), len(even)
    if not a or not b:
        return {"status": "no_survivors"}
    repeats = len(odd_controls)
    if odd_controls.shape != (repeats, a) or even_controls.shape != (repeats, b):
        raise ValueError("controls must preserve both half counts and sharing structure")
    odd_rows = [d for d in draws if d.draw_no % 2]
    even_rows = [d for d in draws if not d.draw_no % 2]
    oh = half_histograms(odd, odd_rows)
    eh = half_histograms(even, even_rows)
    # Every odd stream is repeated b times and every even stream a times.
    selected = b * oh.sum(axis=0) + a * eh.sum(axis=0)
    oc = half_histograms(odd_controls, odd_rows).reshape(repeats, a, 7)
    ec = half_histograms(even_controls, even_rows).reshape(repeats, b, 7)
    baseline = b * oc.sum(axis=1) + a * ec.sum(axis=1)
    tickets = a * b * len(draws)
    sums = baseline @ np.arange(7)
    means = sums / tickets
    observed = int(selected @ np.arange(7))
    # Single-pair reference for descriptive individual comparisons. Components
    # come from the same unselected control pool, one pair per replicate.
    individual_sums = (oc[:, 0] + ec[:, 0]) @ np.arange(7)
    # The best Cartesian pair combines the best odd/even components. Compare
    # maxima against equally searched groups, not one arbitrary random seed.
    selected_max = int((oh @ np.arange(7)).max() + (eh @ np.arange(7)).max())
    control_max = (oc @ np.arange(7)).max(axis=1) + (ec @ np.arange(7)).max(axis=1)
    per_pair = []
    for i, o in enumerate(odd):
        for j, e in enumerate(even):
            hist = oh[i] + eh[j]
            score = int(hist @ np.arange(7))
            per_pair.append({"seed_hex": encode_seed(o, e), "histogram": hist.tolist(),
                             "mean_matches": score / len(draws),
                             "individual_control_fraction_at_least_selected": float(np.mean(individual_sums >= score))})
    return {"status": "evaluated", "draw_range": [draws[0].draw_no, draws[-1].draw_no],
            "pair_count": a*b, "odd_half_count": a, "even_half_count": b,
            "tickets_per_group": tickets, "distinct_stream_tickets": a*len(odd_rows)+b*len(even_rows),
            "selected_histogram": selected.tolist(), "selected_jackpots": int(selected[6]),
            "distinct_stream_jackpots": int(oh[:, 6].sum()+eh[:, 6].sum()),
            "selected_mean_matches": observed / tickets,
            "baseline_mean_matches": float(means.mean()),
            "difference": observed / tickets - float(means.mean()),
            "baseline_repeats": repeats, "baseline_histograms": baseline.tolist(),
            "baseline_reference_interval_95": np.quantile(means, [.025, .975]).tolist(),
            "baseline_fraction_at_least_selected": float(np.mean(sums >= observed)),
            "individual_reference_interval_95": np.quantile(individual_sums/len(draws), [.025, .975]).tolist(),
            "best_pair_comparison": {"selected_mean_matches": selected_max/len(draws),
                                     "baseline_best_mean_matches": float(control_max.mean()/len(draws)),
                                     "baseline_best_reference_interval_95": np.quantile(control_max/len(draws), [.025, .975]).tolist(),
                                     "fraction_control_group_best_at_least_selected_best": float(np.mean(control_max >= selected_max)),
                                     "interpretation": "descriptive maximum comparison across equally sized shared Cartesian groups; no reselection"},
            "per_pair": per_pair}


def evaluate_splits(odd, even, oc, ec, validation, final, workers=1):
    if workers not in (1, 2):
        raise ValueError("workers must be 1 or 2")
    if workers == 1:
        return {"validation": evaluate(odd, even, oc, ec, validation),
                "final": evaluate(odd, even, oc, ec, final)}
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        jobs = {key: pool.submit(evaluate, odd, even, oc, ec, rows)
                for key, rows in (("validation", validation), ("final", final))}
        return {key: job.result() for key, job in jobs.items()}


def markdown(result):
    p, s = result["protocol"], result["selection"]
    lines = ["# 128비트 로또 시드 실험", "",
             f"- 생성기: `{VERSION}`. 상위 64비트는 홀수 회차, 하위 64비트는 짝수 회차에 사용합니다.",
             f"- 각 부분의 조건부 후보 {p['candidates_per_half']:,}개씩을 검사했습니다.",
             f"- 홀수 부분 생존 {len(s['odd_halves'])}개, 짝수 부분 생존 {len(s['even_halves'])}개; 결합 시드 {len(s['pairs']):,}개",
             "- 각 부분은 담당 학습 회차에서 2회 이상 적중해야 합니다. 생존 부분의 모든 조합을 평가합니다.",
             f"- 학습 {p['first_draw']}~{p['train_end']}회, 검증 {p['train_end']+1}~{p['validation_end']}회, 최종 {p['validation_end']+1}~{p['last_draw']}회", ""]
    if s["pairs"]:
        distribution = {}
        for pair in s["pairs"]:
            n = len(pair["training_jackpot_draws"])
            distribution[str(n)] = distribution.get(str(n), 0)+1
        lines.extend([f"학습 적중 횟수별 시드 수: {distribution}", "", "생존 시드 예시(전체 목록은 selection.json):", ""])
        for row in s["pairs"][:5]:
            lines.append(f"- `{row['seed_hex']}`: {row['training_jackpot_draws']}")
    for key, label in (("validation", "검증"), ("final", "최종 평가")):
        row = result[key]
        lines.extend(["", f"## {label}", ""])
        if row["status"] == "no_survivors":
            lines.append("생존 시드가 없어 평가하지 않았습니다.")
            continue
        lines.extend([f"- 그룹별 {row['tickets_per_group']:,}티켓, 공유를 제외한 스트림별 티켓 {row['distinct_stream_tickets']:,}개",
                      f"- 생존군 1등 {row['selected_jackpots']}티켓(서로 다른 스트림의 적중 {row['distinct_stream_jackpots']}건)",
                      f"- 평균 일치: 생존군 {row['selected_mean_matches']:.6f}, 무작위 {row['baseline_mean_matches']:.6f}",
                      f"- 차이: {row['difference']:+.6f}",
                      f"- 같은 공유 구조의 무작위 {row['baseline_repeats']:,}개 그룹 중 생존군 이상 비율: {row['baseline_fraction_at_least_selected']:.2%}",
                      f"- 대조군 평균의 95% 참고 범위: {row['baseline_reference_interval_95']}",
                      f"- 최고 시드 평균 일치: {row['best_pair_comparison']['selected_mean_matches']:.6f}; 대조군에서도 같은 수 중 최고를 고르면 이 이상인 비율 {row['best_pair_comparison']['fraction_control_group_best_at_least_selected_best']:.2%}"])
    lines.extend(["", "## 방법과 한계", "",
                  "- 첫 홀수·짝수 학습 회차의 적중은 각각 역산으로 구성했습니다. 두 적중은 예측 성과가 아닙니다. 나머지 적중도 과거 학습 기록으로 선별했습니다.",
                  "- 128비트로 늘린 것 자체가 미래 예측력을 높이지 않습니다. 이전 64비트 생성기와 구분되는 별도 실험입니다.",
                  "- 각 부분의 후보 집합을 결합할 수 있지만 그 곱만큼 독립 후보를 실제 전수 검사한 것은 아닙니다. 이 실험은 각 부분에서 2회 이상인 조건만 탐색하며 1+3회 등의 다른 4회 적중 패턴을 전부 탐색하지 않습니다.",
                  "- 결합 시드들은 홀수 또는 짝수 부분을 공유합니다. 이를 독립 표본처럼 계산하지 않고 대조군에도 같은 부분 개수와 전체 결합 구조를 적용했습니다. 중복된 조합을 동일 비용의 별도 티켓으로 집계합니다.",
                  "- 이전에 확인한 평가 구간을 재사용한 후향 탐색입니다. 대조군 비율은 탐색 지표이며 미래 성능의 확증 검정이나 수익 예측이 아닙니다.",
                  "- 개별 시드 비교도 설명용입니다. 결과를 보고 성적 좋은 시드를 다시 고르지 않았습니다.",
                  "- 조합 순번의 미세한 모듈러 편향(총변동거리 상한 4.5e-13)은 기존 생성기와 같으며 대조군에도 적용됩니다.",
                  "- 전체 설정·원장·소스·시드·학습 적중 회차·일치 분포를 JSON에 보존했습니다. 원자료: data/lotto/draws.jsonl의 회차별 출처·수집 시각. 공식 출처: [동행복권](https://www.dhlottery.co.kr/).", ""])
    return "\n".join(lines)


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root/"data/lotto/draws.jsonl")
    parser.add_argument("--output", type=Path, default=root/"data/lotto/reports/seed-128")
    parser.add_argument("--candidates-per-half", type=int, default=1_000_000)
    parser.add_argument("--train-end", type=int, default=740)
    parser.add_argument("--validation-end", type=int, default=990)
    parser.add_argument("--baseline-repeats", type=int, default=10000)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2,
                        help="1=serial, 2=parallel halves and independent evaluation splits")
    parser.add_argument("--odd-search-seed", type=int, default=20261015)
    parser.add_argument("--even-search-seed", type=int, default=20261016)
    parser.add_argument("--odd-control-seed", type=int, default=12820261015)
    parser.add_argument("--even-control-seed", type=int, default=12820261016)
    args = parser.parse_args(argv)
    draws = load_draws_jsonl(args.data)
    if not draws or not 0 < draws[0].draw_no < args.train_end < args.validation_end < draws[-1].draw_no:
        parser.error("three ordered nonempty splits required")
    if args.candidates_per_half < 1 or args.baseline_repeats < 1:
        parser.error("positive candidate count and baseline repeats required")
    if args.odd_control_seed == args.even_control_seed:
        parser.error("use separate control random streams for the two halves")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("use a new output directory to preserve previous results")
    train = [d for d in draws if d.draw_no <= args.train_end]
    if min(sum(d.draw_no%2==parity for d in train) for parity in (0,1)) < 2:
        parser.error("at least two training rounds per parity required")
    protocol = {k: str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    protocol.update(version=VERSION, first_draw=draws[0].draw_no, last_draw=draws[-1].draw_no,
                    min_wins_per_half=2, minimum_pair_wins=4,
                    python=platform.python_version(), numpy=np.__version__,
                    training_hash=data_hash(train), ledger_hash=data_hash(draws),
                    source_hashes={name: hashlib.sha256((root/"src"/name).read_bytes()).hexdigest()
                                   for name in ("lotto_seed_128.py", "lotto_seed_expanded.py")})
    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output/"protocol.json", protocol)
    started = time.perf_counter()
    odd, even, selection = select_halves(train, args.candidates_per_half, args.odd_search_seed,
                                       args.even_search_seed, workers=args.workers)
    selection_seconds = time.perf_counter()-started
    oc = uniform_controls(odd, args.baseline_repeats, args.odd_control_seed)
    ec = uniform_controls(even, args.baseline_repeats, args.even_control_seed)
    selection["control_sha256"] = hashlib.sha256(oc.astype("<u8").tobytes()+ec.astype("<u8").tobytes()).hexdigest()
    save(args.output/"selection.json", selection)
    print(f"odd={len(odd)}, even={len(even)}, pairs={len(odd)*len(even)}; evaluating all", flush=True)
    evaluation_started = time.perf_counter()
    evaluated = evaluate_splits(odd, even, oc, ec,
                               [d for d in draws if args.train_end < d.draw_no <= args.validation_end],
                               [d for d in draws if d.draw_no > args.validation_end], workers=args.workers)
    result = {"protocol": protocol, "selection": selection, "selection_hash": sha256_json(selection),
              **evaluated, "timings_seconds": {"selection": selection_seconds,
                                                "evaluation": time.perf_counter()-evaluation_started,
                                                "computation_total": time.perf_counter()-started}}
    save(args.output/"experiment.json", result)
    (args.output/"report.md").write_text(markdown(result), encoding="utf-8")
    print(args.output/"report.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
