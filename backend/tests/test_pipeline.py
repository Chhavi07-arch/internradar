from dataclasses import replace

from sqlmodel import select

from internradar.models import Lifecycle, Opportunity, Origin, Sighting
from internradar.pipeline import run_fetch
from internradar.sources.base import Posting, Source


class FakeSource(Source):
    def __init__(self, name, origin, postings, exhaustive=False, fail=False):
        self.name, self.origin, self.exhaustive = name, origin, exhaustive
        self.postings, self.fail = postings, fail

    def _fetch(self, search):
        if self.fail:
            raise TimeoutError("down")
        return self.postings


def posting(source="linkedin", origin=Origin.BOARD, native_id="1", **overrides) -> Posting:
    base = Posting(
        source=source,
        origin=origin,
        native_id=native_id,
        title="ML Intern (Summer 2027)",
        company="Sarvam AI Pvt. Ltd.",
        location="Bangalore",
        url=f"https://example.com/{source}/{native_id}",
    )
    return replace(base, **overrides)


def opportunities(session):
    return session.exec(select(Opportunity)).all()


def test_same_role_from_two_boards_becomes_one_opportunity(session, search):
    linkedin = posting()
    naukri = posting(
        source="naukri",
        native_id="9",
        title="Machine Learning Internship",
        company="Sarvam AI",
        location="Bengaluru, Karnataka",
    )
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [linkedin])], search)
    run_fetch(session, [FakeSource("naukri", Origin.BOARD, [naukri])], search)

    [opp] = opportunities(session)
    assert {s.source for s in session.exec(select(Sighting))} == {"linkedin", "naukri"}
    assert opp.city == "bengaluru"


def test_shared_url_merges_even_when_titles_differ(session, search):
    a = posting(url="https://jobs.ashbyhq.com/sarvam/a1?utm_source=linkedin")
    b = posting(source="naukri", native_id="2", title="Applied AI Intern",
                url="https://jobs.ashbyhq.com/sarvam/a1")  # fmt: skip
    run_fetch(session, [FakeSource("x", Origin.BOARD, [a, b])], search)
    assert len(opportunities(session)) == 1


def test_fuzzy_title_match_within_company_and_city(session, search):
    a = posting(title="Machine Learning Engineer Intern")
    b = posting(source="naukri", native_id="2", title="Machine Learning Engineering Intern")
    run_fetch(session, [FakeSource("x", Origin.BOARD, [a, b])], search)
    assert len(opportunities(session)) == 1


def test_different_roles_stay_separate(session, search):
    a = posting(title="ML Intern")
    b = posting(native_id="2", title="Frontend Intern")
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [a, b])], search)
    assert len(opportunities(session)) == 2


def test_company_board_overrides_job_board_fields(session, search):
    board = posting(title="ML Intern", apply_url=None)
    apply = "https://jobs.ashbyhq.com/sarvam/a1/application"
    ats = posting(
        source="ashby:sarvam",
        origin=Origin.ATS,
        native_id="a1",
        title="Machine Learning Intern",
        apply_url=apply,
    )
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [board])], search)
    run_fetch(session, [FakeSource("ashby:sarvam", Origin.ATS, [ats])], search)
    run_fetch(
        session,
        [FakeSource("linkedin", Origin.BOARD, [replace(board, title="ML Intern!!")])],
        search,
    )

    [opp] = opportunities(session)
    assert opp.primary_origin == Origin.ATS
    assert opp.title == "Machine Learning Intern"  # the later board fetch did not win
    assert opp.apply_url == apply


def test_refetch_never_touches_user_state(session, search):
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [posting()])], search)
    [opp] = opportunities(session)
    opp.bookmarked, opp.notes = True, "ask Riya for referral"
    session.flush()

    run_fetch(
        session, [FakeSource("linkedin", Origin.BOARD, [posting(title="ML Intern v2")])], search
    )
    session.refresh(opp)
    assert opp.bookmarked and opp.notes == "ask Riya for referral"


def test_irrelevant_postings_are_not_stored(session, search):
    noise = [
        posting(native_id="2", title="Senior Internal Auditor"),
        posting(native_id="3", title="Marketing Intern"),
        posting(native_id="4", location="Remote in New York"),
    ]
    summary = run_fetch(session, [FakeSource("linkedin", Origin.BOARD, noise)], search)
    assert opportunities(session) == []
    assert summary.sources[0].fetched == 3 and summary.sources[0].kept == 0


def test_new_status_lasts_one_run(session, search):
    source = FakeSource("linkedin", Origin.BOARD, [posting()])
    run_fetch(session, [source], search)
    assert opportunities(session)[0].status == Lifecycle.NEW
    run_fetch(session, [source], search)
    assert opportunities(session)[0].status == Lifecycle.ACTIVE


def test_exhaustive_source_closes_after_two_missed_runs_and_reopens(session, search):
    ats = posting(source="ashby:sarvam", origin=Origin.ATS)
    present = FakeSource("ashby:sarvam", Origin.ATS, [ats], exhaustive=True)
    gone = FakeSource("ashby:sarvam", Origin.ATS, [], exhaustive=True)

    run_fetch(session, [present], search)
    run_fetch(session, [gone], search)
    assert opportunities(session)[0].status != Lifecycle.CLOSED  # one miss is not enough
    run_fetch(session, [gone], search)
    assert opportunities(session)[0].status == Lifecycle.CLOSED
    run_fetch(session, [present], search)
    assert opportunities(session)[0].status == Lifecycle.ACTIVE


def test_failed_fetch_never_closes_anything(session, search):
    ats = posting(source="ashby:sarvam", origin=Origin.ATS)
    run_fetch(session, [FakeSource("ashby:sarvam", Origin.ATS, [ats], exhaustive=True)], search)
    down = FakeSource("ashby:sarvam", Origin.ATS, [], exhaustive=True, fail=True)
    for _ in range(3):
        summary = run_fetch(session, [down], search)
    assert opportunities(session)[0].status != Lifecycle.CLOSED
    assert not summary.sources[0].ok


def test_windowed_source_does_not_close_missing_postings(session, search):
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [posting()])], search)
    for _ in range(3):
        run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [])], search)
    assert opportunities(session)[0].status != Lifecycle.CLOSED


def test_same_listing_twice_in_one_run_is_one_sighting(session, search):
    run_fetch(session, [FakeSource("linkedin", Origin.BOARD, [posting(), posting()])], search)
    assert len(session.exec(select(Sighting)).all()) == 1
