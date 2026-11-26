"""Pin committed A-F artifacts for a portable, offline-verifiable synthesis."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
STUDIES = [
    ("A", "forward-auth-ledger", 431, "9e9fca04", "results/results.json", "validation.json", 1899, 54),
    ("B", "selective-accuracy-gates", 427, "4205caad", "results.json", None, 1850, 5),
    ("C", "market-margin-bias", 428, "4e5d8b4d", "results.json", "strict-comparison.json", 1849, 4),
    ("D", "author-trust-votes", 426, "ba51a2ea", "results.json", "manifest.json", 1852, 7),
    ("E", "shrunk-market-residual", 430, "fd95b6f8", "results/results.json", "validation/validation.json", 1866, 21),
    ("F", "league-market-signal", 429, "a71e6648", "results/results.json", "validation.json", 1867, 22),
]


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def refresh():
    items = []
    for letter, topic, pr, protocol_commit, result_path, validation_path, passed, focused in STUDIES:
        branch = f"codex/{topic}-20261030"
        head = git("rev-parse", branch).decode().strip()
        protocol_head = git("rev-parse", protocol_commit).decode().strip()
        git("merge-base", "--is-ancestor", protocol_head, head)
        paths = [f"docs/research/2026-10-30-{topic}.md", f"docs/research/2026-10-30-{topic}-protocol.json",
                 f"experiments/{topic}-20261030/{result_path}"]
        if validation_path:
            paths.append(f"experiments/{topic}-20261030/{validation_path}")
        if letter == "B":
            paths.append(f"experiments/{topic}-20261030/publication-audit.jsonl")
        artifact = []
        for source in paths:
            raw = git("show", f"{head}:{source}")
            dest = HERE / "evidence" / letter / source
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            artifact.append({"source": source, "local": dest.relative_to(HERE).as_posix(),
                             "sha256": hashlib.sha256(raw).hexdigest()})
            if source.endswith(".md"):
                assert raw.decode("utf-8").startswith("아니오"), topic
        items.append(dict(letter=letter, topic=topic, branch=branch, commit=head, protocol_commit=protocol_head,
                          draft_pr=pr, url=f"https://github.com/choigod1023/proto-odds-research/pull/{pr}",
                          full_tests_passed=passed, focused_tests_passed=focused,
                          default_skipped=6, subtests_passed=93, artifacts=artifact))
    (HERE / "study-evidence.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    verify()


def verify():
    items = json.loads((HERE / "study-evidence.json").read_text(encoding="utf-8"))
    count = 0
    for item in items:
        for artifact in item["artifacts"]:
            path = (HERE / artifact["local"]).resolve()
            assert path.is_relative_to(HERE.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"], path
            count += 1
    assert {x["letter"] for x in items} == set("ABCDEF")
    print(json.dumps({"studies": len(items), "verified_committed_artifacts": count, "network_required": False}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--refresh-from-local-git", action="store_true")
    args = p.parse_args()
    refresh() if args.refresh_from_local_git else verify()
