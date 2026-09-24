"""Unofficial command line interface for Cronometer."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from cronopy.client import CronometerClient, CronometerError, NotAuthenticatedError
from cronopy.models import Source
from cronopy.session import default_session_path, delete_session, load_session, save_session

app = typer.Typer(
    help="Unofficial cli for cronometer.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _root(
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            "-d",
            help="Log every HTTP request/response and internal step.",
            envvar="CRONOPY_DEBUG",
        ),
    ] = False,
) -> None:
    if not debug:
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(console=err_console, rich_tracebacks=True, show_path=False, markup=False)
        ],
        force=True,
    )
    for name in ("httpx", "cronopy"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.INFO)  # raw socket trace is too noisy
    err_console.print("[dim]debug logging enabled[/dim]")


def _fail(message: str, code: int = 1) -> None:
    err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code)


def _client_from_disk() -> CronometerClient:
    session = load_session()
    if session is None:
        _fail(f"Not logged in (no session at {default_session_path()}). Run `crono login`.")
    return CronometerClient(session)


@app.command()
def login(
    email: Annotated[
        str,
        typer.Option("--email", "-e", prompt=True, help="Cronometer account email."),
    ],
    password: Annotated[
        str,
        typer.Option(
            "--password",
            "-p",
            prompt=True,
            hide_input=True,
            help="Account password (prompted if omitted).",
        ),
    ],
) -> None:
    """Log in and store the session for later commands."""
    try:
        with CronometerClient(email=email, password=password) as client:
            session = client.login()
    except CronometerError as exc:
        _fail(str(exc))
    path = save_session(session)
    console.print(f"[green]Logged in[/green] as {session.email} (user id {session.user_id}).")
    console.print(f"Session saved to [dim]{path}[/dim]")


@app.command()
def logout() -> None:
    """Invalidate the remote session and remove the stored one."""
    session = load_session()
    if session is None:
        console.print("Not logged in; nothing to do.")
        return
    try:
        with CronometerClient(session) as client:
            client.logout()
    except CronometerError as exc:
        err_console.print(f"[yellow]Warning:[/yellow] remote logout failed: {exc}")
    delete_session()
    console.print("[green]Logged out.[/green] Stored session removed.")


@app.command()
def whoami() -> None:
    """Show the currently stored session."""
    session = load_session()
    if session is None:
        console.print("Not logged in.")
        raise typer.Exit(1)
    console.print(f"{session.email or '<unknown>'} (user id {session.user_id})")
    console.print(f"[dim]{default_session_path()}[/dim]")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Food name to search for.")],
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=200, help="Max results.")] = 25,
    sources: Annotated[
        Source, typer.Option("--sources", "-s", help="Cronometer source filter.")
    ] = Source.ALL,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print raw JSON instead of a table.")
    ] = False,
) -> None:
    """Search foods, recipes and meals."""
    try:
        with _client_from_disk() as client:
            results = client.search(query, max_results=limit, sources=sources)
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except NotAuthenticatedError as exc:
        _fail(str(exc))
    except CronometerError as exc:
        _fail(str(exc))

    if as_json:
        console.print_json(json.dumps(results))
        return

    if not results:
        console.print(f"No results for [bold]{query}[/bold].")
        return

    table = Table(title=f"Results for “{query}”", show_lines=False)
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Type", style="magenta")
    table.add_column("Source", style="dim")
    table.add_column("Measure", style="dim")
    for item in results:
        table.add_row(
            str(item.get("id", "")),
            item.get("displayString") or item.get("name", ""),
            str(item.get("type", "")),
            str(item.get("source", "")),
            str(item.get("measureDisplayName", "")),
        )
    console.print(table)


@app.command()
def calories(
    date: Annotated[
        dt.datetime | None,
        typer.Option(
            "--date", "-d", formats=["%Y-%m-%d"], help="Day to report (YYYY-MM-DD, default today)."
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print raw JSON instead of a table.")
    ] = False,
) -> None:
    """Show calories consumed, burned and remaining for a day."""
    day = date.date() if date else dt.date.today()
    try:
        with _client_from_disk() as client:
            summary = client.get_calories(day)
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except NotAuthenticatedError as exc:
        _fail(str(exc))
    except CronometerError as exc:
        _fail(str(exc))

    if as_json:
        console.print_json(json.dumps(summary.to_dict()))
        return

    table = Table(title=f"Calories for {day.isoformat()}", show_header=False)
    table.add_column("Metric")
    table.add_column("kcal", justify="right")
    table.add_row("Consumed", f"{summary.consumed:.0f}")
    table.add_row("Burned", f"{summary.burned:.0f}")
    table.add_row("  BMR", f"[dim]{summary.bmr:.0f}[/dim]")
    table.add_row("  Activity", f"[dim]{summary.activity:.0f}[/dim]")
    table.add_row("  Exercise", f"[dim]{summary.exercise:.0f}[/dim]")
    if summary.custom_target is not None:
        table.add_row("Custom target", f"{summary.custom_target:.0f}")
    else:
        table.add_row("Weight goal", f"{summary.weight_goal_adjustment:+.0f}")
    table.add_row("Target", f"{summary.target:.0f}")
    style = "green" if summary.remaining >= 0 else "red"
    table.add_row("[bold]Remaining[/bold]", f"[bold {style}]{summary.remaining:.0f}[/bold {style}]")
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
