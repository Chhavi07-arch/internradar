"""Hosted dashboard routes. The legacy SQLite app remains a separate entry point."""

import os
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, create_engine, text
from sqlmodel import select

from internradar import api
from internradar.gmail.extract import deadline_datetime
from internradar.models import FundingEvent, FundingMention, Lifecycle, Origin, utcnow
from internradar.normalize import canonical_url, normalize_city
from internradar.ownership import owner_session
from internradar.user_state import update_state
from internradar.web_auth import COOKIE, WebSettings, install_sign_in, principal_for


class ProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="", max_length=120)
    college: str = Field(default="", max_length=180)
    branch: str = Field(default="", max_length=100)
    batch: int | None = Field(default=None, ge=2000, le=2100)
    roles: list[str] = Field(default_factory=list, max_length=25)


def _cards(session, owner, ident=None):
    public = (
        session.execute(
            text("""
        SELECT o.*, u.seen_at, COALESCE(u.bookmarked, false) AS bookmarked,
               COALESCE(u.hidden, false) AS hidden, u.notes
        FROM opportunity o LEFT JOIN user_opportunity u
        ON u.opportunity_id = o.id AND u.owner_user_id = :owner
        WHERE o.kind = 'INTERNSHIP' AND (CAST(:id AS integer) IS NULL OR o.id = :id)
    """),
            {"owner": owner, "id": ident},
        )
        .mappings()
        .all()
    )
    private = (
        session.execute(
            text("""
        SELECT p.*, d.received_at, c.provider FROM private_opportunity p
        JOIN mail_delivery d USING (owner_user_id, connection_id, delivery_id)
        JOIN mail_connection c ON c.owner_user_id = p.owner_user_id AND c.id = p.connection_id
        WHERE p.owner_user_id = :owner AND (CAST(:id AS integer) IS NULL
              OR p.public_opportunity_id = :id OR (p.public_opportunity_id IS NULL AND -p.id = :id))
        ORDER BY d.received_at DESC, p.id DESC
    """),
            {"owner": owner, "id": ident},
        )
        .mappings()
        .all()
    )
    sightings = {}
    for row in session.execute(
        text("""
        SELECT * FROM sighting WHERE CAST(:id AS integer) IS NULL OR opportunity_id = :id
    """),
        {"id": ident},
    ).mappings():
        sightings.setdefault(row["opportunity_id"], []).append(
            api.SightingOut(
                source=row["source"],
                origin=Origin[row["origin"]],
                url=canonical_url(row["url"]),
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                closed=row["closed_at"] is not None,
            )
        )
    funding = api._funding_by_company(session)
    result = {}
    for row in public:
        item = dict(row)
        item.update(
            primary_origin=Origin[row["primary_origin"]],
            status=Lifecycle[row["status"]],
            seen=row["seen_at"] is not None,
            funding=funding.get(row["company_norm"]),
            sources=sorted({s.source for s in sightings.get(row["id"], []) if not s.closed}),
            sightings=sightings.get(row["id"], []),
            stipend=api._stipend(SimpleNamespace(**item)),
            url=canonical_url(row["url"]),
            apply_url=canonical_url(row["apply_url"]),
        )
        result[row["id"]] = api.OpportunityDetail.model_validate(item)
    for row in private:
        fields = row["fields"]
        if fields.get("is_internship") is False and not fields.get("legacy_archived"):
            continue
        try:
            deadline = deadline_datetime(fields.get("deadline"))
        except (ValueError, TypeError):
            deadline = None
        at = row["received_at"]
        closed = bool(deadline and deadline < utcnow())
        source = f"email:{row['provider']}"
        url = canonical_url(fields.get("apply_url"))
        sighting = api.SightingOut(
            source=source,
            origin=Origin.EMAIL,
            url=url,
            first_seen_at=at,
            last_seen_at=at,
            closed=closed,
        )
        base = result.get(row["public_opportunity_id"])
        if base is not None:
            base.sightings.append(sighting)
            if source not in base.sources:
                base.sources.append(source)
            if base.eligibility is None:
                base.eligibility = fields.get("eligibility")
            if base.deadline_at is None:
                base.deadline_at = deadline
            if base.apply_url is None:
                base.apply_url = url
            continue
        result[-row["id"]] = api.OpportunityDetail(
            id=-row["id"],
            title=fields.get("role") or "Imported opportunity",
            company=fields.get("company") or "Unknown",
            city=normalize_city(fields.get("location")),
            location=fields.get("location"),
            description=None,
            is_remote=bool(fields.get("is_remote")),
            url=url,
            apply_url=url,
            posted_at=None,
            first_seen_at=at,
            deadline_at=deadline,
            primary_origin=Origin.EMAIL,
            status=Lifecycle.CLOSED if closed else Lifecycle.ACTIVE,
            sources=[source],
            stipend=fields.get("stipend"),
            eligibility=fields.get("eligibility"),
            funding=None,
            seen=row["seen_at"] is not None,
            bookmarked=row["bookmarked"],
            hidden=row["hidden"],
            notes=row["notes"],
            sightings=[sighting],
        )
    return list(result.values())


