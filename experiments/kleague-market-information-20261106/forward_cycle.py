"""Freeze newly offered K League fixtures and report official prospective scores."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import numpy as np
import requests

from score_pilot import collect, settle
from study import encoded, freeze, scores, source_blob

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
BETMAN = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"
START = "<!-- prospective-results-start -->"
END = "<!-- prospective-results-end -->"
SEASON = 2026


def load_parser(directory):
    path = directory / "kleague_replay.py"
    path.write_bytes(source_blob(path.name))
    spec = importlib.util.spec_from_file_location("frozen_quote_parser", path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    return parser


def capture(directory, parser, year, round_no):
    round_id = (year % 100) * 10000 + round_no
    params = {"gmId": "G101", "gmTs": str(round_id), "gameYear": str(year), "_sbmInfo": {"debugMode": "false"}}
    headers = {"Referer": "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do",
               "X-Requested-With": "XMLHttpRequest"}
    with requests.post(BETMAN, json=params, headers=headers, timeout=(5, 10), stream=True,
                       allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"Betman round {round_id} returned HTTP {response.status_code}")
        raw = response.raw.read(4_000_001, decode_content=True)
    receipt = datetime.now(timezone.utc)
    if len(raw) > 4_000_000:
        raise ValueError("Betman response exceeded size limit")
    doc = json.loads(raw)
    if (doc.get("currentLottery") or {}).get("saleProgress") is not True:
        return None
    meta = {"request_json": params, "timestamp_basis": "http_response_body_completed", "http_status": 200,
            "received_at": receipt.isoformat(), "body_sha256": hashlib.sha256(raw).hexdigest(),
            "body_file": "betman.body.gz"}
    (directory / meta["body_file"]).write_bytes(gzip.compress(raw, mtime=0))
    metadata = directory / "betman.json"
    metadata.write_bytes(encoded(meta))
    return parser.pregame_snapshot(metadata)


def schedule_key(row):
    date = datetime.fromisoformat(row["kickoff_kst"])
    return int(row["league"][1:]), date.year, date.month


def link_new(snapshot, get_schedule, known):
    linked = []
    for row in snapshot["fixtures"]:
        division, year, month = schedule_key(row)
        if year != SEASON:
            continue
        _, schedule = get_schedule(division, year, month)
        clock = row["kickoff_kst"][:16]
        matches = [r for r in schedule if
                   (r["leagueId"], r["homeTeam"], r["awayTeam"],
                    f"{r['gameDate'].replace('.', '-')}T{r['gameTime']}") ==
                   (division, row["home_team_id"], row["away_team_id"], clock)]
        if len(matches) != 1 or matches[0]["endYn"] == "Y":
            continue
        key = f"kleague:{year}:{division}:{matches[0]['gameId']}"
        if key not in known:
            linked.append(row | {"official_fixture_id": key})
    return linked


def write_atomic(path, raw):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def update_manuscript(path, rounds, waiting, rows):
    n = len(rows)
    lines = [START, "", "### 경기 전 검증 누적 결과", "",
             f"예측을 동결한 경기 {n + waiting}개, 공식 정산 {n}개, 대기 {waiting}개, 수집 회차 {rounds}개."]
    if rows:
        baseline = -float(scores(rows, "winner_implied")["log_score"].mean())
        three = -float(scores(rows, "poisson_three")["log_score"].mean())
        lines += ["", f"승무패 단독 로그손실 {baseline:.5f}, 세 시장 로그손실 {three:.5f}. "
                  f"세 시장에서 단독을 뺀 차이는 경기당 {three - baseline:+.5f} nat다. "
                  "정산을 마친 회차의 누적 기술 통계다."]
    lines += ["", END]
    text = path.read_text(encoding="utf-8")
    before, marker, rest = text.partition(START)
    _, close, after = rest.partition(END)
    if not marker or not close:
        raise ValueError("Manuscript result markers are missing")
    updated = before + "\n".join(lines) + after
    if text != updated:
        write_atomic(path, updated.encode("utf-8"))


def run(state, hints, manuscript):
    state.mkdir(parents=True, exist_ok=True)
    sources = [HERE / "prospective-260132.json", *sorted(state.glob("capture-*/forecast.json"))]
    frozen = [(path, json.loads(path.read_bytes())) for path in sources]
    known = {r["official_fixture_id"] for _, batch in frozen for r in batch["fixtures"]}
    now = datetime.now(KST)
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    latest = max(batch["round"] for _, batch in frozen) % 10000
    with TemporaryDirectory() as temp:
        scratch = Path(temp)
        official = {}

        def get_schedule(division, year, month):
            key = division, year, month
            if key not in official:
                dest = scratch / f"schedule-{year}-{month:02d}"
                dest.mkdir(exist_ok=True)
                official[key] = collect(dest, division, year, month)
            return official[key]

        failures = []
        if now.year == SEASON:
            hint_rows = json.loads(hints.read_bytes())["rounds_requested"] if hints.is_file() else []
            # The local collector supplies open-round hints. Probe the successor
            # as well so a newly issued round can be seen before that feed updates.
            rounds = {(int(y), int(r)) for y, r in hint_rows if int(y) == SEASON and int(r) >= latest}
            rounds.add((SEASON, latest + 1))
            parser = load_parser(scratch)
            for year, round_no in sorted(rounds)[-2:]:
                directory = scratch / str(round_no)
                directory.mkdir()
                try:
                    snapshot = capture(directory, parser, year, round_no)
                    linked = link_new(snapshot, get_schedule, known) if snapshot else []
                    if linked:
                        target = state / f"capture-{stamp}-{snapshot['round']}"
                        temporary = state / f".capture-{stamp}-{snapshot['round']}"
                        shutil.copytree(directory, temporary)
                        freeze(snapshot | {"fixtures": linked}, temporary / "forecast.json")
                        extension = {"sha256": hashlib.sha256((HERE / "FORWARD.md").read_bytes()).hexdigest(),
                                     "program_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
                        (temporary / "extension.json").write_bytes(encoded(extension))
                        for year, month in {schedule_key(row)[1:] for row in linked}:
                            shutil.copytree(scratch / f"schedule-{year}-{month:02d}",
                                            temporary / f"schedule-{year}-{month:02d}")
                        temporary.rename(target)
                        path = target / "forecast.json"
                        frozen.append((path, json.loads(path.read_bytes())))
                        known.update(row["official_fixture_id"] for row in linked)
                except (requests.RequestException, ValueError, KeyError) as exc:
                    failures.append(f"Round {round_no}: {exc}")

        joined = []
        waiting = 0
        results = state / "results"
        results.mkdir(exist_ok=True)
        for path, batch in frozen:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result_path = results / f"{digest}.json"
            if not result_path.exists() and all(
                    datetime.fromisoformat(row["kickoff_kst"]) + timedelta(hours=2) <= now
                    for row in batch["fixtures"]):
                schedules = {key: get_schedule(*key) for key in {schedule_key(row) for row in batch["fixtures"]}}
                result = settle(batch, schedules, expected=None, allow_rescheduled=True)
                if not result["unresolved"]:
                    evidence = results / digest
                    evidence.mkdir(exist_ok=True)
                    for year, month in {key[1:] for key in schedules}:
                        dest = evidence / f"schedule-{year}-{month:02d}"
                        shutil.copytree(scratch / dest.name, dest, dirs_exist_ok=True)
                    write_atomic(result_path, encoded(result | {"frozen_sha256": digest}))
            if result_path.exists():
                result = json.loads(result_path.read_bytes())
                if result["frozen_sha256"] != digest:
                    raise ValueError("Settlement digest does not match the frozen forecast")
                joined.extend(result["joined"])
            else:
                waiting += len(batch["fixtures"])
        update_manuscript(manuscript, len({batch["round"] for _, batch in frozen}), waiting, joined)
        return {"frozen": len(known), "settled": len(joined), "waiting": waiting, "capture_errors": failures}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", type=Path, required=True)
    cli.add_argument("--hints", type=Path, required=True)
    cli.add_argument("--manuscript", type=Path, default=HERE / "MANUSCRIPT.md")
    cli.add_argument("--log", type=Path, required=True)
    args = cli.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            result = run(args.state, args.hints, args.manuscript)
            print(json.dumps(result, ensure_ascii=False))
            return int(bool(result["capture_errors"]))
        except Exception as exc:
            import traceback
            traceback.print_exception(exc)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
