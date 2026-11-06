"""Offline comparison of archived public winner votes with exact reposts collapsed.

Run with python -m src.research_tipster_reposts --help from the repository root.
The baseline is the existing research author-first vote at the selected saved
quote. Only products with comparable narrative evidence on two domains are
reported. Receipt order chooses a representative, without attributing authorship.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scripts.collect_tipster_comparison_sources import link
from scripts.run_pregame_multisport_study import normalize, read_records, receipt_index
from src.multisport_pregame_study import choose, digest, moment, physical, unique_top
from src.public_tipster_votes import parse_page, safe_url


RECEIPT_CLOCK = "http_response_body_completed"
MAX_ARTICLE_BYTES = 2_000_000  # Same bound as the archived public article collector.
PRODUCT_FIELDS = ("product_id", "period", "market", "line", "settlement_rule")


def body_fingerprint(url, html):
    """Hash long, exact narrative text after Unicode and whitespace normalization."""
    domain = urlsplit(url).hostname.removeprefix("www.")
    selector = {"pick79.com": "article.detail-view .detail-content > .prose",
                "goli.tv": "#partner_content"}[domain]
    bodies = BeautifulSoup(html, "lxml").select(selector)
    if len(bodies) != 1:
        return None
    body = bodies[0]
    for tag in body.select("script, style, noscript, template"):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    if domain == "goli.tv":
        text = text.partition("베팅 요약 및 추천")[0]
    text = " ".join(unicodedata.normalize("NFC", text).split())
    # Short text such as team names and pick summaries is weak evidence of copying.
    return hashlib.sha256(text.encode()).hexdigest() if sum(c.isalpha() for c in text) >= 160 else None


def article_evidence(roots):
    """Read the existing collector's sidecars and gzip bodies, using no fetch path."""
    evidence, audit = {}, Counter()
    for root in roots:
        root = root.resolve(strict=True)
        for path in sorted(root.glob("*.json")):
            try:
                meta = json.loads(path.read_bytes())
                if not isinstance(meta, dict) or not safe_url(meta.get("url", "")):
                    continue
                audit["article_manifests"] += 1
                if meta.get("status") != 200 or meta.get("timestamp_basis") != RECEIPT_CLOCK:
                    audit["no_completed_article_receipt"] += 1
                    continue
                url, received = meta["url"], moment(meta["received_at"])
                body_path = (root / meta["body_file"]).resolve()
                if not body_path.is_relative_to(root):
                    raise ValueError("body_path_escapes_archive")
                with gzip.open(body_path, "rb") as handle:
                    raw = handle.read(MAX_ARTICLE_BYTES + 1)
                sha = hashlib.sha256(raw).hexdigest()
                if len(raw) > MAX_ARTICLE_BYTES or sha != meta["body_sha256"]:
                    raise ValueError("article_body_hash_or_size_mismatch")
                audit["hash_verified_article_bodies"] += 1
                html = raw.decode(meta["encoding"])
                article = parse_page(url, html, meta["received_at"])
                fingerprint = body_fingerprint(url, html)
            except (OSError, EOFError, LookupError, TypeError, ValueError, AttributeError) as exc:
                audit["unusable:" + (str(exc) if type(exc) is ValueError else type(exc).__name__)] += 1
                continue
            key = (sha, url, received.isoformat(), RECEIPT_CLOCK)
            entry = (meta, article, fingerprint)
            if key in evidence and evidence[key][1:] != entry[1:]:
                raise ValueError("conflicting_archive_decoding_for_same_receipt")
            evidence[key] = entry
            audit["parsed_article_bodies"] += 1
            if not fingerprint:
                audit["missing_or_short_narrative"] += 1
    return evidence, dict(audit)


def vote_summary(rows, sides):
    tallies = Counter(r["payload"]["selection"] for r in rows)
    counts = {side: tallies[side] for side in sides}
    return dict(counts=counts, selection=unique_top(counts),
                record_ids=sorted(r["record_id"] for r in rows))


