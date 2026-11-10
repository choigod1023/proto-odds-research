"""Join official Betman G101 handicap offers to KBL first-team results.

Product recognition and grading follow PR #689 at
68bdbc7ea6ef0a2035e885c66340de7b1454e232 (side_markets.py).
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
from hashlib import sha256
from itertools import groupby
import json
import math
from pathlib import Path
import re


KST = timezone(timedelta(hours=9))
SEASONS = {"S45": "2024-2025", "S47": "2025-2026"}
PHASES = {"01": ("정규시즌", "regular"), "03": ("플레이오프", "playoff"),
          "04": ("챔피언결정전", "playoff")}
HANDICAPS = {(2, None, None), (23, "6", "5")}
PRICE_STATUS = "retrospective_unverified"
KBL_LIST = "https://api.kbl.or.kr/match/list?"


def _json(path):
    return json.loads(path.read_bytes())


def _verified_body(path, expected_hash):
    body = gzip.decompress(path.read_bytes())
    if sha256(body).hexdigest() != expected_hash:
        raise ValueError(f"response hash mismatch: {path}")
    return json.loads(body)


def _official_records(path, captures):
    body = path.read_bytes()
    records = {}
    for line in body.splitlines():
        record = json.loads(line)
        if record["league"] != "KBL" or record["kind"] not in ("fixture", "result"):
            continue
        ident = record["payload"]["source_fixture_id"]
        key = (ident, record["kind"])
        receipt = (record["source_url"], record["body_sha256"], record["received_at"])
        suffix = sha256((receipt[1] + receipt[2]).encode()).hexdigest()[:12]
        if (
            receipt not in captures
            or record["record_id"] != f"kbl:{key[1]}:{ident}:{suffix}"
            or key in records
        ):
            raise ValueError(f"invalid or duplicate official record: {key}")
        records[key] = record
    return records, sha256(body).hexdigest()


def _official_schedule(path, sources):
    captures = {(r["url"], r["body_sha256"], r["received_at"]): raw for r, raw in sources}
    records, digest = _official_records(path, captures)
    games = {}
    for receipt, body in captures.items():
        for raw in body:
            code = raw["gameCode"]
            if raw["seasonGrade"] != 1 or code not in PHASES:
                continue
            category, phase = PHASES[code]
            season = f"S{raw['seasonCode']}"
            ident = raw["gmkey"]
            if (
                type(raw["gameNo"]) is not int or raw["gameNo"] <= 0
                or raw["glkey"] != f"{season}G{code}"
                or ident != f"{season}G{code}N{raw['gameNo']}"
                or raw["seasonCategoryName"] != category
                or (season in SEASONS and raw["seasonName1"] != SEASONS[season])
                or ident in games
            ):
                raise ValueError(f"invalid or duplicate official fixture: {ident}")
            kickoff = datetime.strptime(
                raw["gameDate"] + raw["gameStart"], "%Y%m%d%H%M"
            ).replace(tzinfo=KST)
            teams = (raw["tcodeH"], raw["tcodeA"])
            if teams[0] == teams[1] or not all(re.fullmatch(r"[0-9]{2}", t) for t in teams):
                raise ValueError(f"invalid official team IDs: {ident}")
            score = [raw["scoreH"], raw["scoreA"]] if raw["isEnded"] == 1 else None
            if score is not None and (
                not all(type(points) is int and points >= 0 for points in score)
                or score[0] == score[1]
            ):
                raise ValueError(f"invalid final KBL score: {ident}")
            fixture = records.pop((ident, "fixture"), None)
            result = records.pop((ident, "result"), None)
            if fixture is None or (result is None) != (score is None):
                raise ValueError(f"official fixture/result missing from {path}: {ident}")
            for record in (fixture, result) if result else (fixture,):
                if (
                    (record["source_url"], record["body_sha256"], record["received_at"]) != receipt
                    or datetime.fromisoformat(record["kickoff"]) != kickoff
                    or (record["home"], record["away"]) != (raw["tnameH"], raw["tnameA"])
                ):
                    raise ValueError(f"normalized KBL record disagrees with raw response: {ident}")
            if (
                (fixture["payload"]["home_team_id"], fixture["payload"]["away_team_id"]) != teams
                or (result and [result["payload"]["home_score"], result["payload"]["away_score"]] != score)
            ):
                raise ValueError(f"normalized KBL teams or score disagree with raw response: {ident}")
            games[ident] = dict(id=ident, kickoff=kickoff, season=season, phase=phase,
                                teams=teams, score=score, fixture=fixture, result=result)
    if any(ident[:3] in SEASONS for ident, _ in records):
        raise ValueError(f"normalized KBL records have no first-team source fixture: {path}")
    return games, digest


def _read_kbl(repo, extra_schedule_path):
    directory = repo / "experiments/pregame-basketball-20261104"
    manifest = _json(directory / "season-records.manifest.json")
    evidence = repo.parent / "pregame-basketball-evidence"
    sources = []
    for receipt in manifest["requests"]:
        if receipt["status"] == 200 and receipt["url"].startswith(KBL_LIST):
            path = evidence / (receipt["label"] + ".body.gz")
            sources.append((receipt, _verified_body(path, receipt["body_sha256"])))
    games, digest = _official_schedule(directory / "season-records.jsonl", sources)
    hashes = {"official_normalized": digest}
    if extra_schedule_path is not None:
        path = Path(extra_schedule_path)
        receipt_path = path.with_suffix(".json")
        if path.name == "extra-schedule.jsonl" and not receipt_path.is_file():
            receipt_path = path.with_name("kbl-2024-q4.json")
        receipt = _json(receipt_path)
        if receipt["status"] != 200 or not receipt["url"].startswith(KBL_LIST):
            raise ValueError(f"expected a successful official KBL match-list receipt: {path}")
        body = _verified_body(receipt_path.with_suffix(".body.gz"), receipt["body_sha256"])
        extra, hashes["extra_official_normalized"] = _official_schedule(path, [(receipt, body)])
        for ident, game in extra.items():
            if ident in games:
                fields = ("kickoff", "season", "phase", "teams", "score")
                if any(game[field] != games[ident][field] for field in fields):
                    raise ValueError(f"conflicting official KBL captures: {ident}")
            else:
                games[ident] = game
    return games, hashes


def _rest(games):
    """The official KBL schedule has at most one fixture per team and KST day."""
    prior, rest = {}, {}
    for day, fixtures in groupby(sorted(games, key=lambda game: game["kickoff"]),
                                 key=lambda game: game["kickoff"].date()):
        teams_today = set()
        for game in fixtures:
            keys = [(game["season"], team) for team in game["teams"]]
            if any(key in teams_today for key in keys):
                raise ValueError(f"multiple same-day fixtures for a KBL team: {game['id']}")
            teams_today.update(keys)
            rest[game["id"]] = [
                ((day - prior[key][0]).days - 1, prior[key][1]) if key in prior else (None, None)
                for key in keys
            ]
            if game["result"] is not None:
                prior.update({key: (day, game["id"]) for key in keys})
    return rest


def _price_terms(row):
    line = row["winHandi"]
    odds = [row["winAllot"], row["loseAllot"]]
    if (
        type(line) in (int, float) and line % 1 == 0.5
        and row["loseHandi"] == -line and row["drawAllot"] == 0
        and row["gameSubject"] in (None, "")
        and all(type(price) in (int, float) and math.isfinite(price) and price > 1 for price in odds)
        and isinstance(row["buyReject"], str)
    ):
        return line, odds
    return None


def _grade(row, line, score):
    adjusted = (Decimal(score[0]) + Decimal(str(line)), Decimal(score[1]))
    y = int(adjusted[0] > adjusted[1])
    try:
        archived = tuple(Decimal(part) for part in (row["mchScore"] or "").split(":"))
    except (AttributeError, InvalidOperation):
        return None
    return y if adjusted == archived and row["gameResult"] == ("0" if y else "2") else None


def _offer_candidates(manifest_path, manifest, index, counts):
    sources = manifest["sources"]
    rounds = [source["round"] for source in sources]
    if len(set(rounds)) != len(rounds) or len(rounds) != manifest["source_round_count"]:
        raise ValueError("duplicate or missing manifest round")
    counts["rounds"] = len(rounds)
    seen = set()
    for source in sorted(sources, key=lambda s: s["round"]):
        round_id = source["round"]
        if not re.fullmatch(r"[0-9]{6}", round_id):
            raise ValueError(f"invalid manifest round: {round_id!r}")
        root = Path(manifest["source_roots"][source["source_root_index"]])
        body_file = Path(source["body_file"])
        path = manifest_path.parent / root / body_file
        detail = _verified_body(path, source["body_sha256"])
        lottery = detail["currentLottery"]
        if str(detail["gmTs"]) != round_id or str(lottery["gmTs"]) != round_id or lottery["gmId"] != "G101":
            raise ValueError(f"Betman G101 round mismatch: {path}")
        schedules = detail["compSchedules"]
        keys = schedules["keys"]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate Betman schedule keys: {path}")
        for values in schedules["datas"]:
            row = dict(zip(keys, values, strict=True))
            codes = (row["handi"], row["betId"], row["betTypId"])
            if row["leagueCode"] != "BK001" or codes not in HANDICAPS:
                continue
            counts["raw_offers"] += 1
            seq = row["matchSeq"]
            if type(seq) is not int or seq <= 0:
                raise ValueError(f"invalid Betman matchSeq: {round_id}/{seq!r}")
            offer_id = f"G101:{round_id}:{seq}"
            if offer_id in seen:
                raise ValueError(f"duplicate Betman product: {offer_id}")
            seen.add(offer_id)
            matches = index.get((row["gameDate"], str(row["homeId"]), str(row["awayId"])), ())
            if len(matches) != 1:
                counts["no_unique_official_fixture"] += 1
                continue
            game = matches[0]
            if game["season"] not in SEASONS:
                counts["other_season"] += 1
                continue
            terms = _price_terms(row)
            if terms is None:
                counts["invalid_or_unpriced"] += 1
                continue
            yield game, (round_id, seq), offer_id, row, terms, {
                "file": body_file.name if body_file.is_absolute() else body_file.as_posix(),
                "manifest_root_index": source["source_root_index"],
                "sha256": source["body_sha256"], "url": source["source_url"],
                "archive_received_at": source["received_at"], "timestamp_basis": source["timestamp_basis"],
            }


def _receipt(record):
    return {"record_id": record["record_id"], "sha256": record["body_sha256"],
            "url": record["source_url"], "received_at": record["received_at"]}


def load(repo: Path, manifest_path=None, extra_schedule_path=None) -> tuple[list[dict], dict]:
    """Load one settled retrospective offer per fixture from verified raw evidence.

    Relative Betman source roots resolve against the manifest directory.
    An extra KBL .jsonl must have a same-stem .json receipt and .body.gz response.
    Also accepts capture.py's extra-schedule.jsonl with kbl-2024-q4 evidence.
    """
    repo = Path(repo).resolve()
    manifest_path = (Path(manifest_path).resolve() if manifest_path is not None else
                     repo / "experiments/pregame-proto-prices-20261104/manifest-v2.json")
    manifest_body = manifest_path.read_bytes()
    manifest = json.loads(manifest_body)
    games, hashes = _read_kbl(repo, extra_schedule_path)
    hashes["betman_manifest"] = sha256(manifest_body).hexdigest()
    target = [game for game in games.values() if game["season"] in SEASONS]
    rest = _rest(target)
    index = defaultdict(list)
    for game in games.values():
        index[(int(game["kickoff"].timestamp()) * 1000, *game["teams"])].append(game)

    counts = Counter(dict.fromkeys((
        "rounds", "raw_offers", "no_unique_official_fixture", "other_season",
        "invalid_or_unpriced", "priced_candidates", "duplicate_offers",
        "unsettled_or_void", "settlement_conflict", "rows",
    ), 0))
    candidates = defaultdict(list)
    for candidate in _offer_candidates(manifest_path, manifest, index, counts):
        candidates[candidate[0]["id"]].append(candidate)
        counts["priced_candidates"] += 1

    rows = []
    for offers in candidates.values():
        counts["duplicate_offers"] += len(offers) - 1
        game, _, offer_id, raw, (line, odds), source = min(offers, key=lambda offer: offer[1])
        if (game["result"] is None or raw["gameReject"] != "0"
                or raw["protoStatus"] not in ("4", "20") or raw["gameResult"] not in ("0", "2")):
            counts["unsettled_or_void"] += 1
            continue
        y = _grade(raw, line, game["score"])
        if y is None:
            counts["settlement_conflict"] += 1
            continue
        (home_rest, home_prior), (away_rest, away_prior) = rest[game["id"]]
        rows.append({
            "id": game["id"], "offer_id": offer_id, "day": game["kickoff"].date().isoformat(),
            "kickoff": game["kickoff"].isoformat(), "season": game["season"], "phase": game["phase"],
            "team_ids": list(game["teams"]), "team_names": [raw["homeName"], raw["awayName"]],
            "home_rest": home_rest, "away_rest": away_rest,
            "home_prior_id": home_prior, "away_prior_id": away_prior,
            "line": line, "odds": odds, "y": y, "buy_reject": raw["buyReject"],
            "score": game["score"], "betman_result_code": raw["gameResult"],
            "betman_result_score": raw["mchScore"],
            "price_observation_status": PRICE_STATUS, "price_observed_at": None,
            "betman_source": source, "kbl_fixture_source": _receipt(game["fixture"]),
            "kbl_result_source": _receipt(game["result"]),
        })
    rows.sort(key=lambda row: (row["kickoff"], row["offer_id"]))
    counts["rows"] = len(rows)
    return rows, {
        **counts, "source_sha256": hashes,
        "manifest": (manifest_path.relative_to(repo).as_posix()
                     if manifest_path.is_relative_to(repo) else manifest_path.name),
        "extra_schedule": (
            Path(extra_schedule_path).name if extra_schedule_path is not None else None
        ),
        "deduplication": "Earliest numeric round then matchSeq among valid priced offers for the "
                         "exact official fixture, before settlement checks. No fallback.",
        "price_observation_status": PRICE_STATUS,
        "purchase_availability_verified": False, "execution_price_verified": False,
        "by_season": {
            season: {
                "schedule_start": min((g["kickoff"].date().isoformat() for g in target
                                       if g["season"] == season), default=None),
                "completed_by_phase": {phase: sum(g["season"] == season and g["phase"] == phase
                                                 and g["result"] is not None for g in target)
                                       for phase in ("regular", "playoff")},
                "rows_by_phase": {phase: sum(r["season"] == season and r["phase"] == phase for r in rows)
                                  for phase in ("regular", "playoff")},
            } for season in SEASONS
        },
        "missing_rest_rows": sum(None in (r["home_rest"], r["away_rest"]) for r in rows),
        "buy_reject_zero": sum(r["buy_reject"] == "0" for r in rows),
        "equal_price_offers": sum(r["odds"][0] == r["odds"][1] for r in rows),
    }
