"""The contract every source implements.

`Source.fetch()` never raises: a failure becomes `SourceResult(ok=False)` so one broken board
cannot stop the others, and the pipeline knows not to expire that source's postings.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from internradar.config import SearchConfig
from internradar.models import Origin


@dataclass(slots=True)
class Posting:
    """One listing exactly as a source reported it, before de-duplication."""

    source: str
    origin: Origin
    native_id: str
    title: str
    company: str
    location: str | None
    url: str | None
    apply_url: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    is_remote: bool = False
    flagged_internship: bool = False  # the source itself labels it an internship
    stipend_min: float | None = None
    stipend_max: float | None = None
    currency: str | None = None
    pay_period: str | None = None
    eligibility: dict[str, Any] | None = None
    # The user vouches for this source (their placement cell), so a posting that does not
    # state a location is kept rather than dropped by the location filter.
    trusted: bool = False


@dataclass(slots=True)
class SourceResult:
    source: str
    ok: bool
    exhaustive: bool
    postings: list[Posting] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class Source(ABC):
    name: str
    origin: Origin
    # Exhaustive sources return their full current listing, so a posting missing from a
    # successful fetch really has been taken down. Search-based sources only return a window.
    exhaustive: bool

    def fetch(self, search: SearchConfig) -> SourceResult:
        start = time.monotonic()
        try:
            postings = self._fetch(search)
            error = None
        except Exception as exc:
            postings, error = [], f"{type(exc).__name__}: {exc}"
        return SourceResult(
            source=self.name,
            ok=error is None,
            exhaustive=self.exhaustive,
            postings=postings,
            error=error,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    @abstractmethod
    def _fetch(self, search: SearchConfig) -> list[Posting]: ...
