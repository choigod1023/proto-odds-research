"""Versioned external results and metrics, separate from the betting ledger.

Provider team IDs remain namespaced. No fuzzy aliases, future leakage, CSV
intermediates, or automatic promotion of these observations into predictions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from runtime_db import RuntimeDatabase


SCHEMA = """
CREATE TABLE IF NOT EXISTS sports_game_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 provider TEXT NOT NULL, event_id TEXT NOT NULL, league TEXT NOT NULL,
 sport TEXT NOT NULL, home_id TEXT NOT NULL, away_id TEXT NOT NULL,
 home_name TEXT NOT NULL, away_name TEXT NOT NULL,
 history_at TEXT NOT NULL, status TEXT NOT NULL,
 home_score INTEGER, away_score INTEGER, score_unit TEXT NOT NULL,
 observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(provider, league, event_id, observed_at)
);
CREATE INDEX IF NOT EXISTS sports_game_asof ON sports_game_versions
 (provider, league, event_id, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS sports_game_home ON sports_game_versions
 (provider, league, home_id, history_at);
CREATE INDEX IF NOT EXISTS sports_game_away ON sports_game_versions
 (provider, league, away_id, history_at);
CREATE TABLE IF NOT EXISTS sports_metric_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 provider TEXT NOT NULL, league TEXT NOT NULL, subject_id TEXT NOT NULL,
 subject_type TEXT NOT NULL, season TEXT NOT NULL, metric_group TEXT NOT NULL,
 observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(provider,league,subject_id,subject_type,season,metric_group,observed_at)
);
CREATE INDEX IF NOT EXISTS sports_metric_asof ON sports_metric_versions
 (provider,league,subject_id,observed_at DESC);