def create_hosted_app(engine: Engine | None = None, settings: WebSettings | None = None) -> FastAPI:
    if settings is None:
        settings = WebSettings(
            *[
                os.environ[f"INTERNRADAR_{name}"]
                for name in (
                    "WEB_ORIGIN",
                    "SIGNIN_CLIENT_ID",
                    "SIGNIN_CLIENT_SECRET",
                    "SIGNIN_STATE_SECRET",
                )
            ]
        )
    origin = urlsplit(settings.origin)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.path
        or origin.query
        or origin.fragment
        or len(settings.state_secret) < 32
    ):
        raise ValueError("Configure an HTTPS origin and a strong session state secret")
    engine = engine or create_engine(os.environ["INTERNRADAR_WEB_DATABASE_URL"], pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise ValueError("Hosted API requires PostgreSQL")
    with engine.connect() as conn:
        if (
            conn.scalar(
                text("""
            SELECT rolsuper OR rolbypassrls OR EXISTS (
                SELECT 1 FROM pg_class c WHERE c.relname IN ('account', 'private_opportunity',
                    'user_opportunity', 'mail_delivery', 'mail_connection', 'student_profile',
                    'private_worker_job', 'web_session')
                AND c.relnamespace = 'public'::regnamespace AND pg_has_role(c.relowner, 'MEMBER')
            ) FROM pg_roles WHERE rolname = current_user
        """)
            )
            is not False
        ):
            raise RuntimeError("Hosted API requires a non-owner runtime login without RLS bypass")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def prevent_cross_origin_writes(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.headers.get("origin") != settings.origin
        ):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Origin not permitted"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    install_sign_in(app, engine, settings)

    def owner(request: Request):
        return principal_for(engine, request.cookies.get(COOKIE)).id

    @app.get("/api/session")
    def session_info(request: Request):
        principal = principal_for(engine, request.cookies.get(COOKIE))
        return {"role": principal.role}

    @app.get("/api/opportunities", response_model=list[api.OpportunityOut])
    def listings(
        request: Request,
        view: api.View = "inbox",
        q: str = "",
        origin: Origin | None = None,
        limit: int = 200,
    ):
        user = owner(request)
        with owner_session(engine, user) as session:
            rows = _cards(session, user)
        rows = [
            r
            for r in rows
            if (r.hidden if view == "hidden" else not r.hidden and r.status != Lifecycle.CLOSED)
            and (origin is None or r.primary_origin == origin)
            and (not q.strip() or q.strip().casefold() in (r.title + " " + r.company).casefold())
        ]
        if view == "saved":
            rows = [r for r in rows if r.bookmarked]
        if view == "deadlines":
            rows = sorted(
                (r for r in rows if r.deadline_at and r.deadline_at >= utcnow()),
                key=lambda r: r.deadline_at,
            )
        else:
            rows.sort(
                key=lambda r: (r.seen if view == "inbox" else False, -r.first_seen_at.timestamp())
            )
        return rows[: max(1, min(1000, limit))]

    @app.get("/api/opportunities/{ident}", response_model=api.OpportunityDetail)
    def detail(request: Request, ident: int):
        user = owner(request)
        with owner_session(engine, user) as session:
            return _require_card(session, user, ident)

    @app.patch("/api/opportunities/{ident}", response_model=api.OpportunityOut)
    def patch_card(request: Request, ident: int, patch: api.OpportunityPatch):
        user = owner(request)
        with owner_session(engine, user) as session:
            _require_card(session, user, ident)
            changes = patch.model_dump(exclude_unset=True)
            changes = {k: v for k, v in changes.items() if v is not None or k == "notes"}
            if "seen" in changes:
                changes["seen_at"] = utcnow() if changes.pop("seen") else None
            if "notes" in changes:
                changes["notes"] = (changes["notes"] or "").strip()[:10000] or None
            if changes:
                _write_card(session, user, ident, changes)
            return _require_card(session, user, ident)

    @app.post("/api/opportunities/mark-seen")
    def mark_seen(request: Request):
        user = owner(request)
        with owner_session(engine, user) as session:
            rows = [
                r
                for r in _cards(session, user)
                if not r.seen and not r.hidden and r.status != Lifecycle.CLOSED
            ]
            for row in rows:
                _write_card(session, user, row.id, {"seen_at": utcnow()})
            return {"marked": len(rows)}

    @app.get("/api/overview", response_model=api.Overview)
    def overview(request: Request):
        user = owner(request)
        now = utcnow()
        with owner_session(engine, user) as session:
            cards = _cards(session, user)
            rows = [r for r in cards if r.status != Lifecycle.CLOSED and not r.hidden]
            history = api._latest_per_source(session)
            start = now.astimezone(api.IST).date() - timedelta(days=13)
            days = {
                start + timedelta(days=i): api.DayCount(day=start + timedelta(days=i))
                for i in range(14)
            }
            for card in cards:
                day = days.get(card.first_seen_at.astimezone(api.IST).date())
                if day:
                    key = card.primary_origin.value
                    setattr(day, key, getattr(day, key) + 1)
            inboxes = {
                str(row.id): row.disabled_at is None
                for row in session.execute(
                    text("""
                SELECT id, disabled_at FROM mail_connection WHERE owner_user_id = :owner
            """),
                    {"owner": user},
                )
            }
            funding_count = session.execute(
                text("""
                SELECT count(*) FROM fundingevent WHERE is_tech AND published_at >= :since
            """),
                {"since": now - timedelta(days=30)},
            ).scalar_one()
            return api.Overview(
                open_internships=len(rows),
                unseen=sum(not r.seen for r in rows),
                saved=sum(r.bookmarked for r in rows),
                due_this_week=sum(
                    r.deadline_at is not None and now <= r.deadline_at <= now + timedelta(days=7)
                    for r in rows
                ),
                funded_rounds_30d=funding_count,
                last_run_at=session.execute(text("SELECT max(finished_at) FROM run")).scalar_one(),
                sources_ok=sum(r.ok for r in history),
                sources_failed=sum(not r.ok for r in history),
                fetch_running=False,
                llm_configured=False,
                inboxes=inboxes,
                per_day=list(days.values()),
            )

    @app.get("/api/sources", response_model=list[api.SourceHealth])
    def source_health(request: Request):
        with owner_session(engine, owner(request)) as session:
            return [
                api.SourceHealth.model_validate(r, from_attributes=True)
                for r in api._latest_per_source(session)
            ]

    @app.get("/api/funding", response_model=list[api.FundingOut])
    def funding_feed(request: Request, days: int = 60):
        with owner_session(engine, owner(request)) as session:
            events = session.exec(
                select(FundingEvent)
                .where(
                    FundingEvent.is_tech == True,  # noqa: E712
                    FundingEvent.published_at >= utcnow() - timedelta(days=max(1, min(days, 365))),
                )
                .order_by(FundingEvent.published_at.desc())
            ).all()
            items = []
            for event in events:
                mentions = len(
                    session.exec(
                        select(FundingMention.id).where(FundingMention.event_id == event.id)
                    ).all()
                )
                internships = session.execute(
                    text("""
                    SELECT count(*) FROM opportunity WHERE company_norm = :name
                    AND kind = 'INTERNSHIP' AND status <> 'CLOSED'
                """),
                    {"name": event.company_norm},
                ).scalar_one()
                items.append(
                    api.FundingOut(
                        **event.model_dump(),
                        mentions=mentions,
                        internships=internships,
                        board=f"{event.board_ats}:{event.board_slug}" if event.board_slug else None,
                    )
                )
            return items

    @app.get("/api/fetch", response_model=api.FetchState)
    def fetch_status(request: Request):
        with owner_session(engine, owner(request)) as session:
            last = session.execute(text("SELECT max(finished_at) FROM run")).scalar_one()
            return api.FetchState(running=False, last_finished_at=last)

    @app.post("/api/fetch", response_model=api.FetchState, status_code=202)
    def no_external_refresh(request: Request):
        owner(request)
        return fetch_status(request)  # hosted refresh reloads the scheduled feed

    @app.get("/api/profile")
    def profile(request: Request):
        user = owner(request)
        with owner_session(engine, user) as session:
            result = session.execute(
                text("SELECT data FROM student_profile WHERE owner_user_id = :owner"),
                {"owner": user},
            ).scalar_one_or_none()
            if result is None:
                raise HTTPException(401, "Sign in required")
            return result

    @app.put("/api/profile", response_model=ProfileData)
    def update_profile(request: Request, profile: ProfileData):
        user = owner(request)
        with owner_session(engine, user) as session:
            result = session.execute(
                text("""UPDATE student_profile SET data = CAST(:data AS json)
                    WHERE owner_user_id = :owner"""),
                {"data": profile.model_dump_json(), "owner": user},
            )
            if result.rowcount != 1:
                raise HTTPException(401, "Sign in required")
        return profile

    api._mount_frontend(app)
    return app


def _require_card(session, user, ident):
    if not -(2**31) + 1 <= ident < 2**31 or ident == 0:
        raise HTTPException(404, "opportunity not found")
    for card in _cards(session, user, ident):
        if card.id == ident:
            return card
    raise HTTPException(404, "opportunity not found")


def _write_card(session, user, ident, changes):
    if ident > 0:
        update_state(session, user, ident, **changes)
        return
    columns = [key for key in ("seen_at", "bookmarked", "hidden", "notes") if key in changes]
    assignment = ", ".join(f"{key} = :{key}" for key in columns)
    session.execute(
        text(f"""UPDATE private_opportunity SET {assignment}
        WHERE owner_user_id = :owner AND id = :id"""),
        changes | {"owner": user, "id": -ident},
    )
