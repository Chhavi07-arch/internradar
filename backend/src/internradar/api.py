"""HTTP API for the dashboard. Read-mostly; the only writes are the user's own state
(seen, saved, hidden, notes) and starting a fetch.
"""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Engine, func, or_
from sqlmodel import Session, col, select

from internradar.config import REPO_ROOT, get_settings, load_gmail_config, load_watchlist
from internradar.db import get_engine, init_db
from internradar.gmail import auth
from internradar.gmail.extract import build_extractor
from internradar.models import (
    FundingEvent,
    FundingMention,
    Kind,
    Lifecycle,
    Opportunity,
    Origin,
    Run,
    Sighting,
    SourceRun,
    utcnow,
)
from internradar.normalize import normalize_company
from internradar.service import FETCH_LOCK, fetch_everything

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
FUNDING_SIGNAL_DAYS = 180
IST = timezone(timedelta(hours=5, minutes=30))  # days on the chart are the user's days

View = Literal["inbox", "all", "saved", "deadlines", "hidden"]


# --- response shapes ------------------------------------------------------------------------


class SourceHealth(BaseModel):
    source: str
    ok: bool
    fetched: int
    kept: int
    new: int
    duration_ms: int
    error: str | None


class DayCount(BaseModel):
    day: date
    ats: int = 0
    list: int = 0
    board: int = 0
    email: int = 0


class Overview(BaseModel):
    open_internships: int
    unseen: int
    saved: int
    due_this_week: int
    funded_rounds_30d: int
    last_run_at: datetime | None
    sources_ok: int
    sources_failed: int
    fetch_running: bool
    llm_configured: bool
    inboxes: dict[str, bool]
    per_day: list[DayCount]


class OpportunityOut(BaseModel):
    id: int
    title: str
    company: str
    city: str | None
    is_remote: bool
    url: str | None
    apply_url: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    deadline_at: datetime | None
    primary_origin: Origin
    status: Lifecycle
    sources: list[str]
    stipend: str | None
    eligibility: dict[str, Any] | None
    funding: str | None
    seen: bool
    bookmarked: bool
    hidden: bool
    notes: str | None


class SightingOut(BaseModel):
    source: str
    origin: Origin
    url: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    closed: bool


class OpportunityDetail(OpportunityOut):
    location: str | None
    description: str | None
    sightings: list[SightingOut]


class OpportunityPatch(BaseModel):
    seen: bool | None = None
    bookmarked: bool | None = None
    hidden: bool | None = None
    notes: str | None = None


class FundingOut(BaseModel):
    id: int
    company: str
    round: str | None
    amount_text: str | None
    amount_usd: float | None
    lead_investors: str | None
    headline: str
    url: str
    published_at: datetime
    mentions: int
    board: str | None
    internships: int


class FetchRequest(BaseModel):
    only: list[str] | None = None
    gmail: bool = True


class FetchState(BaseModel):
    running: bool
    last_finished_at: datetime | None = None
    last_new: int | None = None
    last_error: str | None = None


