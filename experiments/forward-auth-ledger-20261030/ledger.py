"""Research JSONL ledger. Local append protection is NOT third-party history proof.

Trust roots are supplied out of band, never read from the ledger/checkpoint.
The custom Ed25519 witness envelope is NOT an RFC3161 token implementation.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

PINNED_POLICY_SHA256 = "c5d4b18738d273d7a275d08d5dc638e2ca61e3f1764b394921efe72b77c194d5"


def pinned_trust():
    """Verifier-owned policy, not a record/caller-supplied key or verified flag.

    The committed research policy deliberately contains no external witness.
    Provisioning a real independent witness requires review and a new code pin.
    """
    data = Path(__file__).with_name("trust-policy.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != PINNED_POLICY_SHA256:
        raise InvalidLedger("verifier trust policy was rewritten")
    return json.loads(data)["witnesses"]


class InvalidLedger(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("timezone required")
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidLedger("invalid aware timestamp") from exc


def keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required.split()):
        raise InvalidLedger("unexpected or missing fields")


def sha(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise InvalidLedger("invalid SHA256")


def capture_valid(p):
    keys(p, "event_id product league kickoff received_at source_observed_at source_url source_sha256 source_kind odds probabilities votes")
    if any(not isinstance(p[k], str) or not p[k] for k in
           ("event_id", "product", "league", "source_url")):
        raise InvalidLedger("missing identity")
    if p["source_kind"] != "raw_response":
        raise InvalidLedger("raw response evidence required, archive cannot authenticate")
    sha(p["source_sha256"])
    received, start = stamp(p["received_at"]), stamp(p["kickoff"])
    if stamp(p["source_observed_at"]) > received or received > start-timedelta(minutes=30):
        raise InvalidLedger("future source or late receipt")
    odds = p["odds"]
    if not isinstance(odds, dict) or len(odds) not in (2, 3):
        raise InvalidLedger("incomplete product")
    if set(odds) not in ({"home", "away"}, {"home", "draw", "away"}, {"over", "under"}):
        raise InvalidLedger("unsupported side")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 1 for v in odds.values()):
        raise InvalidLedger("invalid odds")
    if not isinstance(p["probabilities"], dict) or not p["probabilities"] or not set(p["probabilities"]) <= {"market", "fixed91", "candidate"}:
        raise InvalidLedger("invalid probability models")
    for vector in p["probabilities"].values():
        if not isinstance(vector, dict) or set(vector) != set(odds):
            raise InvalidLedger("probability roster mismatch")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in vector.values()) or abs(sum(vector.values())-1) > 1e-6:
            raise InvalidLedger("invalid probabilities")
    if not isinstance(p["votes"], list):
        raise InvalidLedger("explicit vote list required")
    for vote in p["votes"]:
        keys(vote, "author_id side source_sha256 received_at published_at")
        sha(vote["source_sha256"])
        if not isinstance(vote["author_id"], str) or not vote["author_id"] or vote["side"] not in odds:
            raise InvalidLedger("invalid vote")
        if not stamp(vote["published_at"]) <= stamp(vote["received_at"]) <= received:
            raise InvalidLedger("post-receipt or postgame vote")


def verify_chain(records, ledger_id):
    previous = None
    captures, frozen, settled = {}, {}, set()
    prior_clock = None
    for i, r in enumerate(records, 1):
        keys(r, "schema ledger_id sequence previous_hash kind recorded_at payload record_hash")
        if r["schema"] != 1 or r["ledger_id"] != ledger_id or r["sequence"] != i or r["previous_hash"] != previous:
            raise InvalidLedger("broken chain/identity/sequence")
        if r["record_hash"] != digest({k: v for k, v in r.items() if k != "record_hash"}):
            raise InvalidLedger("record hash mismatch")
        now, p = stamp(r["recorded_at"]), r["payload"]
        if prior_clock is not None and now < prior_clock:
            raise InvalidLedger("clock moved backwards")
        if r["kind"] == "capture":
            capture_valid(p)
            identity = (p["event_id"], p["product"])
            if identity in frozen or now != stamp(p["received_at"]):
                raise InvalidLedger("capture after freeze or mismatched receipt")
            captures[r["record_hash"]] = r
        elif r["kind"] == "freeze":
            keys(p, "event_id product capture_hash cutoff_at")
            c = captures.get(p["capture_hash"])
            identity = (p["event_id"], p["product"])
            if c is None or identity in frozen or identity != (c["payload"]["event_id"], c["payload"]["product"]):
                raise InvalidLedger("invalid freeze reference")
            cutoff = stamp(c["payload"]["kickoff"])-timedelta(minutes=30)
            if stamp(p["cutoff_at"]) != cutoff or now > cutoff:
                raise InvalidLedger("late or incorrect cutoff")
            latest = [x for x in captures.values() if (x["payload"]["event_id"], x["payload"]["product"]) == identity][-1]
            if c != latest:
                raise InvalidLedger("freeze must use latest capture")
            frozen[identity] = r
        elif r["kind"] == "settlement":
            keys(p, "event_id product freeze_hash winner final_at source_sha256")
            sha(p["source_sha256"])
            identity = (p["event_id"], p["product"])
            f = frozen.get(identity)
            if f is None or f["record_hash"] != p["freeze_hash"] or identity in settled:
                raise InvalidLedger("unknown freeze or duplicate settlement")
            cp = captures[f["payload"]["capture_hash"]]["payload"]
            if not stamp(cp["kickoff"]) < stamp(p["final_at"]) <= now or p["winner"] not in {*cp["odds"], "refund"}:
                raise InvalidLedger("invalid final settlement")
            settled.add(identity)
        else:
            raise InvalidLedger("unknown record kind")
        previous, prior_clock = r["record_hash"], now
    return records


def checkpoint_message(records, ledger_id, witness_id, witnessed_at):
    verify_chain(records, ledger_id)
    if not records:
        raise InvalidLedger("empty checkpoint")
    return dict(schema="receipt-checkpoint-v1", ledger_id=ledger_id, size=len(records),
                tail_hash=records[-1]["record_hash"], witness_id=witness_id,
                witnessed_at=witnessed_at)


def verify_checkpoint(records, ledger_id, checkpoint, trusted_keys=None, *, test_only=False):
    """Verify signature/time/pinned identity; key trust and witness clock are external assumptions.

    Real certification ALWAYS loads the verifier-pinned policy. A caller cannot
    provide roots in real mode. Explicit test-only injected roots return a
    synthetic scope and can never enter authenticated_freezes.
    """
    if not test_only:
        if trusted_keys is not None:
            raise InvalidLedger("caller supplied trust roots cannot authenticate history")
        trusted_keys = pinned_trust()
    elif trusted_keys is None:
        raise InvalidLedger("synthetic test trust required")
    verify_chain(records, ledger_id)
    keys(checkpoint, "schema ledger_id size tail_hash witness_id witnessed_at signature_b64")
    c = {k: v for k, v in checkpoint.items() if k != "signature_b64"}
    trust = trusted_keys.get(c["witness_id"])
    if trust is None or trust.get("independent") is not True:
        raise InvalidLedger("untrusted witness")
    if c["schema"] != "receipt-checkpoint-v1" or c["ledger_id"] != ledger_id or type(c["size"]) is not int or not 0 < c["size"] <= len(records):
        raise InvalidLedger("checkpoint identity/size")
    at = stamp(c["witnessed_at"])
    if not stamp(trust["valid_from"]) <= at <= stamp(trust["valid_until"]):
        raise InvalidLedger("witness key outside trust interval")
    if records[c["size"]-1]["record_hash"] != c["tail_hash"]:
        raise InvalidLedger("checkpoint root mismatch; history rewritten")
    if any(stamp(r["recorded_at"]) > at for r in records[:c["size"]]):
        raise InvalidLedger("checkpoint predates receipt")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise InvalidLedger("signature verifier unavailable; cannot authenticate") from exc
    try:
        signature = base64.b64decode(checkpoint["signature_b64"], validate=True)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust["public_key_hex"])).verify(signature, canonical(c))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidLedger("invalid witness signature") from exc
    return {**c, "authentication_scope": "synthetic_test_only" if test_only else "external_pinned"}


def authenticated_freezes(records, ledger_id, checkpoints):
    verify_chain(records, ledger_id)
    verified = [verify_checkpoint(records, ledger_id, cp) for cp in checkpoints]
    return [r["record_hash"] for r in records if r["kind"] == "freeze" and any(
        c["authentication_scope"] == "external_pinned" and c["size"] >= r["sequence"] and stamp(c["witnessed_at"]) <= stamp(r["payload"]["cutoff_at"])
        for c in verified)]


class Ledger:
    """Single file writer with exclusive cooperative lock, O_APPEND and fsync.

    No rewrite API. OS/filesystem owner can bypass this; only external witness
    checkpoints detect whole-file rewriting. Crash-truncated records fail closed.
    """
    def __init__(self, path, ledger_id):
        self.path, self.ledger_id = Path(path), ledger_id

    def read(self):
        if not self.path.exists():
            return []
        lines = self.path.read_bytes().splitlines()
        if self.path.stat().st_size and not self.path.read_bytes().endswith(b"\n"):
            raise InvalidLedger("truncated record")
        try:
            records = [json.loads(line) for line in lines]
        except (ValueError, UnicodeError) as exc:
            raise InvalidLedger("invalid JSONL") from exc
        return verify_chain(records, self.ledger_id)

    @contextmanager
    def _lock(self):
        lock = self.path.with_suffix(self.path.suffix+".lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            yield
        finally:
            os.close(fd)
            lock.unlink()

    def append(self, kind, payload, recorded_at, *, source_bytes=None):
        payload = json.loads(canonical(payload))
        if kind == "capture":
            if not isinstance(source_bytes, bytes) or hashlib.sha256(source_bytes).hexdigest() != payload["source_sha256"]:
                raise InvalidLedger("raw source hash mismatch")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock():
            records = self.read()
            r = dict(schema=1, ledger_id=self.ledger_id, sequence=len(records)+1,
                     previous_hash=records[-1]["record_hash"] if records else None,
                     kind=kind, recorded_at=recorded_at, payload=payload)
            r["record_hash"] = digest(r)
            verify_chain(records+[r], self.ledger_id)
            with self.path.open("ab") as f:
                f.write(canonical(r)+b"\n")
                f.flush()
                os.fsync(f.fileno())
            return r


def predict_frozen(capture_payload):
    capture_valid(capture_payload)
    return {name: max(vector, key=vector.get) for name, vector in capture_payload["probabilities"].items()}
