import gzip
import hashlib
import json
from pathlib import Path

import pytest
import requests

from scripts import prospective_tipster_pairs as cli
from src import prospective_tipster_pairs as pairs


ROUND = "260132"
EARLY = "2026-11-06T03:00:00+00:00"
QUOTE = "2026-11-06T04:00:00+00:00"
KICKOFF = "2026-11-06T05:00:00+00:00"
RESULT = "2026-11-06T07:00:00+00:00"
AS_OF = "2026-11-06T08:00:00+00:00"
PICK79 = "https://pick79.com/posts/detail/"
GOLI = "https://www.goli.tv/board/analysis/"
BODY = (
    "Home press aggressively from the first whistle and have forced turnovers "
    "in the opposing half in each of their recent fixtures. Away tend to concede "
    "territory after halftime and have struggled to clear second balls near the penalty area."
)


def ms(value):
    return int(pairs.moment(value).timestamp() * 1000)


def market(**changes):
    return dict(itemCode="SC", betId="1", betTypId="1", betNm="축구 승무패", betTypNm="승무패",
                winTxt="승", drawTxt="무", loseTxt="패", handi=0, winHandi=0, drawHandi=0, loseHandi=0,
                live="N", unsetSchedule="N", unsetEndDate="N", gameReject="0", buyReject="0", protoStatus="2",
                leagueCode="52", leagueName="잉글랜드 프리미어리그", leagueShortName="EPL",
                homeName="Home", awayName="Away", homeId="H", awayId="A",
                matchSeq=7, gameDate=ms(KICKOFF), endDate=ms(KICKOFF), winAllot=2.2, drawAllot=3.2,
                loseAllot=3.3, gameResult=None, mchScore=None) | changes


def detail(rows, *, active=True):
    keys = list(dict.fromkeys(key for row in rows for key in row))
    return dict(gmTs=ROUND, currentLottery=dict(gmId="G101", gmTs=int(ROUND), saleProgress=active,
                                               saleStartDate=ms(EARLY), saleEndDate=ms(KICKOFF)),
                compSchedules=dict(keys=keys, datas=[[row.get(k) for k in keys] for row in rows]))


def archive(root, meta_path, raw, **meta):
    root.mkdir(parents=True, exist_ok=True)
    body = meta_path + ".body.gz"
    (root / body).write_bytes(gzip.compress(raw))
    (root / (meta_path + ".json")).write_text(json.dumps(dict(meta, body_file=body,
        timestamp_basis=pairs.RECEIPT_CLOCK, body_sha256=hashlib.sha256(raw).hexdigest())), encoding="utf-8")


def save_snapshot(capture, at, data, **meta):
    raw = json.dumps(data, ensure_ascii=False).encode()
    archive(capture / "price-evidence" / capture.name, f"round-{ROUND}", raw,
            **(dict(url=pairs.DETAIL, received_at=at, http_status=200,
                    request_json=dict(gmId="G101", gmTs=ROUND)) | meta))


def article_html(domain, author, selection, text):
    if domain == PICK79:
        graph = [{"@type": "Article", "author": {"name": author}},
                 {"@type": "SportsEvent", "homeTeam": {"name": "Home"},
                  "awayTeam": {"name": "Away"}, "superEvent": {"name": "EPL"},
                  "sport": "Soccer", "startDate": KICKOFF,
                  "eventStatus": "https://schema.org/EventScheduled"}]
        return f"""
        <script type="application/ld+json">{json.dumps({"@graph": graph})}</script>
        <article class="detail-view"><div class="detail-content"><div class="prose">{text}</div>
        <div class="section-widget"><h2>추천 픽</h2><div><div><div>승무패</div>
        <div>{selection} 승</div></div></div></div></div></article>"""
    return f"""
    <h1 id="page-heading">Home 대 Away EPL 11월 6일 맞대결</h1>
    <div class="writer_box"><span class="_userView">{author}</span></div>
    <div class="view_stat">2026-11-06 11:30</div>
    <div id="partner_content"><p>{text}</p><p>베팅 요약 및 추천</p>
    <p>승/무/패: {selection} 승</p></div>"""


def vote(capture, domain, number, author, selection, *, text=BODY, at=EARLY):
    archive(capture / "article-evidence", str(number), article_html(domain, author, selection, text).encode(),
            url=domain + (str(number) + "/" if domain == PICK79 else str(number)),
            status=200, encoding="utf-8", received_at=at)


@pytest.fixture
def study(tmp_path):
    articles, quote, result = (tmp_path / name for name in ("articles", "quote", "result"))
    for number in (1, 2, 3):
        vote(articles, PICK79, number, str(number), "Home")
    vote(articles, GOLI, 4, "4", "Home")
    vote(articles, GOLI, 5, "5", "Away", text=BODY.replace("territory", "space"))
    vote(articles, GOLI, 6, "6", "Away", text=BODY.replace("half", "third"))
    save_snapshot(quote, QUOTE, detail([market()]))
    save_snapshot(result, RESULT, detail([market(protoStatus="4", gameResult="2", mchScore="1:2")], active=False))
    return articles, quote, result


