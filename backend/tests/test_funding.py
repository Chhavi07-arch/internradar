"""Headlines below are real, copied from Entrackr / Inc42 / YourStory feeds on 2026-11-18."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel import select

from internradar.config import WatchedCompany, Watchlist
from internradar.funding.boards import check_board, slug_candidates
from internradar.funding.feeds import FeedItem, parse_rss
from internradar.funding.headlines import INR_PER_USD, parse_amount, parse_headline
from internradar.funding.radar import _record, funded_companies, run_radar
from internradar.models import FundingEvent, FundingMention


@pytest.mark.parametrize(
    ("headline", "company", "amount", "round_", "lead"),
    [
        ("Physical AI startup Humyn Labs raises $47 Mn in equity and debt",
         "Humyn Labs", "$47 Mn", None, None),
        ("IvyCap Ventures leads $11 Mn Series B round in robotics company Botsync",
         "Botsync", "$11 Mn", "Series B", "IvyCap Ventures"),
        ("AI video production startup Lemonpeel raises $1 Mn in pre-seed round led by Shastra VC",
         "Lemonpeel", "$1 Mn", "Pre-Seed", "Shastra VC"),
        ("Soket AI Raises $4 Mn From Bajaj Finserv To Expand Its AI Model Horizon",
         "Soket AI", "$4 Mn", None, "Bajaj Finserv"),
        ("Recove Ventures Nets ₹56 Cr To Expand Recycling Business",
         "Recove Ventures", "₹56 Cr", None, None),
        ("Coimbatore-based industrial AI startup CountAI raises Rs 10 crore from Equirus InnovateX",
         "CountAI", "Rs 10 crore", None, "Equirus InnovateX"),
        ("Bengaluru's lending app Olyv raises $6 million as it chases $1 billion AUM by FY29",
         "Olyv", "$6 million", None, None),
        ("Ex-TVF director's AI video startup Lemonpeel raises $1 million to build its platform",
         "Lemonpeel", "$1 million", None, None),
        ("altM raises $10.3M in Series A funding led by Rainmatter, Mela Ventures",
         "altM", "$10.3M", "Series A", "Rainmatter"),
        ("Can an AI agent run a factory? Morsa raises $1.5 million to find out",
         "Morsa", "$1.5 million", None, None),
        ("Exclusive: Shree Anandhaas raises Rs 110 Cr, valuation jumps to Rs 1,900 Cr",
         "Shree Anandhaas", "Rs 110 Cr", None, None),
    ],
)  # fmt: skip
def test_parse_headline(headline, company, amount, round_, lead):
    deal = parse_headline(headline)
    assert deal is not None
    assert (deal.company, deal.amount_text, deal.round, deal.lead_investors) == (
        company, amount, round_, lead,
    )  # fmt: skip


@pytest.mark.parametrize(
    "headline",
    [
        "Deeptech Startup Enerzolve To Raise $20 Mn From Jungle Ventures, Others",  # not yet
        "Daksham Capital raises Rs 125 Cr in first close of Rs 300 Cr fund",  # a VC fund
        "Norwest closes $4B fund to back AI, healthcare and financial infra",
        "Ribbit Capital sells Rs 1,905 Cr stake in Groww through block deal",
        "Moglix posts Rs 6,129 Cr revenue and Rs 160 Cr loss in FY26",
        "Blinkit Brings GTA 6 To 11 Cities Ahead Of Launch",
    ],
)
def test_non_rounds_are_rejected(headline):
    assert parse_headline(headline) is None


def test_tech_flag():
    assert parse_headline("Soket AI Raises $4 Mn From Bajaj Finserv").is_tech
    assert parse_headline("Botsync Raises $11 Mn To Connect Robots And Machines").is_tech
    assert not parse_headline("Egg brand Henfruit raises $15 Mn from Lok Capital").is_tech


def test_parse_amount_converts_inr():
    assert parse_amount("$47 Mn")[1] == 47e6
    assert parse_amount("$1.9 billion")[1] == 1.9e9
    assert parse_amount("Rs 1,905 Cr")[1] == pytest.approx(1905e7 / INR_PER_USD)
    assert parse_amount("no money here") == (None, None)


def test_parse_rss():
    xml = b"""<rss><channel>
      <item><title> Botsync raises $11M\r\n </title><link>https://x.com/a</link>
            <pubDate>Wed, 18 Nov 2026 18:10:59 +0530</pubDate></item>
      <item><title>No link</title></item>
    </channel></rss>"""
    [item] = parse_rss("entrackr", xml)
    assert item.title == "Botsync raises $11M"
    assert item.published_at == datetime(2026, 11, 18, 12, 40, 59, tzinfo=UTC)


def test_slug_candidates():
    assert slug_candidates("Humyn Labs") == ["humyn", "humynai", "humyn-ai", "humynhq"]
    assert slug_candidates("Soket AI")[:3] == ["soketai", "soket-ai", "soket"]


# --- careers board verification -----------------------------------------------------------


def _ashby(title: str, locations: list[str]) -> httpx.Client:
    jobs = [
        {"id": str(i), "title": "Engineer", "location": loc, "jobUrl": f"https://j/{i}"}
        for i, loc in enumerate(locations)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json={"jobs": jobs})
        return httpx.Response(200, text=f"<html><title>{title}</title></html>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_board_accepted_when_name_matches_and_hires_in_india():
    match = check_board(_ashby("Sarvam Jobs", ["Bengaluru", "SF"]), "Sarvam AI", "ashby", "sarvam")
    assert match is not None and match.india_jobs == 1


def test_board_rejected_when_it_belongs_to_another_company():
    client = _ashby("Navi AI Jobs", ["San Francisco"])  # real case: not Navi the fintech
    assert check_board(client, "Navi", "ashby", "navi") is None


def test_board_rejected_when_name_differs():
    client = _ashby("Slice Pizza Jobs", ["Bengaluru"])
    assert check_board(client, "Slice", "ashby", "slice") is None


# --- radar bookkeeping --------------------------------------------------------------------


def _item(url: str, title: str, days_ago: int = 0, feed: str = "entrackr") -> FeedItem:
    return FeedItem(feed, title, url, datetime.now(UTC) - timedelta(days=days_ago))


def test_same_round_from_several_outlets_is_one_event(session):
    _record(session, _item("https://e/1", "Botsync raises $11 Mn to automate factories", 1))
    _record(session, _item("https://i/2", "IvyCap Ventures leads $11 Mn Series B round in "
                                         "robotics company Botsync", 0, "inc42"))  # fmt: skip
    _record(session, _item("https://e/1", "Botsync raises $11 Mn to automate factories", 1))

    [event] = session.exec(select(FundingEvent)).all()
    assert event.round == "Series B"  # filled in by the second outlet
    assert event.lead_investors == "IvyCap Ventures"
    assert len(session.exec(select(FundingMention)).all()) == 2  # rerun did not double count


def test_a_new_round_months_later_is_a_new_event(session):
    _record(session, _item("https://e/1", "Botsync raises $5 Mn in seed round", 200))
    _record(session, _item("https://e/2", "Botsync raises $11 Mn in Series B", 0))
    assert len(session.exec(select(FundingEvent)).all()) == 2


def test_run_radar_survives_a_dead_feed_and_finds_boards(session, monkeypatch):
    feed = b"""<rss><channel><item><title>Voice AI startup Bolna raises $6 Mn led by GC</title>
      <link>https://e/bolna</link><pubDate>Wed, 18 Nov 2026 10:00:00 +0000</pubDate></item>
      </channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "entrackr.com":
            return httpx.Response(200, content=feed)
        if host == "api.ashbyhq.com" and request.url.path.endswith("/bolna"):
            return httpx.Response(200, json={"jobs": [
                {"id": "1", "title": "AI Engineer Intern", "location": "Bengaluru", "jobUrl": "u"}
            ]})  # fmt: skip
        if host == "jobs.ashbyhq.com":
            return httpx.Response(200, text="<title>Bolna Jobs</title>")
        return httpx.Response(404)

    monkeypatch.setattr(
        "internradar.funding.radar.utcnow", lambda: datetime(2026, 11, 18, tzinfo=UTC)
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    summary = run_radar(session, Watchlist(), client)

    assert summary.feeds["entrackr"] == 1
    assert isinstance(summary.feeds["inc42"], str)  # 404 reported, not raised
    assert [e.company for e in summary.boards_found] == ["Bolna"]
    [company] = funded_companies(session, Watchlist())
    assert (company.ats, company.slug, company.tags) == ("ashby", "bolna", ["funding-radar"])


def test_funded_companies_skip_boards_already_on_the_watchlist(session):
    session.add(FundingEvent(company="Bolna", company_norm="bolna", headline="h", url="u",
                             board_ats="ashby", board_slug="bolna"))  # fmt: skip
    session.flush()
    watchlist = Watchlist(companies=[WatchedCompany(name="Bolna", ats="ashby", slug="bolna")])
    assert funded_companies(session, watchlist) == []
