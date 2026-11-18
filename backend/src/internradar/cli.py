from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import col, func, select

from internradar.config import get_settings, load_search_config, load_watchlist
from internradar.db import init_db, session_scope
from internradar.models import Kind, Lifecycle, Opportunity, Sighting, SourceRun, utcnow
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
    sources = build_sources(load_watchlist(), set(only) if only else None)
    if not sources:
        raise typer.BadParameter(f"no source matches {only}")

    with (
        console.status(f"Fetching {len(sources)} sources…"),
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


def _age(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    days = (utcnow() - moment).days
    return "today" if days <= 0 else f"{days}d ago"
