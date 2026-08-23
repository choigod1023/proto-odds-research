"""로또 6/45 연구 파이프라인 CLI.

예시:
  python src/lotto_pipeline.py all --budget 10000
  python src/lotto_pipeline.py audit --simulations 1000
  python src/lotto_pipeline.py generate --target-draw 1239 --budget 5000

번호 구매 기능은 없다. 이 도구는 공식 데이터 수집, 사전등록, 평가만 수행한다.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np

from lotto645 import (
    KST,
    MODEL_VERSION,
    data_hash,
    generate_portfolio,
    load_draws_jsonl,
    make_preregistration,
    randomness_audit,
    save_draws_jsonl,
    sha256_json,
    walk_forward_backtest,
    write_preregistration,
)
from lotto_collect import OFFICIAL_RESULT_PAGE, collect_official_draws


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "lotto" / "draws.jsonl"
DEFAULT_REPORT_DIR = ROOT / "data" / "lotto" / "reports"
DEFAULT_PREREG_DIR = ROOT / "data" / "lotto" / "preregistrations"
DEFAULT_PUBLIC = ROOT / "docs" / "data" / "lotto_latest.json"


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_native(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_json(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_native(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _model_code_hash() -> str:
    digest = hashlib.sha256()
    for name in ("lotto645.py", "lotto_collect.py", "lotto_pipeline.py"):
        path = ROOT / "src" / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()

def _git_commit() -> str | None:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        model_status = subprocess.check_output(
            ["git", "status", "--porcelain", "--", "src/lotto645.py", "src/lotto_collect.py", "src/lotto_pipeline.py"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return head + ("+working-tree" if model_status else "")
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_or_collect(path: Path, *, refresh: bool, last_draw: int | None, workers: int) -> list:
    if refresh or not path.exists():
        print("공식 동행복권 원장을 수집합니다…", flush=True)
        draws = collect_official_draws(last_draw=last_draw, workers=workers)
        save_draws_jsonl(draws, path)
        print(f"원장 저장: {path} ({len(draws)}회)")
        return draws
    return load_draws_jsonl(path)


def _seed(target_draw: int, cutoff_draw: int, ledger_hash: str) -> str:
    # 결과를 본 뒤 마음에 드는 난수만 다시 고르지 못하도록 입력에서 결정한다.
    return f"{MODEL_VERSION}|target={target_draw}|cutoff={cutoff_draw}|data={ledger_hash}"


def run_audit(draws: list, *, simulations: int, report_dir: Path) -> dict[str, Any]:
    report = randomness_audit(draws, simulations=simulations)
    report["data_hash"] = data_hash(draws)
    report["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    _write_json(report, report_dir / "audit.json")
    return report


def run_backtest(draws: list, *, min_train: int, report_dir: Path) -> dict[str, Any]:
    report = walk_forward_backtest(draws, min_train=min_train)
    report["data_hash"] = data_hash(draws)
    report["generated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    _write_json(report, report_dir / "backtest.json")
    return report


def run_generate(
    draws: list, *, audit: dict[str, Any], backtest: dict[str, Any], target_draw: int | None,
    budget: int, candidate_pool: int, uncertainty_samples: int, prereg_dir: Path,
    public_path: Path, preregister: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], Path | None]:
    target = target_draw or (draws[-1].draw_no + 1)
    if target <= draws[-1].draw_no:
        raise ValueError("예측 대상 회차는 데이터 마감 회차보다 뒤여야 합니다")
    ledger_hash = data_hash(draws)
    seed_text = _seed(target, draws[-1].draw_no, ledger_hash)
    portfolio = generate_portfolio(
        draws,
        backtest,
        target_draw_no=target,
        budget_won=budget,
        seed_text=seed_text,
        candidate_pool=candidate_pool,
        uncertainty_samples=uncertainty_samples,
    )
    prereg = make_preregistration(
        draws, portfolio, backtest, audit, code_commit=_git_commit(),
        model_code_hash=_model_code_hash()
    )
    prereg_path = write_preregistration(prereg, prereg_dir) if preregister else None
    # 같은 입력 재실행은 기존 사전등록을 재사용하고 공개 JSON도 실제 고정본을 가리킨다.
    if prereg_path:
        prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    public = {
        "schema_version": 1,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "official_source": OFFICIAL_RESULT_PAGE,
        "data_cutoff_draw_no": draws[-1].draw_no,
        "data_cutoff_draw_date": draws[-1].draw_date,
        "draw_count": len(draws),
        "data_hash": ledger_hash,
        "model_version": MODEL_VERSION,
        "audit": audit,
        "backtest": backtest,
        "portfolio": portfolio,
        "preregistration": prereg,
        "preregistration_file": str(prereg_path.relative_to(ROOT)).replace("\\", "/") if prereg_path else None,
        "warning": (
            "로또는 공정하다면 모든 고유 조합의 1등 확률이 같습니다. "
            "검증 관문을 통과하지 못한 모델은 번호 가중치를 0으로 되돌립니다."
        ),
    }
    _write_json(public, public_path)
    return portfolio, prereg, prereg_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="로또 6/45 균등감사·워크포워드·사전등록")
    parser.add_argument("--data", type=Path, default=DEFAULT_LEDGER)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="공식 원장 수집")
    collect.add_argument("--last-draw", type=int)
    collect.add_argument("--workers", type=int, default=3)

    audit = sub.add_parser("audit", help="균등 비복원 무작위성 감사")
    audit.add_argument("--simulations", type=int, default=250)
    audit.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    backtest = sub.add_parser("backtest", help="확장창 워크포워드 검증")
    backtest.add_argument("--min-train", type=int, default=300)
    backtest.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)

    generate = sub.add_parser("generate", help="검증 관문을 적용한 고유 조합 생성·사전등록")
    generate.add_argument("--target-draw", type=int)
    generate.add_argument("--budget", type=int, default=5_000)
    generate.add_argument("--candidate-pool", type=int, default=5_000)
    generate.add_argument("--uncertainty-samples", type=int, default=192)
    generate.add_argument("--simulations", type=int, default=250)
    generate.add_argument("--min-train", type=int, default=300)
    generate.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    generate.add_argument("--prereg-dir", type=Path, default=DEFAULT_PREREG_DIR)
    generate.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    generate.add_argument("--no-preregister", action="store_true")

    all_cmd = sub.add_parser("all", help="수집→감사→백테스트→조합→사전등록")
    all_cmd.add_argument("--refresh", action="store_true")
    all_cmd.add_argument("--last-draw", type=int)
    all_cmd.add_argument("--workers", type=int, default=3)
    all_cmd.add_argument("--target-draw", type=int)
    all_cmd.add_argument("--budget", type=int, default=5_000)
    all_cmd.add_argument("--candidate-pool", type=int, default=5_000)
    all_cmd.add_argument("--uncertainty-samples", type=int, default=192)
    all_cmd.add_argument("--simulations", type=int, default=250)
    all_cmd.add_argument("--min-train", type=int, default=300)
    all_cmd.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    all_cmd.add_argument("--prereg-dir", type=Path, default=DEFAULT_PREREG_DIR)
    all_cmd.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    all_cmd.add_argument("--no-preregister", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        draws = collect_official_draws(last_draw=args.last_draw, workers=args.workers)
        save_draws_jsonl(draws, args.data)
        print(f"{len(draws)}회 저장 · cutoff={draws[-1].draw_no} · hash={data_hash(draws)}")
        return 0

    if not args.data.exists():
        raise FileNotFoundError(f"원장이 없습니다. 먼저 collect 또는 all --refresh를 실행하세요: {args.data}")
    draws = load_draws_jsonl(args.data)
    if args.command == "audit":
        report = run_audit(draws, simulations=args.simulations, report_dir=args.report_dir)
        print(json.dumps(_native(report), ensure_ascii=False, indent=2))
        return 0
    if args.command == "backtest":
        report = run_backtest(draws, min_train=args.min_train, report_dir=args.report_dir)
        print(json.dumps(_native(report), ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate":
        audit = run_audit(draws, simulations=args.simulations, report_dir=args.report_dir)
        backtest = run_backtest(draws, min_train=args.min_train, report_dir=args.report_dir)
        portfolio, prereg, path = run_generate(
            draws, audit=audit, backtest=backtest, target_draw=args.target_draw,
            budget=args.budget, candidate_pool=args.candidate_pool,
            uncertainty_samples=args.uncertainty_samples, prereg_dir=args.prereg_dir,
            public_path=args.public, preregister=not args.no_preregister,
        )
        print(json.dumps(_native({"portfolio": portfolio, "preregistration": prereg,
                                 "file": str(path) if path else None}), ensure_ascii=False, indent=2))
        return 0

    # all: 이 분기는 원장이 없을 때도 수집해야 하므로 위의 존재 확인보다 먼저 와야 한다.
    raise AssertionError("unreachable")


def all_main(argv: list[str] | None = None) -> int:
    """`all`은 원장이 없어도 동작해야 해서 main의 공통 검사와 분리한다."""
    args = build_parser().parse_args(argv)
    if args.command != "all":
        return main(argv)
    draws = _load_or_collect(args.data, refresh=args.refresh, last_draw=args.last_draw, workers=args.workers)
    audit = run_audit(draws, simulations=args.simulations, report_dir=args.report_dir)
    backtest = run_backtest(draws, min_train=args.min_train, report_dir=args.report_dir)
    portfolio, prereg, path = run_generate(
        draws, audit=audit, backtest=backtest, target_draw=args.target_draw,
        budget=args.budget, candidate_pool=args.candidate_pool,
        uncertainty_samples=args.uncertainty_samples, prereg_dir=args.prereg_dir,
        public_path=args.public, preregister=not args.no_preregister,
    )
    print(f"완료: {portfolio['target_draw_no']}회 · {portfolio['model_status']} · {portfolio['ticket_count']}조합")
    print(f"예측 해시: {prereg['prediction_hash']}")
    if path:
        print(f"사전등록: {path}")
    print(f"공개 JSON: {args.public}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(all_main())
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
