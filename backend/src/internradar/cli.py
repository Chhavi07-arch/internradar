import typer
from rich.console import Console

from internradar.config import get_settings, load_search_config
from internradar.db import init_db

app = typer.Typer(help="InternRadar: one dashboard for every internship.", no_args_is_help=True)
console = Console()


@app.command()
def init() -> None:
    """Create the local database and validate config/search.yaml."""
    settings = get_settings()
    search = load_search_config()
    init_db()
    console.print(f"[green]✓[/] database at {settings.data_dir / 'internradar.db'}")
    console.print(
        f"[green]✓[/] searching {len(search.roles)} roles in "
        f"{', '.join(search.locations.cities)}{' + remote' if search.locations.remote else ''} "
        f"for batch {search.candidate.batch}"
    )


@app.callback()
def _main() -> None:
    """Keeps `init` as a subcommand even while it is the only one."""
