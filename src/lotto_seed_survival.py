"""Retrospective seed selection with chronologically untouched evaluation.

Each seed has one fixed stream: one uniform ticket per draw, starting at draw 1.
Never seed from outcomes or restart the stream at a split boundary.
"""
from __future__ import annotations

from collections import Counter
import platform
import random
from typing import Sequence

import numpy as np

from lotto645 import DrawRecord, data_hash, sha256_json, validate_draws

VERSION = "seed-survival-v1"


def tickets(seed: int, last_draw: int):
    rng = random.Random(f"{VERSION}|{seed}")
    for _ in range(last_draw):
        yield tuple(sorted(rng.sample(range(1, 46), 6)))


def match_counts(seed: int, draws: Sequence[DrawRecord]) -> list[int]:
    outcomes = {d.draw_no: set(d.numbers_sorted) for d in draws}
    return [len(outcomes[n].intersection(ticket))
            for n, ticket in enumerate(tickets(seed, draws[-1].draw_no), 1)
            if n in outcomes]


def _summary(counts: list[int]) -> dict:
    histogram = Counter(counts)
    return {"tickets": len(counts), "jackpots": histogram[6],
            "matches_histogram": {str(i): histogram[i] for i in range(7)},
            "mean_matches": sum(counts) / len(counts) if counts else None}


def _evaluate(seeds: list[int], draws: Sequence[DrawRecord], controls: list[list[int]]) -> dict:
    selected = _summary([m for seed in seeds for m in match_counts(seed, draws)])
    if not seeds:
        return {"status": "no_survivors", "selected": selected, "baseline": None}
    baseline = [_summary([m for seed in group for m in match_counts(seed, draws)])
                for group in controls]
    means = [row["mean_matches"] for row in baseline]
    return {"status": "evaluated", "selected": selected,
            "baseline": {"repetitions": len(controls), "tickets_per_repetition": selected["tickets"],
                         "mean_matches": float(np.mean(means)),
                         "mean_matches_reference_interval_95": np.quantile(means, [.025, .975]).tolist(),
                         "jackpots_total": sum(row["jackpots"] for row in baseline),
                         "runs": baseline},
            "mean_matches_difference": selected["mean_matches"] - float(np.mean(means))}


def run_seed_survival(draws: Sequence[DrawRecord], *, candidate_count: int = 10000,
                      train_end: int, validation_end: int, min_wins: int = 1,
                      baseline_repeats: int = 100, start_seed: int = 0) -> dict:
    draws = validate_draws(draws)
    if not draws or draws[0].draw_no < 1:
        raise ValueError("양의 회차 원장이 필요합니다")
    if candidate_count < 1 or baseline_repeats < 1 or min_wins < 1 or start_seed < 0:
        raise ValueError("후보 수·대조 반복·최소 적중은 양수, 시작 시드는 0 이상이어야 합니다")
    if not draws[0].draw_no <= train_end < validation_end < draws[-1].draw_no:
        raise ValueError("학습·검증·최종 평가 구간이 순서대로 각각 있어야 합니다")
    train = [d for d in draws if d.draw_no <= train_end]
    validation = [d for d in draws if train_end < d.draw_no <= validation_end]
    final = [d for d in draws if d.draw_no > validation_end]
    config = dict(version=VERSION, python_version=platform.python_version(),
                  candidate_count=candidate_count, start_seed=start_seed,
                  train_end=train_end, validation_end=validation_end,
                  min_wins=min_wins, baseline_repeats=baseline_repeats,
                  tickets_per_seed_per_draw=1)
    wins = {}
    for seed in range(start_seed, start_seed + candidate_count):
        count = match_counts(seed, train).count(6)
        if count:
            wins[seed] = count
    stages = [{"min_wins": k, "seeds": [s for s, n in wins.items() if n >= k]}
              for k in range(1, max(3, min_wins, max(wins.values(), default=0)) + 1)]
    selected = stages[min_wins - 1]["seeds"]
    # Disjoint, unselected seed range fixed before examining evaluation outcomes.
    control_start = start_seed + candidate_count
    controls = [list(range(control_start + i * len(selected), control_start + (i + 1) * len(selected)))
                for i in range(baseline_repeats)]
    selection = {"config": config, "training_data_hash": data_hash(train),
                 "stages": stages, "selected_seeds": selected, "control_seed_groups": controls}
    return {"config": config, "data_hash": data_hash(draws),
            "selection": selection, "selection_hash": sha256_json(selection),
            "training_jackpot_counts": {str(s): n for s, n in wins.items()},
            "split_ranges": {"train": [train[0].draw_no, train[-1].draw_no],
                             "validation": [validation[0].draw_no, validation[-1].draw_no],
                             "final": [final[0].draw_no, final[-1].draw_no]},
            "validation": _evaluate(selected, validation, controls),
            "final": _evaluate(selected, final, controls),
            "limitations": ["과거 자료를 나눈 후향 실험이며 실제 미래 사전등록이 아닙니다.",
                            "검증·최종 결과로 시드나 규칙을 재선택하지 않습니다. 재실행·튜닝 시 새 최종 구간이 필요합니다.",
                            "대조군 구간은 평균 일치 수의 경험적 참고 범위이며 예측 우위의 유의성 검정이 아닙니다.",
                            "한 시드당 회차별 한 조합입니다. 시드 간 중복 조합도 동일 비용의 별도 티켓으로 계산합니다.",
                            "부분 일치는 설명용 지표이며 생존 조건은 6개 일치입니다."]}


def markdown_report(report: dict) -> str:
    cfg = report["config"]
    lines = ["# 로또 시드 생존 실험", "", f"- 후보: {cfg['candidate_count']:,}개, 시드당 회차별 1조합",
             f"- 생존 기준: 학습 구간 1등 {cfg['min_wins']}회 이상",
             f"- 구간: {report['split_ranges']}",
             f"- 선별 해시: `{report['selection_hash']}`", "",
             "## 학습 구간 선별", ""]
    for stage in report["selection"]["stages"]:
        lines.append(f"- {stage['min_wins']}회 이상: {len(stage['seeds'])}개 — {stage['seeds']}")
    for key, label in (("validation", "검증"), ("final", "최종 평가")):
        result = report[key]
        lines.extend(["", f"## {label}", ""])
        if result["status"] == "no_survivors":
            lines.append("생존 시드가 없어 비교하지 않았습니다.")
            continue
        selected, baseline = result["selected"], result["baseline"]
        lines.extend([f"- 생존군: {selected['tickets']:,}티켓, 1등 {selected['jackpots']}회",
                      f"- 티켓당 평균 일치: 생존군 {selected['mean_matches']:.6f}, 무작위 대조군 {baseline['mean_matches']:.6f}",
                      f"- 평균 일치 차이: {result['mean_matches_difference']:+.6f}",
                      f"- 대조군: 동일 티켓 수로 {baseline['repetitions']}회 반복",
                      f"- 대조군 평균 일치의 95% 참고 범위: {baseline['mean_matches_reference_interval_95']}"])
    lines.extend(["", "## 방법과 한계", ""] + [f"- {s}" for s in report["limitations"]])
    lines.extend(["", "원자료: `data/lotto/draws.jsonl`의 회차별 출처·수집 시각을 보존했습니다.",
                  "공식 출처: [동행복권](https://www.dhlottery.co.kr/)",
                  "전체 설정·시드·일치 분포·데이터 해시는 함께 생성한 JSON에 저장됩니다.", ""])
    return "\n".join(lines)