# --- app ------------------------------------------------------------------------------------


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="InternRadar", version="0.1.0")
    app.state.engine = engine or get_engine()
    app.state.fetch = FetchState(running=False)
    init_db(app.state.engine)

    def session() -> Iterator[Session]:
        with Session(app.state.engine) as s:
            yield s

    @app.get("/api/overview", response_model=Overview)
    def overview(s: Session = Depends(session)) -> Overview:  # noqa: B008
        now = utcnow()
        open_q = select(func.count()).select_from(Opportunity).where(*_visible())
        runs = _latest_per_source(s)
        last_run_at = s.exec(select(func.max(Run.finished_at))).one()
        return Overview(
            open_internships=s.exec(open_q).one(),
            unseen=s.exec(open_q.where(col(Opportunity.seen_at).is_(None))).one(),
            saved=s.exec(open_q.where(col(Opportunity.bookmarked).is_(True))).one(),
            due_this_week=s.exec(
                open_q.where(
                    col(Opportunity.deadline_at) >= now,
                    col(Opportunity.deadline_at) <= now + timedelta(days=7),
                )
            ).one(),
            funded_rounds_30d=s.exec(
                select(func.count())
                .select_from(FundingEvent)
                .where(
                    col(FundingEvent.is_tech).is_(True),
                    FundingEvent.published_at >= now - timedelta(days=30),
                )
            ).one(),
            last_run_at=last_run_at,
            sources_ok=sum(r.ok for r in runs),
            sources_failed=sum(not r.ok for r in runs),
            fetch_running=FETCH_LOCK.locked(),
            llm_configured=build_extractor(get_settings()) is not None,
            inboxes={a.label: auth.is_connected(a.label) for a in load_gmail_config().accounts},
            per_day=_per_day(s, days=14),
        )

    @app.get("/api/opportunities", response_model=list[OpportunityOut])
    def list_opportunities(
        view: View = "inbox",
        q: str | None = None,
        origin: Origin | None = None,
        limit: int = Query(200, le=1000),
        s: Session = Depends(session),  # noqa: B008
    ) -> list[OpportunityOut]:
        query = select(Opportunity).where(Opportunity.kind == Kind.INTERNSHIP)
        if view == "hidden":
            query = query.where(col(Opportunity.hidden).is_(True))
        else:
            query = query.where(*_visible())
        if view == "inbox":
            query = query.order_by(
                col(Opportunity.seen_at).is_not(None), col(Opportunity.first_seen_at).desc()
            )
        elif view == "saved":
            query = query.where(col(Opportunity.bookmarked).is_(True))
        elif view == "deadlines":
            query = query.where(col(Opportunity.deadline_at).is_not(None)).order_by(
                col(Opportunity.deadline_at)
            )
        query = query.order_by(col(Opportunity.first_seen_at).desc())
        if origin:
            query = query.where(Opportunity.primary_origin == origin)
        if q:
            like = f"%{q.strip()}%"
            query = query.where(
                or_(col(Opportunity.title).ilike(like), col(Opportunity.company).ilike(like))
            )
        rows = s.exec(query.limit(limit)).all()
        return _serialize(s, rows)

    @app.get("/api/opportunities/{opportunity_id}", response_model=OpportunityDetail)
    def get_opportunity(opportunity_id: int, s: Session = Depends(session)) -> OpportunityDetail:  # noqa: B008
        opportunity = _get_or_404(s, opportunity_id)
        [base] = _serialize(s, [opportunity])
        sightings = s.exec(select(Sighting).where(Sighting.opportunity_id == opportunity_id)).all()
        return OpportunityDetail(
            **base.model_dump(),
            location=opportunity.location,
            description=opportunity.description,
            sightings=[
                SightingOut(
                    source=x.source,
                    origin=x.origin,
                    url=x.url,
                    first_seen_at=x.first_seen_at,
                    last_seen_at=x.last_seen_at,
                    closed=x.closed_at is not None,
                )
                for x in sightings
            ],
        )

    @app.patch("/api/opportunities/{opportunity_id}", response_model=OpportunityOut)
    def patch_opportunity(
        opportunity_id: int,
        patch: OpportunityPatch,
        s: Session = Depends(session),  # noqa: B008
    ) -> OpportunityOut:
        opportunity = _get_or_404(s, opportunity_id)
        _apply(opportunity, patch)
        s.commit()
        s.refresh(opportunity)
        return _serialize(s, [opportunity])[0]

    @app.post("/api/opportunities/mark-seen")
    def mark_all_seen(s: Session = Depends(session)) -> dict[str, int]:  # noqa: B008
        unseen = s.exec(
            select(Opportunity).where(*_visible(), col(Opportunity.seen_at).is_(None))
        ).all()
        now = utcnow()
        for opportunity in unseen:
            opportunity.seen_at = now
        s.commit()
        return {"marked": len(unseen)}

    @app.get("/api/funding", response_model=list[FundingOut])
    def funding(days: int = Query(60, le=365), s: Session = Depends(session)) -> list[FundingOut]:  # noqa: B008
        mentions = (
            select(FundingMention.event_id, func.count().label("n"))
            .group_by(FundingMention.event_id)
            .subquery()
        )
        events = s.exec(
            select(FundingEvent, mentions.c.n)
            .join(mentions, mentions.c.event_id == FundingEvent.id)
            .where(
                col(FundingEvent.is_tech).is_(True),
                FundingEvent.published_at >= utcnow() - timedelta(days=days),
            )
            .order_by(col(FundingEvent.published_at).desc())
        ).all()
        interns = _open_internships_by_company(s)
        watched = {
            normalize_company(c.name): f"{c.ats}:{c.slug}" for c in load_watchlist().companies
        }
        return [
            FundingOut(
                id=e.id,
                company=e.company,
                round=e.round,
                amount_text=e.amount_text,
                amount_usd=e.amount_usd,
                lead_investors=e.lead_investors,
                headline=e.headline,
                url=e.url,
                published_at=e.published_at,
                mentions=n,
                board=f"{e.board_ats}:{e.board_slug}"
                if e.board_slug
                else watched.get(e.company_norm),
                internships=interns.get(e.company_norm, 0),
            )
            for e, n in events
        ]

    @app.get("/api/sources", response_model=list[SourceHealth])
    def sources(s: Session = Depends(session)) -> list[SourceHealth]:  # noqa: B008
        return sorted(
            (SourceHealth.model_validate(r, from_attributes=True) for r in _latest_per_source(s)),
            key=lambda r: (r.ok, -r.new, r.source),
        )

    @app.get("/api/fetch", response_model=FetchState)
    def fetch_state() -> FetchState:
        return app.state.fetch.model_copy(update={"running": FETCH_LOCK.locked()})

    @app.post("/api/fetch", response_model=FetchState, status_code=202)
    def start_fetch(request: FetchRequest) -> FetchState:
        if FETCH_LOCK.locked():
            raise HTTPException(409, "a fetch is already running")

        def run() -> None:
            try:
                report = fetch_everything(
                    set(request.only) if request.only else None, request.gmail, trigger="dashboard"
                )
                app.state.fetch = FetchState(
                    running=False, last_finished_at=utcnow(), last_new=report.new
                )
            except Exception as exc:  # surfaced to the dashboard, not swallowed
                app.state.fetch = FetchState(
                    running=False, last_finished_at=utcnow(), last_error=str(exc)[:300]
                )

        threading.Thread(target=run, daemon=True, name="fetch").start()
        return FetchState(running=True)

    _mount_frontend(app)
    return app


