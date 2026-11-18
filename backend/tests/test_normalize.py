import pytest

from internradar.normalize import (
    canonical_url,
    fingerprint,
    normalize_city,
    normalize_company,
    normalize_title,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sarvam AI Pvt. Ltd.", "sarvam ai"),
        ("Razorpay Software Private Limited", "razorpay"),
        ("The Boston Consulting Group", "boston consulting"),
        ("Zeta Technologies India", "zeta"),
        ("H&M", "h and m"),
        ("Labs", "labs"),  # never strip a name down to nothing
    ],
)
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ML Intern (Summer 2026)", "machine learning intern"),
        ("Machine Learning Internship", "machine learning intern"),
        ("SDE Intern - 2026", "software development engineer intern"),
        ("Full-Stack Developer Intern [Remote]", "full stack developer intern"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bangalore", "bengaluru"),
        ("Bengaluru, Karnataka, India", "bengaluru"),
        ("Work From Home", "remote"),
        ("Remote - India", "remote"),
        ("Gurgaon, Haryana", "gurugram"),
        ("Pune", "pune"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_city(raw, expected):
    assert normalize_city(raw) == expected


def test_canonical_url_strips_tracking_and_keeps_job_id():
    url = "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&utm_source=x&gh_src=li#apply"
    assert canonical_url(url) == "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123"


def test_canonical_url_collapses_linkedin_variants():
    a = "https://in.linkedin.com/jobs/view/ml-intern-at-sarvam-4012345678?trk=public_jobs"
    b = "https://www.linkedin.com/jobs/view/4012345678/"
    assert canonical_url(a) == canonical_url(b) == "https://www.linkedin.com/jobs/view/4012345678"


@pytest.mark.parametrize("bad", [None, "", "mailto:hr@acme.com", "javascript:void(0)"])
def test_canonical_url_rejects_non_http(bad):
    assert canonical_url(bad) is None


def test_fingerprint_matches_same_role_across_sources():
    linkedin = fingerprint("Sarvam AI Pvt. Ltd.", "ML Intern (Summer 2026)", "Bangalore")
    naukri = fingerprint("Sarvam AI", "Machine Learning Internship", "Bengaluru, Karnataka")
    assert linkedin == naukri


def test_fingerprint_separates_different_cities():
    assert fingerprint("Acme", "Backend Intern", "Bengaluru") != fingerprint(
        "Acme", "Backend Intern", "Pune"
    )
