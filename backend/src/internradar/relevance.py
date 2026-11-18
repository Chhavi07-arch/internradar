"""Decides which fetched postings are worth storing: tech internships in the user's locations.

Boards return plenty of noise (Naukri's "Relationship Executive", Greenhouse's
"Internal Auditor"), so every posting passes through these checks before it is stored.
"""

import re

from internradar.config import SearchConfig
from internradar.normalize import normalize_city, normalize_company
from internradar.sources.base import Posting

# Whole words only: "intern" must not match "internal" or "international".
_INTERN = re.compile(r"\b(intern|interns|internship|internships|trainee|apprentice)\b", re.I)

_TECH = re.compile(
    r"\b("
    r"engineer\w*|developer|software|sde|swe|programmer|"
    r"backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|web|mobile|android|ios|"
    r"data|ml|machine learning|ai|artificial intelligence|gen ?ai|llm|nlp|"
    r"computer vision|deep learning|research|scientist|"
    r"devops|sre|cloud|platform|infrastructure|security|python|java|golang"
    r")\b",
    re.I,
)

# Roles that mention AI or data but are not engineering work: "AI Growth Hacker Intern".
_NON_TECH = re.compile(
    r"\b(marketing|growth|sales|business|graphics?|motion|video|content|social media|"
    r"product management|product manager|hr|human resources|recruit\w*|operations|finance)\b",
    re.I,
)

# A remote posting is only useful if it is open to India.
_OPEN_REGIONS = re.compile(r"\b(india|apac|asia|global|worldwide|anywhere)\b", re.I)
_REMOTE_WORDS = re.compile(r"\b(remote|wfh|work from home|anywhere)\b", re.I)


def is_internship(title: str, flagged_by_source: bool = False) -> bool:
    return flagged_by_source or bool(_INTERN.search(title))


def is_tech(title: str) -> bool:
    return bool(_TECH.search(title)) and not _NON_TECH.search(title)


def location_matches(location: str | None, is_remote: bool, search: SearchConfig) -> bool:
    if not location:
        return False
    wanted = {normalize_city(c) for c in search.locations.cities}
    parts = re.split(r"[,;/|]| or | and ", location)
    if any(normalize_city(p) in wanted for p in parts):
        return True
    if re.fullmatch(r"\W*india\W*", location, re.I):  # country only: usually "any office"
        return True
    if not search.locations.remote or not (is_remote or _REMOTE_WORDS.search(location)):
        return False
    # "Remote" alone is fine; "Remote - India" is fine; "Remote in New York" is not.
    leftover = _REMOTE_WORDS.sub(" ", location)
    return not re.sub(r"[\W_]+", "", leftover) or bool(_OPEN_REGIONS.search(leftover))


def is_relevant(posting: Posting, search: SearchConfig) -> bool:
    excluded = {normalize_company(c) for c in search.exclude_companies}
    return (
        normalize_company(posting.company) not in excluded
        and is_internship(posting.title, posting.flagged_internship)
        and is_tech(posting.title)
        and (
            (posting.trusted and not posting.location)
            or location_matches(posting.location, posting.is_remote, search)
        )
    )