# --- helpers --------------------------------------------------------------------------------


def _visible() -> tuple[Any, ...]:
    return (
        Opportunity.kind == Kind.INTERNSHIP,
        Opportunity.status != Lifecycle.CLOSED,
        col(Opportunity.hidden).is_(False),
    )


def _get_or_404(s: Session, opportunity_id: int) -> Opportunity:
    opportunity = s.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(404, "opportunity not found")
    return opportunity


def _apply(opportunity: Opportunity, patch: OpportunityPatch) -> None:
    if patch.seen is not None:
        opportunity.seen_at = utcnow() if patch.seen else None
    if patch.bookmarked is not None:
        opportunity.bookmarked = patch.bookmarked
    if patch.hidden is not None:
        opportunity.hidden = patch.hidden
    if "notes" in patch.model_fields_set:
        opportunity.notes = (patch.notes or "").strip() or None


def _serialize(s: Session, rows: list[Opportunity]) -> list[OpportunityOut]:
    ids = [o.id for o in rows]
    sources: dict[int, list[str]] = {}
    for opportunity_id, source in s.exec(
        select(Sighting.opportunity_id, Sighting.source)
        .where(col(Sighting.opportunity_id).in_(ids), col(Sighting.closed_at).is_(None))
        .distinct()
    ):
        sources.setdefault(opportunity_id, []).append(source)
    funding = _funding_by_company(s)
    return [
        OpportunityOut(
            id=o.id,
            title=o.title,
            company=o.company,
            city=o.city,
            is_remote=o.is_remote,
            url=o.url,
            apply_url=o.apply_url,
            posted_at=o.posted_at,
            first_seen_at=o.first_seen_at,
            deadline_at=o.deadline_at,
            primary_origin=o.primary_origin,
            status=o.status,
            sources=sorted(sources.get(o.id, [])),
            stipend=_stipend(o),
            eligibility=o.eligibility,
            funding=funding.get(o.company_norm),
            seen=o.seen_at is not None,
            bookmarked=o.bookmarked,
            hidden=o.hidden,
            notes=o.notes,
        )
        for o in rows
    ]


def _stipend(o: Opportunity) -> str | None:
    if o.stipend_min is None and o.stipend_max is None:
        return None
    low, high = o.stipend_min, o.stipend_max
    is_range = low and high and low != high
    amount = f"{low:,.0f} - {high:,.0f}" if is_range else f"{low or high:,.0f}"
    return " ".join(p for p in (o.currency, amount, o.pay_period and f"/ {o.pay_period}") if p)


def _funding_by_company(s: Session) -> dict[str, str]:
    """Latest known round per company: the watchlist note, overridden by fresher news."""
    funding = {
        normalize_company(c.name): c.funding for c in load_watchlist().companies if c.funding
    }
    events = s.exec(
        select(FundingEvent)
        .where(FundingEvent.published_at >= utcnow() - timedelta(days=FUNDING_SIGNAL_DAYS))
        .order_by(col(FundingEvent.published_at))
    ).all()
    for e in events:
        parts = [e.round, e.amount_text, f"({e.published_at:%b %Y})"]
        funding[e.company_norm] = " ".join(p for p in parts if p)
    return funding


def _open_internships_by_company(s: Session) -> dict[str, int]:
    rows = s.exec(
        select(Opportunity.company_norm, func.count())
        .where(*_visible())
        .group_by(Opportunity.company_norm)
    ).all()
    return dict(rows)


def _latest_per_source(s: Session) -> list[SourceRun]:
    """Each source's most recent result, so a partial fetch does not hide the others."""
    latest_ids = select(func.max(SourceRun.id)).group_by(SourceRun.source)
    return list(s.exec(select(SourceRun).where(col(SourceRun.id).in_(latest_ids))))


def _per_day(s: Session, days: int) -> list[DayCount]:
    start = utcnow().astimezone(IST).date() - timedelta(days=days - 1)
    counts = {
        start + timedelta(days=i): DayCount(day=start + timedelta(days=i)) for i in range(days)
    }
    rows = s.exec(
        select(Opportunity.first_seen_at, Opportunity.primary_origin).where(
            Opportunity.kind == Kind.INTERNSHIP,
            Opportunity.first_seen_at >= datetime.combine(start, time.min, IST).astimezone(UTC),
        )
    ).all()
    for seen, origin in rows:
        bucket = counts.get(seen.astimezone(IST).date())
        if bucket is not None:
            setattr(bucket, Origin(origin).value, getattr(bucket, Origin(origin).value) + 1)
    return list(counts.values())


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built dashboard (frontend/dist) from the same port, with SPA fallback."""
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and Path(FRONTEND_DIST.resolve()) in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