def evaluate(study, **kwargs):
    return pairs.evaluate(ROUND, capture_dirs=study, evaluated_at=AS_OF, **kwargs)


def test_same_quote_paired_roi_uses_pregame_prices_and_later_official_result(study):
    articles, quote, result = study
    late = "2026-11-06T04:15:00+00:00"
    vote(articles, GOLI, 9, "9", "Home", text=BODY.replace("territory", "possession"), at=late)
    save_snapshot(result, RESULT, detail([market(protoStatus="4", gameResult="2", mchScore="1:2",
                                               loseAllot=5.1)], active=False))
    report = evaluate(study)
    event, = report["events"]
    assert event["as_of"] == QUOTE
    assert event["product"]["settlement_rule"] == pairs.SC_RULE
    assert event["existing_vote"]["counts"] == {"home": 4, "draw": 0, "away": 2}
    assert event["deduplicated_vote"]["counts"] == {"home": 1, "draw": 0, "away": 2}
    assert event["result"]["winner"] == "away"
    paired = report["paired_vs_existing_vote"]
    assert paired["graded_products"] == paired["decisive_products"] == 1
    assert paired["arms"]["existing_vote"]["roi_pct"] == -100
    assert paired["arms"]["deduplicated_vote"]["roi_pct"] == 230
    assert paired["deduplicated_minus_baseline_pp"] == {"accuracy_pct": 100, "roi_pct": 330}
    pending = evaluate((articles, quote))
    assert pending["events"][0]["existing_vote"] == event["existing_vote"]
    assert pending["events"][0]["deduplicated_vote"] == event["deduplicated_vote"]
    assert pending["paired_vs_existing_vote"]["pending_products"] == 1
    assert pending["paired_vs_existing_vote"]["arms"]["existing_vote"]["roi_pct"] is None


@pytest.mark.parametrize(("at", "changes", "reason"), [
    (QUOTE, dict(winTxt="unexpected"), "unknown_settlement_terms"),
    (QUOTE, dict(protoStatus="0"), "product_not_confirmed_on_sale"),
    (QUOTE, dict(endDate=ms(QUOTE)), "outside_verified_sale_window"),
    ("2026-11-06T04:30:00+00:00", {}, "outside_quote_window"),
])
def test_unknown_or_unavailable_quote_is_held(study, at, changes, reason):
    _, quote, _ = study
    save_snapshot(quote, at, detail([market(**changes)]))
    report = evaluate(study)
    assert report["comparison_products"] == 0
    assert reason in report["held_products"][f"G101:{ROUND}:7"]


def test_exact_fixture_must_have_one_product(study):
    _, quote, _ = study
    save_snapshot(quote, QUOTE, detail([market(), market(matchSeq=8, buyReject="4")]))
    report = evaluate(study)
    assert report["comparison_products"] == 0
    assert report["vote_link_attempt_counts"]["ambiguous_proto_product_for_article"] == 6


@pytest.mark.parametrize(("changes", "status"), [
    (dict(gameResult="4", winAllot=0, drawAllot=0, loseAllot=0,
          winTxt=None, drawTxt=None, loseTxt=None, mchScore=":"), "void"),
    (dict(gameResult="1", mchScore="1:1"), "settled"),
    (dict(gameResult="1", mchScore="2:1"), "pending"),
    (dict(gameResult="2", mchScore="1:2", homeId="other"), "pending"),
    (dict(gameResult="2", mchScore="1:2", betId="118"), "pending"),
])
def test_official_draw_void_and_mismatched_labels(study, changes, status):
    _, _, result = study
    save_snapshot(result, RESULT, detail([market(**({"protoStatus": "4"} | changes))], active=False))
    report = evaluate(study)
    assert report["events"][0]["result"]["status"] == status
    paired = report["paired_vs_existing_vote"]
    if status == "void":
        assert paired["void_products"] == 1 and paired["decisive_products"] == 0
        assert paired["arms"]["existing_vote"]["accuracy_pct"] is None
        assert paired["arms"]["existing_vote"]["roi_pct"] == 0


def test_later_quote_keeps_first_comparison_and_pending_revision_withholds_result(study, tmp_path):
    articles, quote, result = study
    revision = tmp_path / "revision"
    later = "2026-11-06T04:15:00+00:00"
    vote(articles, GOLI, 9, "9", "Home", text=BODY.replace("territory", "possession"),
         at="2026-11-06T04:01:00+00:00")
    save_snapshot(revision, later, detail([market(winAllot=1.8)]))
    correction = tmp_path / "correction"
    save_snapshot(correction, AS_OF, detail([market(protoStatus="3")], active=False))
    report = evaluate((correction, revision, articles, quote, result))
    event, = report["events"]
    assert event["as_of"] == QUOTE
    assert event["odds"]["home"] == 2.2
    assert event["deduplicated_vote"]["selection"] == "away"
    assert event["result"]["reason"] == "official_product_result_not_final"
    assert report["status"] == "pending_official_settlement"


