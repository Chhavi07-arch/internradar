import pytest

from internradar.models import Origin
from internradar.relevance import is_internship, is_relevant, is_tech, location_matches
from internradar.sources.base import Posting


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Machine Learning Intern", True),
        ("Backend Internship - Summer 2027", True),
        ("Graduate Trainee - Software", True),
        ("Senior Internal Auditor", False),
        ("International Tax Manager", False),
        ("Software Engineer", False),
    ],
)
def test_is_internship(title, expected):
    assert is_internship(title) is expected


def test_source_flag_counts_as_internship():
    assert is_internship("Software Engineering (6 months)", flagged_by_source=True)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI/ML Research Intern", True),
        ("Full-Stack Developer Intern", True),
        ("GenAI Intern", True),
        ("SDE Intern", True),
        ("Intern Communications", False),
        ("Marketing internship (brand & communication)", False),
        ("Relationship and Business Development Executive", False),
        ("AI Growth Hacker Intern", False),
        ("AI Motion Graphics Intern (Remote)", False),
        ("AI Product Management Internship", False),
        ("Data Analyst Intern", True),
        ("QA Engineer Intern", True),
        ("Technology Program Intern", True),
    ],
)
def test_is_tech(title, expected):
    assert is_tech(title) is expected


@pytest.mark.parametrize(
    ("location", "is_remote", "expected"),
    [
        ("Bengaluru, Karnataka, India", False, True),
        ("Bangalore", False, True),
        ("Hyderabad; Bengaluru", False, True),
        ("Hyderabad, India", False, False),
        ("Onsite in Bangalore", False, True),
        ("India", False, True),
        ("Remote", False, True),
        ("Remote - India", False, True),
        ("India", True, True),
        ("Remote in New York, New Jersey", False, False),
        ("San Francisco, CA", True, False),
        (None, True, False),
    ],
)
def test_location_matches(search, location, is_remote, expected):
    assert location_matches(location, is_remote, search) is expected


def test_remote_ignored_when_user_disables_it(search):
    search.locations.remote = False
    assert not location_matches("Remote - India", True, search)


def test_excluded_companies_match_after_normalisation(search):
    search.exclude_companies = ["ThinkNovaa"]
    posting = Posting(
        source="linkedin", origin=Origin.BOARD, native_id="1", title="ML Intern",
        company="ThinkNovaa Pvt. Ltd.", location="Bengaluru", url=None,
    )  # fmt: skip
    assert not is_relevant(posting, search)
