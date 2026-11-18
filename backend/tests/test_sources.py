"""Parsers are tested against trimmed copies of real API responses (Nov 2026)."""

from datetime import UTC, date, datetime

from internradar.config import WatchedCompany
from internradar.models import Origin
from internradar.sources.ats import html_to_text, parse_ashby, parse_greenhouse, parse_lever
from internradar.sources.base import Source
from internradar.sources.jobspy_source import JobSpySource, row_to_posting


def test_parse_greenhouse():
    data = {
        "jobs": [
            {
                "id": 8394096,
                "title": "AI Researcher Intern",
                "absolute_url": "https://job-boards.greenhouse.io/hackerrank/jobs/8394096",
                "location": {"name": "Bangalore, India"},
                "first_published": "2026-11-16T16:08:54-05:00",
                "application_deadline": "2026-12-01T00:00:00-05:00",
                "content": "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;",
            }
        ]
    }
    [p] = parse_greenhouse(
        WatchedCompany(name="HackerRank", ats="greenhouse", slug="hackerrank"), data
    )
    assert p.source == "greenhouse:hackerrank"
    assert p.origin is Origin.ATS
    assert p.native_id == "8394096"
    assert p.location == "Bangalore, India"
    assert p.posted_at == datetime(2026, 11, 16, 21, 8, 54, tzinfo=UTC)
    assert p.deadline_at == datetime(2026, 12, 1, 5, 0, tzinfo=UTC)
    assert p.description == "Build & ship"


def test_parse_lever():
    data = [
        {
            "id": "09c9d0cd",
            "text": "Security Engineer Intern",
            "hostedUrl": "https://jobs.lever.co/fampay/09c9d0cd",
            "applyUrl": "https://jobs.lever.co/fampay/09c9d0cd/apply",
            "categories": {"commitment": "Internship", "location": "Bengaluru"},
            "createdAt": 1785842845029,
            "workplaceType": "onsite",
            "descriptionPlain": "About the role",
        }
    ]
    [p] = parse_lever(WatchedCompany(name="FamPay", ats="lever", slug="fampay"), data)
    assert p.flagged_internship
    assert not p.is_remote
    assert p.posted_at is not None and p.posted_at.tzinfo is UTC


def test_parse_ashby_skips_unlisted():
    data = {
        "jobs": [
            {
                "id": "a1",
                "title": "ML Intern",
                "location": "Bengaluru",
                "isRemote": False,
                "employmentType": "Intern",
                "jobUrl": "https://jobs.ashbyhq.com/sarvam/a1",
                "applyUrl": "https://jobs.ashbyhq.com/sarvam/a1/application",
                "publishedAt": "2026-04-17T11:46:39.214+00:00",
                "isListed": True,
            },
            {"id": "a2", "title": "Hidden", "jobUrl": "x", "isListed": False},
        ]
    }
    postings = parse_ashby(WatchedCompany(name="Sarvam AI", ats="ashby", slug="sarvam"), data)
    assert [p.native_id for p in postings] == ["a1"]
    assert postings[0].flagged_internship


def test_html_to_text_handles_empty():
    assert html_to_text(None) is None
    assert html_to_text("<p></p>") is None


def test_jobspy_row_with_missing_values():
    row = {
        "id": "li-4491179180",
        "title": "AI/ML Research Intern",
        "company": "The Wedding Company",
        "location": "Bengaluru, Karnataka, India",
        "date_posted": date(2026, 11, 17),
        "job_url": "https://www.linkedin.com/jobs/view/4491179180",
        "job_url_direct": float("nan"),
        "job_type": "internship",
        "is_remote": False,
        "min_amount": float("nan"),
    }
    p = row_to_posting("linkedin", row)
    assert p.posted_at == datetime(2026, 11, 17, tzinfo=UTC)
    assert p.apply_url is None
    assert p.stipend_min is None
    assert p.flagged_internship


def test_jobspy_queries_cover_cities_and_remote(search):
    queries = JobSpySource("linkedin")._queries(search)
    assert {q["location"] for q in queries} == {"Bengaluru, India", "India"}
    assert any(q["is_remote"] for q in queries)


def test_fetch_turns_exceptions_into_failed_result(search):
    class Broken(Source):
        name, origin, exhaustive = "broken", Origin.ATS, True

        def _fetch(self, search):
            raise ConnectionError("board down")

    result = Broken().fetch(search)
    assert not result.ok
    assert result.postings == []
    assert "board down" in (result.error or "")
