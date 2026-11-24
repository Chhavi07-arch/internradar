"""Normalisation used for de-duplication.

The same internship shows up as "Sarvam AI Pvt. Ltd. / ML Intern (Summer 2026) / Bangalore"
on one site and "Sarvam AI / Machine Learning Internship / Bengaluru, Karnataka" on another.
Everything here maps such variants onto one canonical form, so the fingerprint matches.
"""

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_COMPANY_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "llc", "llp", "corp",
    "corporation", "co", "company", "technologies", "technology", "solutions", "labs",
    "software", "services", "systems", "group", "gmbh", "plc", "india",
}  # fmt: skip

_TITLE_ABBREVIATIONS = {
    "sde": "software development engineer",
    "swe": "software engineer",
    "ml": "machine learning",
    "dl": "deep learning",
    "ds": "data science",
    "fullstack": "full stack",
    "internship": "intern",
    "sr": "senior",
    "jr": "junior",
}

_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru urban": "bengaluru",
    "blr": "bengaluru",
    "gurgaon": "gurugram",
    "bombay": "mumbai",
    "new delhi": "delhi",
    "madras": "chennai",
    "calcutta": "kolkata",
}

# Canonical names of cities likely to appear in Indian listings, found anywhere in the text,
# so "Onsite in Bangalore" and "Bengaluru (Hybrid)" both resolve to "bengaluru".
_KNOWN_CITIES = set(_CITY_ALIASES.values()) | {
    "hyderabad", "noida", "pune", "ahmedabad", "jaipur", "kochi", "chandigarh",
    "indore", "coimbatore", "thiruvananthapuram", "bhubaneswar", "mysuru",
}  # fmt: skip

_REMOTE_WORDS = {"remote", "wfh", "work from home", "anywhere", "worldwide"}

# Query params that only track where a click came from; they never identify the job.
_TRACKING_PARAMS = {
    "trk", "trackingid", "refid", "ref", "src", "lipi", "ebp", "fbclid", "gclid",
    "mc_cid", "mc_eid", "originalsubdomain", "lever-source", "gh_src", "source",
}  # fmt: skip

_LINKEDIN_JOB = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)")


def _fold(text: str) -> str:
    """Lowercase, strip accents, turn every non-alphanumeric run into one space."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_company(name: str) -> str:
    words = _fold(name).split()
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def normalize_title(title: str) -> str:
    title = re.sub(r"[(\[].*?[)\]]", " ", title)  # "(Summer 2026)", "[Remote]"
    words = [_TITLE_ABBREVIATIONS.get(w, w) for w in _fold(title).split()]
    words = [w for w in " ".join(words).split() if not re.fullmatch(r"20\d\d", w)]
    return " ".join(words)


def normalize_city(location: str | None) -> str | None:
    """Return a canonical city, "remote", or None when the location is unknown."""
    if not location:
        return None
    padded = f" {_fold(location)} "
    if any(f" {word} " in padded for word in _REMOTE_WORDS):
        return "remote"
    # Earliest known city wins: "Hyderabad; Bengaluru" -> "hyderabad".
    found = [
        (padded.find(f" {name} "), _CITY_ALIASES.get(name, name))
        for name in (*_KNOWN_CITIES, *_CITY_ALIASES)
        if f" {name} " in padded
    ]
    if found:
        return min(found)[1]
    first = _fold(re.split(r"[,;/|(]", location)[0])
    return _CITY_ALIASES.get(first, first) or None


def canonical_url(url: str | None) -> str | None:
    """Strip tracking params and fragments so two links to one posting compare equal."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
        return None
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")

    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and (
        m := _LINKEDIN_JOB.search(path)
    ):
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"

    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    )
    return urlunsplit(("https", host, path, urlencode(query), ""))


def fingerprint(company: str, title: str, location: str | None) -> str:
    """Stable id for "this role at this company in this city", independent of the source."""
    key = "|".join(
        [normalize_company(company), normalize_title(title), normalize_city(location) or ""]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:32]
