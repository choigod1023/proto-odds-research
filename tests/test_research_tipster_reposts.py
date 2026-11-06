import gzip
import hashlib
import json

import pytest

from scripts.collect_tipster_comparison_sources import link
from src.public_tipster_votes import parse_page
from src.research_tipster_reposts import compare_archived_votes


PICK79 = "https://pick79.com/posts/detail/"
GOLI = "https://www.goli.tv/board/analysis/"
EARLY = "2026-10-26T07:00:00Z"
LATER = "2026-10-26T08:00:00Z"
KICKOFF = "2026-10-26T10:00:00Z"
BODY = (
    "Home have used a deeper rotation over the last three weeks. Their reserve guards "
    "consistently draw help before the second pass, opening the weak side corner. "
    "Away rely on early transition chances and have struggled to recover against this action."
)
OTHER = BODY.replace("deeper rotation", "shorter rotation")


@pytest.fixture
def archive(tmp_path):
    articles, prices = tmp_path / "articles", tmp_path / "prices"
    articles.mkdir()
    prices.mkdir()

    def save(root, url, raw, received):
        stem = str(len(list(root.glob("*.json"))))
        body = f"{stem}.body.gz"
        meta = dict(url=url, status=200, body_file=body, encoding="utf-8",
                    body_sha256=hashlib.sha256(raw).hexdigest(), received_at=received,
                    timestamp_basis="http_response_body_completed")
        (root / body).write_bytes(gzip.compress(raw))
        (root / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def price(product=3, kickoff=KICKOFF):
        url = f"https://www.betman.co.kr/{product}"
        meta = save(prices, url, b"official price", "2026-10-26T09:00:00Z")
        return dict(record_id=f"price-{product}-{kickoff}", kind="price", sport="bk", league="KBL",
                    home="Home", away="Away", kickoff=kickoff, source_url=url,
                    received_at=meta["received_at"], timestamp_basis=meta["timestamp_basis"],
                    timing_basis="actual_receipt", body_sha256=meta["body_sha256"],
                    payload=dict(product_id=f"G101:260130:{product}", market="winner_2way",
                                 period="full", line=None, settlement_rule="full_game_including_overtime",
                                 odds={"home": 1.8, "away": 2.0}))

    def vote(quote, url, author, *, text=BODY, selection="Home", received=EARLY):
        if url.startswith(PICK79):
            graph = [{"@type": "Article", "author": {"name": author}},
                     {"@type": "SportsEvent", "homeTeam": {"name": "Home"},
                      "awayTeam": {"name": "Away"}, "superEvent": {"name": "KBL"},
                      "sport": "Basketball", "startDate": quote["kickoff"],
                      "eventStatus": "https://schema.org/EventScheduled"}]
            html = f"""
                <script type="application/ld+json">{json.dumps({"@graph": graph})}</script>
                <article class="detail-view"><div class="detail-content">
                <div class="prose">{text}</div>
                <div class="section-widget"><h2>추천 픽</h2><div><div><div>승무패</div>
                <div>{selection} 승</div></div></div></div>
                </div></article>"""
        else:
            html = f"""
                <h1 id="page-heading">Home 대 Away KBL 10월 26일 맞대결</h1>
                <div class="writer_box"><span class="_userView">{author}</span></div>
                <div class="view_stat">2026-10-26 13:00</div>
                <div id="partner_content"><p>{text}</p><p>베팅 요약 및 추천</p>
                <p>{selection} 승</p></div>"""
        meta = save(articles, url, html.encode(), received)
        return link(parse_page(url, html, received), meta, [quote])

    def run(rows):
        return compare_archived_votes(rows, article_roots=[articles], price_roots=[prices])

    return price, vote, run, articles


def test_exact_cross_domain_body_changes_plurality_without_backfilling(archive):
    price, vote, run, _ = archive
    quote = price()
    original = vote(quote, PICK79 + "original/", "first")
    repost = vote(quote, GOLI + "1", "second", text=BODY.replace(" weeks. ", " weeks.\n "), received=LATER)
    revision = vote(quote, GOLI + "2", "second", text=OTHER, selection="Away",
                    received="2026-10-26T08:15:00Z")
    dissent = vote(quote, GOLI + "3", "third", text=OTHER, selection="Away")
    records = [quote, original, repost, revision, dissent, original]
    report = run(records)
    assert report == run(reversed(records))
    assert report["compared_products"] == report["products_with_reposts"] == 1
    row, = report["comparisons"]
    assert row["product"]["product_id"] == quote["payload"]["product_id"]
    assert row["existing_vote"]["counts"] == {"home": 2, "away": 1}
    assert row["existing_vote"]["selection"] == "home"
    assert row["deduplicated_vote"]["counts"] == {"home": 1, "away": 1}
    assert row["deduplicated_vote"]["selection"] is None
    group, = row["reposts"]
    assert group["kept_record_id"] == original["record_id"]
    assert group["removed_record_ids"] == [repost["record_id"]]
    assert group["domains"] == ["goli.tv", "pick79.com"]
    assert original["body_sha256"] != repost["body_sha256"]
    assert BODY not in json.dumps(report)


@pytest.mark.parametrize(("copied", "selection"), [(OTHER, "Home"), (BODY, "Away")])
def test_equal_pick_or_conflicting_picks_do_not_establish_reposts(archive, copied, selection):
    price, vote, run, _ = archive
    quote = price()
    report = run([quote, vote(quote, PICK79 + "one", "one"),
                  vote(quote, GOLI + "1", "two", text=copied, selection=selection)])
    row, = report["comparisons"]
    assert row["existing_vote"] == row["deduplicated_vote"]
    assert row["reposts"] == [] and report["products_with_reposts"] == 0


def test_same_domain_short_bodies_and_other_products_do_not_combine(archive):
    price, vote, run, _ = archive
    first, second = price(), price(4)
    rows = [first, second, vote(first, PICK79 + "one", "one"), vote(first, PICK79 + "two", "two"),
            vote(second, GOLI + "1", "three"), vote(first, GOLI + "2", "four", text="Home win")]
    result = run(rows)
    assert result["compared_products"] == 0 and result["products_with_reposts"] == 0
    assert result["holds"]["insufficient_verified_body_domains"] == 2


@pytest.mark.parametrize("failure", ["tampered", "late", "post_t30", "forged_link"])
def test_unverified_body_clock_or_link_cannot_establish_repost(archive, failure):
    price, vote, run, articles = archive
    quote = price()
    original = vote(quote, PICK79 + "one", "one")
    received = "2026-10-26T09:10:00Z" if failure == "late" else LATER
    copy = vote(quote, GOLI + "1", "two", received=received)
    if failure == "tampered":
        (articles / "1.body.gz").write_bytes(gzip.compress(b"changed"))
        copy["receipt_verified"] = True
    if failure == "post_t30":
        manifest = articles / "1.json"
        meta = json.loads(manifest.read_bytes())
        meta["received_at"] = copy["received_at"] = "2026-10-26T09:30:01Z"
        copy["published_at"] = EARLY
        manifest.write_text(json.dumps(meta), encoding="utf-8")
    if failure == "forged_link":
        copy["payload"]["author"] = "unverified"
    result = run([quote, original, copy])
    assert result["compared_products"] == 0 and result["products_with_reposts"] == 0


def test_existing_author_conflict_is_excluded_before_deduplication(archive):
    price, vote, run, _ = archive
    quote = price()
    report = run([quote, vote(quote, PICK79 + "one", "one"),
                  vote(quote, GOLI + "1", "two"),
                  vote(quote, GOLI + "2", "two", selection="Away")])
    assert report["compared_products"] == 0
    assert report["vote_rejections"]["conflicting_first_author_votes"] == 1


def test_retrospective_price_cannot_define_comparison_cutoff(archive):
    price, vote, run, _ = archive
    quote = price()
    rows = [quote, vote(quote, PICK79 + "one", "one"), vote(quote, GOLI + "1", "two")]
    quote["timing_basis"] = "retrospective"
    result = run(rows)
    assert result["compared_products"] == 0
    assert result["holds"]["no_eligible_quote_or_vote"] == 1
    assert result["price_rejections"]["no_actual_pregame_receipt"] == 1


def test_identical_bodies_for_different_kickoffs_are_never_combined(archive):
    price, vote, run, _ = archive
    first, second = price(), price(kickoff="2026-10-26T11:00:00Z")
    rows = [first, second, vote(first, PICK79 + "one", "one"), vote(second, GOLI + "1", "two")]
    result = run(rows)
    assert result["compared_products"] == 0
    assert result["holds"]["insufficient_verified_body_domains"] == 2
