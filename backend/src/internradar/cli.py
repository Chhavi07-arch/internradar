from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import col, func, select

from internradar.config import Watchlist, get_settings, load_search_config, load_watchlist
from internradar.db import init_db, session_scope
from internradar.funding.radar import funded_companies, make_client, run_radar
from internradar.models import (
    FundingEvent,
    FundingMention,
    Kind,
    Lifecycle,
    Opportunity,
    Sighting,
    SourceRun,
    utcnow,
)
from internradar.pipeline import run_fetch
from internradar.sources import build_sources

app = typer.Typer(help="InternRadar: one dashboard for every internship.", no_args_is_help=True)
console = Console()


@app.command()
def init() -> None:
    """Create the local database and validate the config files."""
    settings = get_settings()
    search = load_search_config()
    watchlist = load_watchlist()
    init_db()
    console.print(f"[green]✓[/] database at {settings.data_dir / 'internradar.db'}")
    console.print(
        f"[green]✓[/] searching {len(search.roles)} roles in "
        f"{', '.join(search.locations.cities)}{' + remote' if search.locations.remote else ''} "
        f"for batch {search.candidate.batch}"
    )
    console.print(f"[green]✓[/] watching {len(watchlist.companies)} company career boards")


@app.command()
def fetch(
    only: list[str] = typer.Option(  # noqa: B008
        None, "--only", "-o", help='Source name ("linkedin", "ashby:sarvam") or "boards" / "ats".'
    ),
) -> None:
    """Fetch every source once and store new internships."""
    init_db()
    search = load_search_config()
    watchlist = load_watchlist()
    with session_scope() as session:
        radar = funded_companies(session, watchlist)
    sources = build_sources(
        Watchlist(companies=watchlist.companies + radar), set(only) if only else None
    )
    if not sources:
        raise typer.BadParameter(f"no source matches {only}")

    with (
        console.status(f"Fetching {len(sources)} sources ({len(radar)} from the funding radar)…"),
        session_scope() as session,
    ):
        summary = run_fetch(session, sources, search)
        rows = [
            (s.source, s.ok, s.fetched, s.kept, s.new, s.duration_ms, s.error)
            for s in summary.sources
        ]

    table = Table(title=f"Run #{summary.run_id}", title_justify="left")
    for column in ("Source", "OK", "Fetched", "Relevant", "New", "Time", "Error"):
        table.add_column(
            column, justify="right" if column in ("Fetched", "Relevant", "New", "Time") else "left"
        )
    for source, ok, fetched, kept, new, ms, error in sorted(rows, key=lambda r: (-r[4], r[0])):
        table.add_row(
            source, "✓" if ok else "[red]✗[/]", str(fetched), str(kept),
            f"[bold green]{new}[/]" if new else "0", f"{ms / 1000:.1f}s", (error or "")[:60],
        )  # fmt: skip
    console.print(table)
    console.print(
        f"[bold]{sum(r[4] for r in rows)} new internships.[/] Run `internradar list --new`."
    )


@app.command("list")
def list_(
    new: bool = typer.Option(False, "--new", help="Only internships found in the latest run."),
    limit: int = typer.Option(40, "--limit", "-n"),
) -> None:
    """Show open internships, newest first."""
    init_db()
    with session_scope() as session:
        sources_per_opp = (
            select(
                Sighting.opportunity_id, func.group_concat(Sighting.source.distinct()).label("seen")
            )
            .where(Sighting.closed_at.is_(None))
            .group_by(Sighting.opportunity_id)
            .subquery()
        )
        query = (
            select(Opportunity, sources_per_opp.c.seen)
            .join(sources_per_opp, sources_per_opp.c.opportunity_id == Opportunity.id)
            .where(
                Opportunity.kind == Kind.INTERNSHIP,
                Opportunity.status != Lifecycle.CLOSED,
                col(Opportunity.hidden).is_(False),
            )
            .order_by(col(Opportunity.first_seen_at).desc(), col(Opportunity.posted_at).desc())
            .limit(limit)
        )
        if new:
            query = query.where(Opportunity.status == Lifecycle.NEW)
        rows = [
            (o.status, o.company, o.title, o.city or "—", seen, _age(o.posted_at), o.url)
            for o, seen in session.exec(query)
        ]

    table = Table(show_lines=False)
    for column in ("", "Company", "Role", "Where", "Seen on", "Posted"):
        table.add_column(column, overflow="fold")
    for status, company, title, city, seen, age, url in rows:
        title_cell = f"[link={url}]{title}[/link]" if url else title
        marker = "[bold green]●[/]" if status == Lifecycle.NEW else ""
        table.add_row(marker, company, title_cell, city, seen, age)
    console.print(table)
    console.print(f"{len(rows)} shown. Click a role to open it (in terminals that support links).")


