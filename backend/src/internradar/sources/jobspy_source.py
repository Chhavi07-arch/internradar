"""LinkedIn, Indeed and Naukri via the JobSpy library (MIT).

Boards rate-limit aggressively (LinkedIn blocks after ~10 pages per IP), so each run asks for a
small, recent window per (role, location) and pauses between queries. These sources are not
exhaustive: a posting missing from one window has not necessarily closed.
"""

import math
import time
from datetime import UTC, date, datetime
from typing import Any

from internradar.config import SearchConfig
from internradar.models import Origin
from internradar.sources.base import Posting, Source

RESULTS_PER_QUERY = 15
PAUSE_BETWEEN_QUERIES_S = 2.0


def _clean(value: Any) -> Any:
    """JobSpy returns a pandas DataFrame, where missing values are NaN or None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _to_datetime(value: Any) -> datetime | None:
    value = _clean(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


def row_to_posting(site: str, row: dict[str, Any]) -> Posting:
    job_type = str(_clean(row.get("job_type")) or "")
    return Posting(
        source=site,
        origin=Origin.BOARD,
        native_id=str(row["id"]),
        title=str(row["title"]),
        company=str(_clean(row.get("company")) or "Unknown"),
        location=_clean(row.get("location")),
        url=_clean(row.get("job_url")),
        apply_url=_clean(row.get("job_url_direct")),
        description=_clean(row.get("description")),
        posted_at=_to_datetime(row.get("date_posted")),
        is_remote=bool(_clean(row.get("is_remote"))),
        flagged_internship="internship" in job_type,
        stipend_min=_clean(row.get("min_amount")),
        stipend_max=_clean(row.get("max_amount")),
        currency=_clean(row.get("currency")),
        pay_period=_clean(row.get("interval")),
    )


class JobSpySource(Source):
    origin = Origin.BOARD
    exhaustive = False

    def __init__(self, site: str):
        self.name = site

    def _queries(self, search: SearchConfig) -> list[dict[str, Any]]:
        places: list[dict[str, Any]] = [
            {"location": f"{city}, India", "is_remote": False} for city in search.locations.cities
        ]
        if search.locations.remote:
            places.append({"location": "India", "is_remote": True})
        return [{"search_term": role, **place} for role in search.roles for place in places]

    def _fetch(self, search: SearchConfig) -> list[Posting]:
        from jobspy import scrape_jobs  # heavy import (pandas); only paid when boards run

        postings: list[Posting] = []
        errors: list[str] = []
        for i, query in enumerate(self._queries(search)):
            if i:
                time.sleep(PAUSE_BETWEEN_QUERIES_S)
            try:
                df = scrape_jobs(
                    site_name=[self.name],
                    job_type="internship",
                    results_wanted=RESULTS_PER_QUERY,
                    hours_old=search.max_age_hours,
                    country_indeed="India",
                    verbose=0,
                    **query,
                )
            except Exception as exc:
                errors.append(f"{query['search_term']}: {exc}")
                continue
            errors.extend(
                f"{query['search_term']}: {e}" for e in df.attrs.get("errors", {}).values()
            )
            postings.extend(row_to_posting(self.name, row) for row in df.to_dict("records"))

        if errors and not postings:
            raise RuntimeError("; ".join(errors[:3]))
        return postings
