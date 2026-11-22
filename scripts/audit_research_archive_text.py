"""Check source bytes and explicit aliases before blaming console mojibake.

This is an offline research diagnostic, not an eligibility or price verifier.
No lossy decoding, fuzzy matching, outcome-based identity repair or source edits.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def audit(path, league, aliases=None):
    blob = path.read_bytes()
    raw = gzip.decompress(blob) if path.suffix == ".gz" else blob
    text = raw.decode("utf-8-sig", errors="strict")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    selected = [row for row in rows if row.get("league") == league]
    names = Counter()
    for row in selected:
        for side in ("home", "away"):
            name = row.get(side)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("missing_team_name")
            names[name] += 1
    literal_replacement = {name: count for name, count in names.items() if "\ufffd" in name}
    targets = defaultdict(list)
    unmapped = []
    if aliases is not None:
        for name in names:
            if name not in aliases:
                unmapped.append(name)
            else:
                targets[str(aliases[name])].append(name)
    # Multiple spellings can legitimately name one team. Flag the situation for
    # explicit review; do not infer whether the aliases are correct from winners.
    many_to_one = {key: value for key, value in targets.items() if len(value) > 1}
    return dict(schema="research-archive-text-audit-v1", source_sha256=hashlib.sha256(blob).hexdigest(),
                league=league, rows=len(selected), distinct_team_names=len(names), raw_utf8_valid=True,
                literal_replacement_names=literal_replacement, team_names=dict(sorted(names.items())),
                aliases_supplied=aliases is not None, unmapped_names=sorted(unmapped),
                many_names_to_one_alias=many_to_one,
                limits=["Valid UTF-8 and no replacement characters do not authenticate team identity",
                        "Console rendering cannot prove source-byte corruption",
                        "This audit does not verify pregame timing, prices, results, completeness or model performance"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--league", required=True)
    parser.add_argument("--aliases", type=Path, help="Explicit source name to canonical ID JSON map")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    aliases = json.loads(args.aliases.read_text(encoding="utf-8-sig")) if args.aliases else None
    result = audit(args.archive, args.league, aliases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({k: result[k] for k in ("rows", "distinct_team_names", "raw_utf8_valid", "literal_replacement_names")}, ensure_ascii=True))