"""


def utc_stamp(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timezone-aware observation/cutoff is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_game(row, observed_at):
    row = dict(row)
    for key in ("provider", "event_id", "league", "sport", "home_id", "away_id", "home_name", "away_name"):
        if row.get(key) is None or not str(row[key]).strip():
            raise ValueError(f"missing {key}")
        row[key] = str(row[key]).strip()
    if row["home_id"] == row["away_id"]:
        raise ValueError("home/away identities must differ")
    if row.get("status") not in {"final", "cancelled"}:
        raise ValueError("only final results or explicit invalidations are historical records")
    units = {"축구": "goals", "야구": "runs", "농구": "points", "배구": "sets"}
    if units.get(row["sport"]) != row.get("score_unit"):
        raise ValueError("sport and score unit mismatch")
    if row.get("kickoff_at"):
        row["kickoff_at"] = utc_stamp(row["kickoff_at"])
        row["time_precision"] = "timestamp"
        history_at = row["kickoff_at"]
    else:
        # Unknown source time zone is not invented. Date-only historical data is
        # ordered conservatively at the *end* of its source date (UTC).
        day = datetime.strptime(str(row.get("game_date")), "%Y-%m-%d")
        history_at = utc_stamp((day + timedelta(days=1)).replace(tzinfo=timezone.utc))
        row["kickoff_at"] = None
        row["time_precision"] = "date"
    if row["status"] == "final" and history_at > observed_at:
        raise ValueError("a future game cannot be a completed historical observation")
    for side in ("home", "away"):
        score = row.get(f"{side}_score")
        if row["status"] == "cancelled":
            row[f"{side}_score"] = None
        elif isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0 or int(score) != score:
            raise ValueError(f"invalid {side} final score")
        else:
            row[f"{side}_score"] = int(score)
    metrics = {} if row["status"] == "cancelled" else row.get("metrics", {})
    if not isinstance(metrics, dict) or any(side not in {"home", "away"} for side in metrics):
        raise ValueError("metrics must be separated into home and away")
    for side, values in metrics.items():
        if not isinstance(values, dict):
            raise ValueError("team metrics must be an object")
        for key, value in values.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"invalid metric {side}.{key}")
            if key in {"xg", "npxg", "xgot", "xg_open", "xg_set"} and value is not None and value < 0:
                raise ValueError("expected goals must be nonnegative")
            if key in {"xg", "npxg", "xgot"} and row["sport"] != "축구":
                raise ValueError("do not relabel other-sport metrics as football xG")
    row["metrics"] = metrics
    row.pop("observed_at", None)  # local receipt time always wins
    return row, history_at


class SportsHistoryStore:
    def __init__(self, database: RuntimeDatabase):
        self.db = database
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)

    def record_games(self, rows, *, observed_at=None):
        stamp = utc_stamp(observed_at)
        normalized = [normalize_game(row, stamp) for row in rows]
        inserted = 0
        with self.db.transaction() as connection:
            for row, history_at in normalized:
                # Fetch timings/raw body references don't constitute a score
                # correction. Preserve first known availability on duplicate polls.
                key = (row["provider"], row["league"], row["event_id"])
                latest = connection.execute(
                    "SELECT content_hash,observed_at,payload_json FROM sports_game_versions "
                    "WHERE provider=? AND league=? AND event_id=? ORDER BY observed_at DESC,id DESC LIMIT 1", key,
                ).fetchone()
                if latest and latest["observed_at"] > stamp:
                    raise ValueError("out-of-order observation: retry with actual receipt time")
                if latest and row.get("detail_fetch_status") == "not_requested_budget" and row["status"] == "final":
                    previous = json.loads(latest["payload_json"])
                    unchanged = all(row.get(field) == previous.get(field) for field in
                                    ("status", "home_id", "away_id", "home_score", "away_score", "kickoff_at"))
                    if unchanged and any(previous.get("metrics", {}).values()):
                        row["metrics"] = previous["metrics"]
                        row["metric_status"] = previous.get("metric_status", "available")
                        row["metrics_observed_at"] = previous.get("metrics_observed_at", latest["observed_at"])
                        row["metrics_source_url"] = previous.get("metrics_source_url", previous.get("source_url"))
                        row["metrics_reused"] = True
                body = canonical_json(row)
                digest = hashlib.sha256(body.encode()).hexdigest()
                if latest and latest["content_hash"] == digest:
                    continue
                connection.execute(
                    "INSERT INTO sports_game_versions(provider,event_id,league,sport,home_id,away_id,"
                    "home_name,away_name,history_at,status,home_score,away_score,score_unit,observed_at,content_hash,payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(row[k] for k in ("provider", "event_id", "league", "sport", "home_id", "away_id", "home_name", "away_name")) +
                    (history_at, row["status"], row.get("home_score"), row.get("away_score"), row["score_unit"], stamp, digest, body),
                )
                # Existing explicit CSV exporter works without adding another file pipeline.
                payload = {**row, "observed_at": stamp, "revision": digest}
                connection.execute(
                    "INSERT INTO event_records(stream,identity,observed_at,content_hash,payload_json,inserted_at) VALUES (?,?,?,?,?,?)",
                    ("sports_history", hashlib.sha256(canonical_json([*key, stamp]).encode()).hexdigest(),
                     stamp, digest, canonical_json(payload), stamp),
                )
                inserted += 1
        return inserted

    def record_metric_snapshots(self, rows, *, observed_at=None):
        """Season totals are current observations, never backfilled game features."""
        stamp = utc_stamp(observed_at)
        inserted = 0
        with self.db.transaction() as connection:
            for original in rows:
                row = dict(original)
                row.pop("observed_at", None)
                if row.get("scope") != "season_snapshot" or row.get("kind") != "metric_snapshot":
                    raise ValueError("aggregate metrics require explicit season_snapshot scope")
                fields = ("provider", "league", "subject_id", "subject_type", "season", "group")
                if not all(row.get(key) is not None and str(row[key]).strip() for key in fields):
                    raise ValueError("metric snapshot missing identity/season/group")
                for key in fields:
                    row[key] = str(row[key])
                metrics = row.get("metrics")
                if not isinstance(metrics, dict):
                    raise ValueError("metric snapshot must contain metrics object")
                for value in metrics.values():
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                        raise ValueError("invalid aggregate metric")
                key = tuple(row[k] for k in fields)
                body = canonical_json(row)
                digest = hashlib.sha256(body.encode()).hexdigest()
                latest = connection.execute("SELECT content_hash,observed_at FROM sports_metric_versions "
                    "WHERE provider=? AND league=? AND subject_id=? AND subject_type=? AND season=? AND metric_group=? "
                    "ORDER BY observed_at DESC,id DESC LIMIT 1", key).fetchone()
                if latest and latest["observed_at"] > stamp:
                    raise ValueError("out-of-order metric observation")
                if latest and latest["content_hash"] == digest:
                    continue
                connection.execute("INSERT INTO sports_metric_versions(provider,league,subject_id,subject_type,season,"
                    "metric_group,observed_at,content_hash,payload_json) VALUES (?,?,?,?,?,?,?,?,?)", (*key, stamp, digest, body))
                connection.execute("INSERT INTO event_records(stream,identity,observed_at,content_hash,payload_json,inserted_at) "
                    "VALUES (?,?,?,?,?,?)", ("sports_metric_snapshots", hashlib.sha256(canonical_json([*key, stamp]).encode()).hexdigest(),
                    stamp, digest, canonical_json({**row, "observed_at": stamp, "revision": digest}), stamp))
                inserted += 1
        return inserted

    def metric_snapshots(self, *, provider=None, league=None, subject_id=None, as_of=None):
        filters, params = ["observed_at<=?"], [utc_stamp(as_of)]
        for field, value in (("provider", provider), ("league", league), ("subject_id", subject_id)):
            if value is not None:
                filters.append(f"{field}=?")
                params.append(str(value))
        with self.db.connect() as connection:
            rows = connection.execute("""WITH versions AS (
              SELECT *,ROW_NUMBER() OVER(PARTITION BY provider,league,subject_id,subject_type,season,metric_group
                ORDER BY observed_at DESC,id DESC) AS revision_rank
              FROM sports_metric_versions WHERE """ + " AND ".join(filters) + ") "
              "SELECT payload_json,observed_at FROM versions WHERE revision_rank=1 ORDER BY provider,league,subject_id,season,metric_group", params)
            return [{**json.loads(row["payload_json"]), "observed_at": row["observed_at"]} for row in rows]

    @staticmethod
    def _recent_query():
        # Observation cutoff is applied BEFORE choosing the latest revision.
        # Status/team filtering follows ranking, so corrections/cancellations
        # cannot resurrect an older final score or an old team identity.
        return """WITH versions AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY provider,league,event_id
            ORDER BY observed_at DESC,id DESC) AS revision_rank
          FROM sports_game_versions WHERE provider=? AND league=? AND observed_at<=?
        ), recent AS (
          SELECT *, CASE WHEN home_id=? THEN 'home' ELSE 'away' END AS side
          FROM versions WHERE revision_rank=1 AND status='final' AND history_at<?
            AND (home_id=? OR away_id=?)
          ORDER BY history_at DESC,event_id DESC LIMIT ?
        ) """

    def team_form(self, provider, league, team_id, *, as_of=None, limit=10):
        if not 1 <= limit <= 100:
            raise ValueError("recent limit must be in 1..100")
        cutoff = utc_stamp(as_of)
        params = (provider, league, cutoff, str(team_id), cutoff, str(team_id), str(team_id), limit)
        query = self._recent_query()
        with self.db.connect() as connection:
            connection.execute("BEGIN")
            records = connection.execute(query + "SELECT * FROM recent", params).fetchall()
            summary = connection.execute(query + """SELECT COUNT(*) AS games,
              SUM(CASE WHEN (side='home' AND home_score>away_score) OR
                (side='away' AND away_score>home_score) THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN home_score=away_score THEN 1 ELSE 0 END) AS draws,
              AVG(CASE WHEN side='home' THEN home_score ELSE away_score END) AS avg_scored,
              AVG(CASE WHEN side='home' THEN away_score ELSE home_score END) AS avg_conceded
              FROM recent""", params).fetchone()
            metric_rows = connection.execute(query + """SELECT metric.key AS name,
              COUNT(*) AS samples, AVG(metric.value) AS mean
              FROM recent, json_each(json_extract(recent.payload_json, '$.metrics.' || recent.side)) AS metric
              WHERE metric.type IN ('integer','real') GROUP BY metric.key ORDER BY metric.key""", params).fetchall()
        result = dict(summary)
        result["wins"] = result["wins"] or 0
        result["draws"] = result["draws"] or 0
        result["losses"] = result["games"] - result["wins"] - result["draws"]
        return {"provider": provider, "league": league, "team_id": str(team_id),
                "as_of": cutoff, "requested_games": limit,
                "status": "available" if records else "no_completed_history",
                "score_unit": records[0]["score_unit"] if records else None,
                "summary": result,
                "metrics": [dict(row) for row in metric_rows],
                "recent_games": [{**json.loads(row["payload_json"]), "side": row["side"],
                                  "observed_at": row["observed_at"]} for row in records]}

    def inventory(self):
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute("""
              SELECT provider,league,sport,COUNT(*) AS versions,
                COUNT(DISTINCT event_id) AS events, MAX(history_at) AS newest_game,
                MAX(observed_at) AS last_observed_at
              FROM sports_game_versions GROUP BY provider,league,sport ORDER BY provider,league
            """)]
