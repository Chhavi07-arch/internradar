"""Find a funded company's public careers board on Greenhouse, Lever or Ashby.

Guessing slugs is easy; guessing the *right company* is not: jobs.ashbyhq.com/navi belongs to
a San Francisco hardware startup, not to Navi the Indian fintech. A board is only accepted
when its page title names the company and it lists at least one role in India.
"""

import re
from dataclasses import dataclass

import httpx

from internradar.config import WatchedCompany
from internradar.normalize import normalize_city, normalize_company
from internradar.sources.ats import parse_ashby, parse_greenhouse, parse_lever

_API = {
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", parse_ashby),
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", parse_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
}
_PAGE = {
    "ashby": "https://jobs.ashbyhq.com/{slug}",  # title: "Sarvam Jobs"
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",  # title: "Jobs at CloudSEK"
    "lever": "https://jobs.lever.co/{slug}",  # title: "Meesho"
}

# Suffixes that differ between how the press and the careers page name a company.
_NAME_NOISE = {"ai", "jobs", "careers", "hq", "labs", "technologies", "tech", "io"}
_INDIAN_CITIES = {
    "bengaluru", "mumbai", "delhi", "gurugram", "noida", "hyderabad", "pune", "chennai",
    "kolkata", "ahmedabad", "jaipur", "kochi", "chandigarh", "indore", "coimbatore",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class BoardMatch:
    ats: str
    slug: str
    jobs: int
    india_jobs: int


def _core_name(name: str) -> str:
    """ "Jobs at Sarvam AI", "Sarvam Jobs" and "SarvamAI" all become "sarvam"."""
    name = re.sub(r"^\s*jobs at\s+", "", name, flags=re.I)
    words = normalize_company(name).split()
    kept = [w for w in words if w not in _NAME_NOISE] or words
    return "".join(kept).removesuffix("ai")


def slug_candidates(company: str) -> list[str]:
    words = normalize_company(company).split()
    if not words:
        return []
    joined = "".join(words)
    base = joined.removesuffix("ai") or joined
    candidates = [joined, "-".join(words), base, f"{base}ai", f"{base}-ai", f"{base}hq"]
    return list(dict.fromkeys(c for c in candidates if len(c) >= 3))  # dedupe, keep order


def _in_india(location: str | None) -> bool:
    if not location:
        return False
    return "india" in location.lower() or normalize_city(location) in (_INDIAN_CITIES | {"remote"})


def _page_title(client: httpx.Client, ats: str, slug: str) -> str | None:
    response = client.get(_PAGE[ats].format(slug=slug))
    if response.status_code != 200:
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.S | re.I)
    return match.group(1).strip() if match else None


def check_board(client: httpx.Client, company: str, ats: str, slug: str) -> BoardMatch | None:
    url, parse = _API[ats]
    response = client.get(url.format(slug=slug))
    if response.status_code != 200:
        return None
    postings = parse(WatchedCompany(name=company, ats=ats, slug=slug), response.json())
    india = sum(_in_india(p.location) or p.is_remote for p in postings)
    if not postings or not india:
        return None
    title = _page_title(client, ats, slug)
    if title is None or _core_name(title) != _core_name(company):
        return None
    return BoardMatch(ats=ats, slug=slug, jobs=len(postings), india_jobs=india)


def find_board(client: httpx.Client, company: str) -> BoardMatch | None:
    for slug in slug_candidates(company):
        for ats in _API:
            try:
                if match := check_board(client, company, ats, slug):
                    return match
            except (httpx.HTTPError, ValueError, KeyError):
                continue
    return None
