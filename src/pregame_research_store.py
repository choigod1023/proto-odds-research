"""Append-only, separate SQLite research archive. No runtime-ledger writes.

The caller supplies canonical event IDs and explicit snapshot/selection IDs;
this module never guesses teams, markets, scores, selections or probabilities.
Official source allowlisting is a caller trust policy, not source verification.
All timestamps require timezone offsets. Source publication time is not our
observation time. Exports use both observed_at and captured_at to prevent a
late import of an old article from qualifying as prospective evidence.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

APPLICATION_ID = 0x50525331
FACT_TYPES = frozenset({"starter", "absence", "rotation", "lineup", "form", "rest",
                        "starters", "lineups", "unavailable", "benches"})
SCHEMA_COLUMNS = {
    "events": ["event_id", "kickoff"],
    "observations": ["id", "event_id", "kind", "source_name", "snapshot_id", "selection_id",
                     "revision", "observed_at", "captured_at", "source_updated_at", "datahash", "payload_json"],
}


class ResearchConflict(ValueError):
    """Identity or time conflicts: caller must inspect, never overwrite."""


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).isoformat(timespec="microseconds")


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 512:
        raise ValueError(f"invalid {name}")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PregameResearchStore:
    def __init__(self, path, *, official_sources, busy_timeout_ms=250,
                 max_payload_bytes=65536, clock=None, read_only=False):
        self.path = Path(path).resolve()
        self.read_only = read_only
        self.official_sources = frozenset(_identifier(s, "official source") for s in official_sources)
        if not self.official_sources:
            raise ValueError("official_sources must be nonempty")
        if not 1 <= busy_timeout_ms <= 1000 or not 256 <= max_payload_bytes <= 1048576:
            raise ValueError("invalid bounded timeout/payload limit")
        self.timeout = busy_timeout_ms / 1000
        self.max_payload_bytes = max_payload_bytes
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Refuse any non-research DB BEFORE schema changes (including runtime DB).
        with self._connect() as db:
            db.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if read_only and app_id != APPLICATION_ID:
                raise ResearchConflict("not an initialized research database")
            if app_id != APPLICATION_ID and (app_id != 0 or tables):
                raise ResearchConflict("not a dedicated research database")
            if app_id == APPLICATION_ID and tables != {"events", "observations"}:
                raise ResearchConflict("unexpected research schema")
            if app_id == APPLICATION_ID:
                for table, columns in SCHEMA_COLUMNS.items():
                    actual = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                    if actual != columns:
                        raise ResearchConflict("unexpected research columns")
            if app_id == 0 and not read_only:
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute("""CREATE TABLE events(event_id TEXT PRIMARY KEY, kickoff TEXT NOT NULL)""")
                db.execute("""CREATE TABLE observations(
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id),
                    kind TEXT NOT NULL CHECK(kind IN ('evidence','pair','result')),
                    source_name TEXT NOT NULL, snapshot_id TEXT NOT NULL, selection_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, observed_at TEXT NOT NULL, captured_at TEXT NOT NULL,
                    source_updated_at TEXT, datahash TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(event_id,kind,source_name,snapshot_id,selection_id,revision),
                    UNIQUE(event_id,kind,source_name,snapshot_id,selection_id,observed_at))""")
                db.execute("CREATE INDEX obs_pair ON observations(event_id,kind,snapshot_id,selection_id)")
                db.execute("CREATE INDEX obs_kind_id ON observations(kind,id)")
                for table in ("events", "observations"):
                    for action in ("UPDATE", "DELETE"):
                        db.execute(f"""CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table}
                            BEGIN SELECT RAISE(ABORT,'research records are append-only'); END""")
            db.commit()

    @contextmanager
    def _connect(self):
        if self.read_only:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=self.timeout)
        else:
            db = sqlite3.connect(self.path, timeout=self.timeout)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()  # rollback any uncommitted transaction

    def append_evidence(self, *, event_id, kickoff, observed_at, source, typed_facts,
                        source_updated_at=None):
        if not isinstance(typed_facts, list) or not 1 <= len(typed_facts) <= 256:
            raise ValueError("typed_facts must be a nonempty bounded list")
        for fact in typed_facts:
            if not isinstance(fact, dict) or set(fact) != {"type", "subject", "value"}:
                raise ValueError("fact requires type/subject/value only")
            if not isinstance(fact["type"], str) or fact["type"] not in FACT_TYPES:
                raise ValueError("unsupported fact type (final picks are excluded)")
            _identifier(fact["subject"], "fact subject")
        return self._append("evidence", event_id, kickoff, observed_at, source, "", "",
                            {"typed_facts": typed_facts}, source_updated_at)

    def append_pair(self, *, event_id, kickoff, observed_at, source, snapshot_id,
                    selection_id, odds, baseline_decision, source_updated_at=None,
                    evidence_refs=None):
        for name, value in (("odds", odds), ("baseline_decision", baseline_decision)):
            if not isinstance(value, (dict, list)) or not value:
                raise ValueError(f"{name} must be a nonempty JSON object or list")
        self._check_embedded(baseline_decision, event_id, snapshot_id, selection_id)
        payload = {"odds": odds, "baseline_decision": baseline_decision}
        if evidence_refs is not None:
            if not isinstance(evidence_refs, list) or len(evidence_refs) > 256:
                raise ValueError("evidence_refs must be a bounded list")
            for ref in evidence_refs:
                if (not isinstance(ref, dict) or set(ref) != {"id", "datahash"}
                        or type(ref["id"]) is not int or ref["id"] <= 0
                        or not isinstance(ref["datahash"], str) or len(ref["datahash"]) != 64):
                    raise ValueError("evidence reference requires id/datahash")
            if len({ref["id"] for ref in evidence_refs}) != len(evidence_refs):
                raise ValueError("duplicate evidence reference")
            payload["evidence_refs"] = sorted(evidence_refs, key=lambda ref: ref["id"])
        return self._append("pair", event_id, kickoff, observed_at, source, snapshot_id,
                            selection_id, payload, source_updated_at)

    def append_result(self, *, event_id, kickoff, settled_observed_at, source,
                      snapshot_id, selection_id, outcome, source_updated_at=None):
        if not isinstance(outcome, dict) or not outcome:
            raise ValueError("outcome must be a nonempty JSON object")
        self._check_embedded(outcome, event_id, snapshot_id, selection_id)
        return self._append("result", event_id, kickoff, settled_observed_at, source,
                            snapshot_id, selection_id, {"outcome": outcome}, source_updated_at)

    @staticmethod
    def _check_embedded(payload, event_id, snapshot_id, selection_id):
        if isinstance(payload, dict):
            for key, expected in (("event_id", event_id), ("snapshot_id", snapshot_id),
                                  ("selection_id", selection_id)):
                if key in payload and payload[key] != expected:
                    raise ResearchConflict(f"embedded {key} mismatch")

    def _append(self, kind, event_id, kickoff, observed_at, source, snapshot_id,
                selection_id, payload, source_updated_at):
        if self.read_only:
            raise PermissionError("research store is read-only")
        _identifier(event_id, "event_id")
        source_name = source.get("name") if isinstance(source, dict) else source
        _identifier(source_name, "source")
        if kind != "evidence":
            _identifier(snapshot_id, "snapshot_id")
            _identifier(selection_id, "selection_id")
        if kind == "result" and source_name not in self.official_sources:
            raise ValueError("result source is not allowlisted as official")
        now, start, observed = _time(self.clock()), _time(kickoff), _time(observed_at)
        if observed > now:
            raise ValueError("observation is in the future")
        updated = _time(source_updated_at) if source_updated_at is not None else None
        if updated is not None and updated > observed:
            raise ValueError("source update is later than observation")
        cutoff = start - timedelta(minutes=30)
        if kind == "evidence" and observed >= start:
            raise ValueError("evidence must be observed before kickoff")
        if kind == "result" and observed < start:
            raise ValueError("settlement cannot precede kickoff")
        # Time fields are metadata, not content: repeated unchanged observations
        # retain the first stored time; A -> B -> A is a new revision, not deduped.
        body = _json({"source": source, **payload})
        if len(body.encode("utf-8")) > self.max_payload_bytes:
            raise ValueError("payload exceeds byte limit")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        key = (event_id, kind, source_name, snapshot_id, selection_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            event = db.execute("SELECT kickoff FROM events WHERE event_id=?", (event_id,)).fetchone()
            if event and event[0] != _stamp(start):
                raise ResearchConflict("kickoff changed for existing event_id")
            previous = db.execute("""SELECT * FROM observations WHERE event_id=? AND kind=?
                AND source_name=? AND snapshot_id=? AND selection_id=? ORDER BY revision DESC LIMIT 1""", key).fetchone()
            if previous:
                if _stamp(observed) < previous["observed_at"]:
                    raise ResearchConflict("out-of-order observation")
                if previous["datahash"] == digest:
                    return self._decode(previous)
                if _stamp(observed) == previous["observed_at"]:
                    raise ResearchConflict("different content at identical observation time")
            if kind == "pair":
                if observed >= cutoff or now >= cutoff:
                    raise ValueError("pair must be captured strictly before T-30")
                for other in db.execute("""SELECT payload_json FROM observations WHERE event_id=?
                    AND kind='pair' AND snapshot_id=? AND selection_id=? ORDER BY id LIMIT 1""",
                    (event_id, snapshot_id, selection_id)):
                    other_payload = json.loads(other[0])
                    if any(other_payload[name] != payload[name] for name in ("odds", "baseline_decision")):
                        raise ResearchConflict("pair identity conflicts across sources")
                for ref in payload.get("evidence_refs", []):
                    fact = db.execute("SELECT * FROM observations WHERE id=?", (ref["id"],)).fetchone()
                    if (fact is None or fact["kind"] != "evidence" or fact["event_id"] != event_id
                            or fact["datahash"] != ref["datahash"]
                            or fact["observed_at"] > _stamp(observed)
                            or fact["captured_at"] > _stamp(now)):
                        raise ResearchConflict("evidence reference unavailable or mismatched")
            if not event:
                db.execute("INSERT INTO events VALUES (?,?)", (event_id, _stamp(start)))
            cursor = db.execute("""INSERT INTO observations(event_id,kind,source_name,snapshot_id,
                selection_id,revision,observed_at,captured_at,source_updated_at,datahash,payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (*key, previous["revision"] + 1 if previous else 1,
                _stamp(observed), _stamp(now), _stamp(updated) if updated else None, digest, body))
            row = db.execute("SELECT * FROM observations WHERE id=?", (cursor.lastrowid,)).fetchone()
            db.commit()
            return self._decode(row)

    @staticmethod
    def _decode(row):
        value = dict(row)
        value.update(json.loads(value.pop("payload_json")))
        return value

    @staticmethod
    def _page(after_id, limit):
        if type(after_id) is not int or after_id < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("after_id must be nonnegative; limit must be 1..1000")

    def iter_pairs(self, *, after_id=0, limit=1000):
        """Bounded keyset page for settlement capture; resume with last row id."""
        self._page(after_id, limit)
        with self._connect() as db:
            for row in db.execute("""SELECT o.*,e.kickoff FROM observations o JOIN events e USING(event_id)
                WHERE kind='pair' AND o.id>? ORDER BY o.id LIMIT ?""", (after_id, limit)):
                yield self._decode(row)

    def export_paired(self, *, after_id=0, limit=1000):
        """Stream immutable pairs and exact-identity outcomes, with missing flags.

        Results are latest per official source; disagreement across sources is
        withheld. Evidence is latest per source as of the pair's actual capture,
        and must have been both observed and archived before that capture.
        No scores are inferred. Missing pairs are not manufactured.
        """
        self._page(after_id, limit)
        with self._connect() as db:
            db.execute("BEGIN")
            pairs = db.execute("""SELECT o.*,e.kickoff FROM observations o JOIN events e USING(event_id)
                                  WHERE kind='pair' AND o.id>? ORDER BY o.id LIMIT ?""", (after_id, limit))
            for raw in pairs:
                pair = self._decode(raw)
                flags, evidence = [], []
                # Do not use later evidence to reconstruct earlier decisions.
                rows = db.execute("""SELECT o.* FROM observations o WHERE event_id=? AND kind='evidence'
                    AND observed_at<=? AND captured_at<=? AND revision=(
                        SELECT MAX(n.revision) FROM observations n WHERE n.event_id=o.event_id
                        AND n.kind='evidence' AND n.source_name=o.source_name
                        AND n.snapshot_id='' AND n.selection_id=''
                        AND n.observed_at<=? AND n.captured_at<=?) ORDER BY source_name""",
                    (pair["event_id"], pair["observed_at"], pair["captured_at"],
                     pair["observed_at"], pair["captured_at"]))
                seen = set()
                for row in rows:
                    if row["source_name"] not in seen:
                        evidence.append(self._decode(row))
                        seen.add(row["source_name"])
                if "evidence_refs" in pair:
                    # Explicit context versions, never a newer fact from the same source.
                    evidence = []
                    for ref in pair["evidence_refs"]:
                        row = db.execute("SELECT * FROM observations WHERE id=?", (ref["id"],)).fetchone()
                        if (row is None or row["kind"] != "evidence" or row["event_id"] != pair["event_id"]
                                or row["datahash"] != ref["datahash"]
                                or row["observed_at"] > pair["observed_at"]
                                or row["captured_at"] > pair["captured_at"]):
                            flags.append("invalid_evidence_reference")
                        else:
                            evidence.append(self._decode(row))
                if not evidence:
                    flags.append("missing_pregame_evidence")
                results, seen = [], set()
                for row in db.execute("""SELECT o.* FROM observations o WHERE event_id=? AND kind='result'
                    AND snapshot_id=? AND selection_id=? AND revision=(
                        SELECT MAX(n.revision) FROM observations n WHERE n.event_id=o.event_id
                        AND n.kind='result' AND n.source_name=o.source_name
                        AND n.snapshot_id=o.snapshot_id AND n.selection_id=o.selection_id)
                    ORDER BY source_name""",
                    (pair["event_id"], pair["snapshot_id"], pair["selection_id"])):
                    if row["source_name"] in self.official_sources and row["source_name"] not in seen:
                        results.append(self._decode(row))
                        seen.add(row["source_name"])
                outcome = None
                if not results:
                    flags.append("missing_official_result")
                elif len({_json(r["outcome"]) for r in results}) != 1:
                    flags.append("conflicting_official_results")
                else:
                    outcome = results[0]["outcome"]
                yield {"pair": pair, "evidence": evidence, "results": results,
                       "official_outcome": outcome, "incomplete_flags": flags,
                       "complete": not flags}