@app.command()
def sources() -> None:
    """Health of each source in the latest run."""
    init_db()
    with session_scope() as session:
        latest = session.exec(select(func.max(SourceRun.run_id))).one()
        runs = (
            session.exec(select(SourceRun).where(SourceRun.run_id == latest)).all()
            if latest
            else []
        )
        rows = [(r.source, r.ok, r.fetched, r.kept, r.new, r.error) for r in runs]
    table = Table(title=f"Run #{latest}" if latest else "No runs yet", title_justify="left")
    for column in ("Source", "OK", "Fetched", "Relevant", "New", "Error"):
        table.add_column(column)
    for source, ok, fetched, kept, new, error in rows:
        table.add_row(
            source, "✓" if ok else "[red]✗[/]", str(fetched), str(kept), str(new), error or ""
        )
    console.print(table)


@app.command()
def radar(
    days: int = typer.Option(60, "--days", "-d", help="Show rounds from the last N days."),
    show_all: bool = typer.Option(False, "--all", help="Include non-tech companies."),
    offline: bool = typer.Option(False, "--offline", help="Only show stored rounds."),
) -> None:
    """Scan funding news, find funded startups' careers boards, and show the hiring signals."""
    init_db()
    watchlist = load_watchlist()
    if not offline:
        with (
            console.status("Reading funding news and looking for careers boards…"),
            make_client() as client,
            session_scope() as session,
        ):
            summary = run_radar(session, watchlist, client)
            new_rounds = len(summary.new_events)
            boards = [f"{e.company} → {e.board_ats}:{e.board_slug}" for e in summary.boards_found]
        for feed, result in summary.feeds.items():
            ok = isinstance(result, int)
            console.print(f"{'[green]✓[/]' if ok else '[red]✗[/]'} {feed}: {result}")
        console.print(f"[bold]{new_rounds} new rounds, {len(boards)} careers boards found.[/]")
        for board in boards:
            console.print(f"  [green]+[/] {board}")

    with session_scope() as session:
        mentions = (
            select(FundingMention.event_id, func.count().label("n"))
            .group_by(FundingMention.event_id)
            .subquery()
        )
        internships = (
            select(Opportunity.company_norm, func.count().label("n"))
            .where(Opportunity.kind == Kind.INTERNSHIP, Opportunity.status != Lifecycle.CLOSED)
            .group_by(Opportunity.company_norm)
            .subquery()
        )
        query = (
            select(FundingEvent, mentions.c.n, internships.c.n)
            .join(mentions, mentions.c.event_id == FundingEvent.id)
            .outerjoin(internships, internships.c.company_norm == FundingEvent.company_norm)
            .where(FundingEvent.published_at >= utcnow() - timedelta(days=days))
            .order_by(col(FundingEvent.published_at).desc())
        )
        if not show_all:
            query = query.where(col(FundingEvent.is_tech).is_(True))
        watched = {c.name.lower(): c for c in watchlist.companies}
        rows = [
            (
                e.published_at, e.company, e.round, e.amount_text, e.lead_investors, e.url,
                f"{e.board_ats}:{e.board_slug}" if e.board_slug
                else "watchlist" if e.company.lower() in watched
                else "—",
                n_mentions, n_interns or 0,
            )
            for e, n_mentions, n_interns in session.exec(query)
        ]  # fmt: skip

    table = Table(title=f"Funding radar: last {days} days", title_justify="left")
    for column in ("Date", "Company", "Round", "Amount", "Led by", "Careers board", "Interns"):
        table.add_column(column, overflow="fold")
    for when, company, round_, amount, lead, url, board, n_mentions, n_interns in rows:
        name = f"[link={url}]{company}[/link]" + (
            f" [dim]x{n_mentions}[/]" if n_mentions > 1 else ""
        )
        table.add_row(
            f"{when:%d %b}", name, round_ or "—", amount or "—", lead or "—",
            f"[green]{board}[/]" if ":" in board else board,
            f"[bold green]{n_interns}[/]" if n_interns else "—",
        )  # fmt: skip
    console.print(table)
    console.print("[dim]xN = reported by N outlets. Boards found here are polled by `fetch`.[/]")


def _age(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    days = (utcnow() - moment).days
    return "today" if days <= 0 else f"{days}d ago"
