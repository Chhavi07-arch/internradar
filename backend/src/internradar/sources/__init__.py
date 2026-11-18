from internradar.config import Watchlist
from internradar.sources.ats import ATSSource
from internradar.sources.base import Posting, Source, SourceResult
from internradar.sources.jobspy_source import JobSpySource

BOARDS = ("linkedin", "indeed", "naukri")

__all__ = ["BOARDS", "Posting", "Source", "SourceResult", "build_sources"]


def build_sources(watchlist: Watchlist, only: set[str] | None = None) -> list[Source]:
    """All configured sources; `only` filters by name ("linkedin") or kind ("ats", "boards")."""
    sources: list[Source] = [JobSpySource(site) for site in BOARDS]
    sources += [ATSSource(company) for company in watchlist.companies]
    if not only:
        return sources
    return [
        s
        for s in sources
        if s.name in only
        or ("boards" in only and isinstance(s, JobSpySource))
        or ("ats" in only and isinstance(s, ATSSource))
    ]
