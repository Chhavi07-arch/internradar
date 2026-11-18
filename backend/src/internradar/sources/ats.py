"""Company career boards hosted on Greenhouse, Lever and Ashby.

All three expose a public, unauthenticated JSON endpoint per company that returns every open
role, so these sources are exhaustive. Parsing is split into pure functions for testing.
"""

import html
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from internradar.config import SearchConfig, WatchedCompany
from internradar.models import Origin
from internradar.sources.base import Posting, Source

_TIMEOUT = httpx.Timeout(20.0)
_HEADERS = {"User-Agent": "InternRadar/0.1 (personal job tracker)"}


def html_to_text(raw: str | None) -> str | None:
    if not raw:
        return None
    text = html.unescape(html.unescape(raw))  # Greenhouse double-escapes its content
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip() or None


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def parse_greenhouse(company: WatchedCompany, data: dict[str, Any]) -> list[Posting]:
    return [
        Posting(
            source=f"greenhouse:{company.slug}",
            origin=Origin.ATS,
            native_id=str(job["id"]),
            title=job["title"],
            company=company.name,
            location=(job.get("location") or {}).get("name"),
            url=job["absolute_url"],
            apply_url=job["absolute_url"],
            description=html_to_text(job.get("content")),
            posted_at=_iso(job.get("first_published") or job.get("updated_at")),
            deadline_at=_iso(job.get("application_deadline")),
        )
        for job in data.get("jobs", [])
    ]


def parse_lever(company: WatchedCompany, data: list[dict[str, Any]]) -> list[Posting]:
    postings = []
    for job in data:
        categories = job.get("categories") or {}
        created = job.get("createdAt")
        postings.append(
            Posting(
                source=f"lever:{company.slug}",
                origin=Origin.ATS,
                native_id=job["id"],
                title=job["text"],
                company=company.name,
                location=categories.get("location"),
                url=job["hostedUrl"],
                apply_url=job.get("applyUrl"),
                description=job.get("descriptionPlain"),
                posted_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
                is_remote=job.get("workplaceType") == "remote",
                flagged_internship="intern" in (categories.get("commitment") or "").lower(),
            )
        )
    return postings


def parse_ashby(company: WatchedCompany, data: dict[str, Any]) -> list[Posting]:
    return [
        Posting(
            source=f"ashby:{company.slug}",
            origin=Origin.ATS,
            native_id=job["id"],
            title=job["title"],
            company=company.name,
            location=job.get("location"),
            url=job["jobUrl"],
            apply_url=job.get("applyUrl"),
            description=job.get("descriptionPlain"),
            posted_at=_iso(job.get("publishedAt")),
            is_remote=bool(job.get("isRemote")),
            flagged_internship=job.get("employmentType") == "Intern",
        )
        for job in data.get("jobs", [])
        if job.get("isListed", True)
    ]


_ENDPOINTS = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        parse_greenhouse,
    ),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", parse_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}", parse_ashby),
}


class ATSSource(Source):
    origin = Origin.ATS
    exhaustive = True

    def __init__(self, company: WatchedCompany):
        self.company = company
        self.name = f"{company.ats}:{company.slug}"

    def _fetch(self, search: SearchConfig) -> list[Posting]:
        url, parse = _ENDPOINTS[self.company.ats]
        response = httpx.get(url.format(slug=self.company.slug), timeout=_TIMEOUT, headers=_HEADERS)
        response.raise_for_status()
        return parse(self.company, response.json())