def compare_archived_votes(records, *, article_roots, price_roots):
    articles, article_audit = article_evidence(article_roots)
    prices, price_audit = receipt_index(price_roots)
    groups, identities, holds = defaultdict(dict), {}, Counter()
    for original in records:
        try:
            kind = original.get("kind")
            if kind not in {"price", "tipster"}:
                continue
            record_id = original["record_id"]
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("invalid_record_id")
            key = (physical(original), digest([original["payload"].get(k) for k in PRODUCT_FIELDS]))
            row = normalize(original, articles if kind == "tipster" else prices, set())
            checksum = digest(row)
        except (KeyError, AttributeError, TypeError, ValueError):
            holds["malformed_record"] += 1
            continue
        if record_id in identities and identities[record_id] != checksum:
            raise ValueError("conflicting_record_id")
        identities[record_id] = checksum
        groups[key][record_id] = row

    comparisons = []
    price_rejections, vote_rejections = Counter(), Counter()
    for (fixture, _), rows in sorted(groups.items()):
        if all(r["kind"] == "price" for r in rows.values()):
            continue
        chosen, reasons = choose(list(rows.values()))
        price_rejections.update(reasons.get("price", {}))
        vote_rejections.update(reasons.get("vote", {}))
        if chosen is None or not chosen["vote_record_ids"]:
            holds["no_eligible_quote_or_vote"] += 1
            continue

        baseline = [rows[record_id] for record_id in chosen["vote_record_ids"]]
        quote = rows[chosen["price_record_id"]]
        domains = set()
        by_body = defaultdict(list)
        try:
            for vote in baseline:
                url = vote["source_url"]
                key = (vote["body_sha256"], url, moment(vote["received_at"]).isoformat(), RECEIPT_CLOCK)
                meta, article, fingerprint = articles[key]
                linked = link(article, meta, [quote])
                if any(vote["payload"].get(k) != v for k, v in linked["payload"].items()):
                    raise ValueError("saved_vote_disagrees_with_archived_article")
                if fingerprint:
                    domain = urlsplit(url).hostname.removeprefix("www.")
                    domains.add(domain)
                    by_body[fingerprint].append((domain, vote))
        except (KeyError, TypeError, ValueError, StopIteration):
            holds["accepted_vote_cannot_be_relinked"] += 1
            continue
        if len(domains) < 2:
            holds["insufficient_verified_body_domains"] += 1
            continue

        reposts, removed = [], set()
        for fingerprint, members in sorted(by_body.items()):
            member_domains = sorted({domain for domain, _ in members})
            if len(member_domains) < 2 or len({r["payload"]["selection"] for _, r in members}) != 1:
                continue
            ordered = sorted((r for _, r in members), key=lambda r: (moment(r["received_at"]), r["record_id"]))
            removed_ids = [r["record_id"] for r in ordered[1:]]
            removed.update(removed_ids)
            reposts.append(dict(normalized_body_sha256=fingerprint, domains=member_domains,
                                kept_record_id=ordered[0]["record_id"], removed_record_ids=removed_ids))
        remaining = [r for r in baseline if r["record_id"] not in removed]
        comparisons.append(dict(fixture=fixture, as_of=chosen["as_of"], price_record_id=quote["record_id"],
                                product={k: chosen["product"].get(k) for k in PRODUCT_FIELDS},
                                existing_vote=vote_summary(baseline, chosen["odds"]),
                                deduplicated_vote=vote_summary(remaining, chosen["odds"]), reposts=reposts))

    return dict(schema="archived-tipster-reposts-v1", compared_products=len(comparisons),
                products_with_reposts=sum(bool(r["reposts"]) for r in comparisons),
                article_audit=article_audit, price_receipt_audit=price_audit,
                holds=dict(holds), price_rejections=dict(price_rejections),
                vote_rejections=dict(vote_rejections), comparisons=comparisons)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True,
                        help="Saved tipster or official price records (repeatable)")
    parser.add_argument("--article-root", type=Path, action="append", required=True,
                        help="Archived article sidecar and body directory (repeatable)")
    parser.add_argument("--price-root", type=Path, action="append", required=True,
                        help="Official price receipt directory (repeatable)")
    args = parser.parse_args()
    report = compare_archived_votes((row for path in args.input for row in read_records(path)),
                                    article_roots=args.article_root, price_roots=args.price_root)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
