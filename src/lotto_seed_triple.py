"""Fixed-budget, checkpointed search for three training jackpots.

Uses the same SplitMix64 ticket generator as the preceding expanded experiment.
An affine permutation enumerates unique anchor preimages without large RAM use.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time

import numpy as np

from lotto645 import data_hash, load_draws_jsonl, sha256_json
from lotto_seed_expanded import (MASK, N, VERSION, combo_rank, evaluate, ranks,
                                 uniform_controls)


def native_scan(executable, training, start, count, offset, stride, workers):
    target = combo_rank(training[0].numbers_sorted)
    payload = f"{len(training)} {training[0].draw_no} {target} {start} {count} {offset} {stride} {workers}\n"
    payload += "".join(f"{d.draw_no} {combo_rank(d.numbers_sorted)}\n" for d in training)
    completed = subprocess.run([str(executable)], input=payload, text=True,
                               capture_output=True, check=True)
    result = json.loads(completed.stdout)
    if len(result["histogram"]) != len(training) + 1 or sum(result["histogram"]) != count:
        raise ValueError("native histogram does not account for every candidate")
    if result["histogram"][0] or sum(result["histogram"][3:]) != len(result["survivors"]):
        raise ValueError("native anchor or survivor count mismatch")
    return result


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def summary(result):
    p = result["protocol"]
    lines = ["# 로또 3회 이상 적중 시드 탐색", "",
             f"첫 학습 회차를 맞히는 조건부 후보 {p['candidate_count']:,}개를 검사했습니다.",
             f"생성기 `{VERSION}`와 학습 구간은 이전 100만 후보 실험과 같습니다.",
             "후보 열거는 거대한 무작위 목록 대신 서로 다른 역상들을 순회하는 고정 아핀 순열을 사용했습니다.",
             "첫 적중은 역산으로 만든 결과입니다. 무작위 시드 전수 탐색 수나 예측 성과로 해석하면 안 됩니다.", "",
             f"- 생존 단계별 개수: {result['survival_counts']}",
             f"- 학습: {p['anchor_draw']}~{p['train_end']}회; 검증: {p['train_end']+1}~{p['validation_end']}회; 최종: {p['validation_end']+1}~{p['last_draw']}회",
             f"- 실제 탐색 시간: {result['scan_seconds']:.2f}초", "",
             "## 생존 시드와 학습 적중 회차", ""]
    for row in result["training_hits"]:
        lines.append(f"- `{row['seed']}`: {row['draws']}")
    for key, label in (("validation", "검증"), ("final", "최종 평가")):
        row = result[key]
        lines.extend(["", f"## {label}", ""])
        if row["status"] == "no_survivors":
            lines.append("생존 시드가 없어 비교하지 않았습니다.")
            continue
        lines.extend([f"- 그룹별 {row['tickets_per_group']:,}티켓; 생존군 1등 {row['selected_jackpots']}회",
                      f"- 평균 일치: 생존군 {row['selected_mean_matches']:.6f}, 무작위 {row['baseline_mean_matches']:.6f}",
                      f"- 생존군−대조군 차이: {row['difference']:+.6f}",
                      f"- 무작위 {row['baseline_repeats']:,}개 그룹 중 생존군 이상 비율: {row['baseline_fraction_at_least_selected']:.2%}",
                      f"- 대조군 평균의 95% 참고 범위: {row['baseline_mean_reference_interval_95']}"])
        for item in row["per_seed"]:
            lines.append(f"- 시드 `{item['seed']}`: 평균 일치 {item['mean_matches']:.6f}, 1등 {item['histogram'][6]}회")
    lines.extend(["", "## 방법과 한계", "",
                  "- 후보 수·선별 기준·평가 구간·대조군 난수 시드를 실행 전에 protocol.json에 고정했습니다. 전체 후보를 검사한 뒤 3회 이상 시드 전부를 평가했습니다.",
                  "- 동일 생성기의 미선별 무작위 시드로 같은 회차와 같은 티켓 수의 대조군을 만들었습니다. 부분 일치는 설명용 지표이며 선별 조건은 6개 모두 일치입니다.",
                  "- 이전에 확인한 검증·최종 구간을 재사용한 후향 탐색입니다. 대조군 비율은 탐색 지표이며 새로운 미래 성능의 확증 검정이 아닙니다.",
                  "- 첫 적중을 구성한 조건부 시드이고, 역상 순회의 아핀 순열 자체는 무작위 표본이 아닙니다. 다른 생성기·후보 추출법에 결과를 일반화하지 않습니다.",
                  "- 64비트 순열의 조합 순번 변환에는 미세한 모듈러 편향이 있습니다(균등분포와 총변동거리 상한 4.5e-13). 대조군도 같은 변환을 사용합니다.",
                  "- 설정·소스·실행 파일·원장 해시와 배치별 기록을 JSON에 보존했습니다. 체크포인트 재개 시 같은 설정인지 확인하며 중복 계산을 집계하지 않습니다.",
                  "- 원자료는 data/lotto/draws.jsonl의 회차별 출처와 수집 시각을 따릅니다. 공식 출처: [동행복권](https://www.dhlottery.co.kr/).", ""])
    return "\n".join(lines)


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=root / "data/lotto/draws.jsonl")
    parser.add_argument("--output", type=Path, default=root / "data/lotto/reports/seed-triple")
    parser.add_argument("--candidate-count", type=int, default=1_000_000_000)
    parser.add_argument("--batch-size", type=int, default=100_000_000)
    parser.add_argument("--offset", type=int, default=20261015)
    parser.add_argument("--stride", type=int, default=1000003)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--train-end", type=int, default=740)
    parser.add_argument("--validation-end", type=int, default=990)
    parser.add_argument("--baseline-repeats", type=int, default=10000)
    parser.add_argument("--control-seed", type=int, default=64520261015)
    args = parser.parse_args(argv)
    draws = load_draws_jsonl(args.data)
    if not draws or not 0 < draws[0].draw_no < args.train_end < args.validation_end < draws[-1].draw_no:
        parser.error("three chronological nonempty splits required")
    training = [d for d in draws if d.draw_no <= args.train_end]
    population = (MASK - combo_rank(training[0].numbers_sorted)) // N + 1
    if not (1 <= args.candidate_count <= population and args.batch_size > 0 and
            1 <= args.workers <= 64 and args.baseline_repeats > 0 and
            0 <= args.offset < population and 0 < args.stride < population and
            math.gcd(args.stride, population) == 1):
        parser.error("invalid count, workers, baseline, or affine permutation")
    args.executable = args.executable.resolve()
    protocol = {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}
    protocol.update(version=VERSION, min_wins=3, anchor_draw=training[0].draw_no,
                    last_draw=draws[-1].draw_no, population=population,
                    python=platform.python_version(), numpy=np.__version__,
                    training_hash=data_hash(training), ledger_hash=data_hash(draws),
                    executable_sha256=hashlib.sha256(args.executable.read_bytes()).hexdigest(),
                    source_hashes={name: hashlib.sha256((root / "src" / name).read_bytes()).hexdigest()
                                   for name in ("lotto_seed_scan.cpp", "lotto_seed_triple.py", "lotto_seed_expanded.py")})
    args.output.mkdir(parents=True, exist_ok=True)
    protocol_path = args.output / "protocol.json"
    if protocol_path.exists():
        if json.loads(protocol_path.read_text()) != protocol:
            parser.error("checkpoint protocol differs; use a new output directory")
    elif any(args.output.iterdir()):
        parser.error("nonempty output directory without protocol")
    else:
        write_json(protocol_path, protocol)
    totals = np.zeros(len(training) + 1, dtype=np.int64)
    survivors, elapsed = [], 0.0
    for start in range(0, args.candidate_count, args.batch_size):
        count = min(args.batch_size, args.candidate_count - start)
        path = args.output / f"batch-{start:012d}.json"
        if path.exists():
            batch = json.loads(path.read_text())
            if batch["start"] != start or batch["count"] != count or batch["protocol_hash"] != sha256_json(protocol):
                raise ValueError("batch checkpoint does not match protocol")
        else:
            begin = time.perf_counter()
            batch = native_scan(args.executable, training, start, count, args.offset, args.stride, args.workers)
            batch.update(start=start, count=count, seconds=time.perf_counter()-begin,
                         protocol_hash=sha256_json(protocol))
            write_json(path, batch)
        if sum(batch["histogram"]) != count or sum(batch["histogram"][3:]) != len(batch["survivors"]):
            raise ValueError("checkpoint count mismatch")
        totals += np.array(batch["histogram"], dtype=np.int64)
        survivors.extend(batch["survivors"])
        elapsed += batch["seconds"]
        print(f"scanned={start+count:,}/{args.candidate_count:,}; triple={len(survivors)}; seconds={elapsed:.1f}", flush=True)
    selected = np.array(sorted(int(row["seed"]) for row in survivors), dtype=np.uint64)
    if len(set(map(int, selected))) != len(selected):
        raise ValueError("duplicate survivor")
    hits = [{"seed": str(int(s)), "draws": [d.draw_no for d in training
             if int(ranks([s], d.draw_no)[0]) == combo_rank(d.numbers_sorted)]} for s in selected]
    native_hits = {row["seed"]: row["hits"] for row in survivors}
    if any(len(row["draws"]) != native_hits[row["seed"]] or len(row["draws"]) < 3 for row in hits):
        raise ValueError("Python/native training hit mismatch")
    controls = uniform_controls(selected, args.baseline_repeats, args.control_seed)
    counts = {str(k): int(totals[k:].sum()) for k in range(1, max(4, int(np.flatnonzero(totals)[-1]))+1)}
    selection = {"training_hits": hits, "survival_counts": counts,
                 "control_sha256": hashlib.sha256(controls.astype("<u8").tobytes()).hexdigest()}
    write_json(args.output / "selection.json", selection)
    result = {"protocol": protocol, **selection, "selection_hash": sha256_json(selection),
              "scan_seconds": elapsed,
              "validation": evaluate(selected, controls, [d for d in draws if args.train_end < d.draw_no <= args.validation_end]),
              "final": evaluate(selected, controls, [d for d in draws if d.draw_no > args.validation_end])}
    write_json(args.output / "experiment.json", result)
    (args.output / "report.md").write_text(summary(result), encoding="utf-8")
    print(args.output / "report.md", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