def test_existing_price_capture_root_has_same_replay(study):
    articles, quote, result = study
    report = pairs.evaluate(ROUND, article_roots=[articles / "article-evidence"],
                            price_roots=[p / "price-evidence" / p.name for p in (quote, result)],
                            evaluated_at=AS_OF)
    assert report["events"] == evaluate(study)["events"]


def test_tampered_quote_cannot_become_eligible(study):
    _, quote, _ = study
    body = next(quote.rglob("*.body.gz"))
    body.write_bytes(gzip.compress(b"modified"))
    report = evaluate(study)
    assert report["comparison_products"] == 0
    assert any(r.get("reason") == "round_body_hash_or_size_mismatch" for r in report["price_receipts"])


def test_recurring_capture_preserves_articles_on_tls_failure(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        encoding = "utf-8"
        headers = {}

        def __init__(self, text):
            self.raw = text.encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def iter_content(self, _):
            yield self.raw

    urls = []
    def get(_, url, **kwargs):
        urls.append(url)
        assert kwargs["allow_redirects"] is False
        if url == cli.tipsters.LISTINGS[0]:
            return Response(f'<a href="{PICK79}1/">1</a>' + " " * 20_000)
        if url == cli.tipsters.LISTINGS[-1]:
            return Response("")
        return Response(article_html(PICK79, "one", "Home", BODY))

    payloads = []
    response_body = json.dumps(detail([market()]))

    def post(url, *, json, **kwargs):
        payloads.append((url, json))
        assert kwargs["allow_redirects"] is False
        if len(payloads) == 1:
            raise requests.exceptions.ConnectionError("[WinError 10054] TLS handshake")
        return Response(response_body)

    monkeypatch.setattr(cli.tipsters.requests.Session, "get", get)
    monkeypatch.setattr(cli.tipsters, "MAX_LISTING_BYTES", 30_000)
    monkeypatch.setattr(cli.tipsters, "MAX_ARTICLE_BYTES", 10_000)
    monkeypatch.setattr(cli.proto.requests, "post", post)
    monkeypatch.setattr(cli.proto, "EVIDENCE", tmp_path / "global-price-stop")
    monkeypatch.setattr(cli.tipsters, "now", lambda: EARLY)
    monkeypatch.setattr(cli.proto, "now", lambda: QUOTE)
    first = cli.capture(ROUND, tmp_path)
    assert first["articles"]["parsed_articles"] == 1
    assert first["official"]["status"] == "pending"
    assert "10054" in first["official"]["reason"]
    assert len(urls) == 3 and len(payloads) == 1
    first_path = Path(first["capture_dir"])
    second = cli.capture(ROUND, tmp_path, article_requests=0)
    assert second["official"]["status"] == "completed"
    assert first_path != Path(second["capture_dir"])
    assert len(urls) == 3 and len(payloads) == 2
    assert all(url == pairs.DETAIL and request["gmTs"] == ROUND for url, request in payloads)
    report = evaluate([first_path, Path(second["capture_dir"])])
    assert report["article_audit"]["parsed_article_bodies"] == 1
    assert report["comparison_products"] == 0
    assert report["held_products"][f"G101:{ROUND}:7"] == ["insufficient_verified_body_domains"]


def test_frozen_operating_requires_matching_offer_and_roster(study, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    from ai_decision import event_id, offer_id, selection_id
    from featured_pick import VERSION

    game = dict(sport="sc", league="EPL", home="Home", away="Away", year=2026, round=132,
                date="11.06(금) 14:00")
    options = []
    for label, odds in (("홈", 2.2), ("무", 3.2), ("원정", 3.3)):
        option = {"게임번호": "007", "선택": label, "market": "승무패", "label": "", "line": None}
        options.append(dict(market="승무패", label="", line=None, selection=label, odds=odds,
                            selection_id=selection_id(game, option), offer_id=offer_id(game, option)))
    pick = dict(options[0], version=VERSION, game_no="007", settlement_rule="official_winner")
    row = dict(record_type="prediction", event_id=event_id(game), kickoff=KICKOFF,
               features=dict(fixture=game, options=options), predictions=dict(featured_pick=pick),
               as_of=EARLY, captured_at=EARLY, market_observed_at=EARLY,
               snapshot_id="saved", ledger_sequence=1, record_hash="test")
    report = evaluate(study, ledger_records=[row])
    assert report["events"][0]["frozen_operating_pick"]["selection"] == "home"
    assert report["paired_vs_frozen_operating_pick"]["graded_products"] == 1
    options[2]["odds"] = 3.35
    mismatch = evaluate(study, ledger_records=[row])
    assert mismatch["paired_vs_frozen_operating_pick"]["paired_products"] == 0
    assert mismatch["events"][0]["frozen_operating_pick"]["reason"] == "frozen_offer_or_price_roster_mismatch"
