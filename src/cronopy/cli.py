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
from cronopy.models import DiaryEntry, DiaryGroup, FoodInfo, Source
from cronopy.session import default_session_path, delete_session, load_session, save_session

app = typer.Typer(
    help="Unofficial cli for cronometer.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger("cronopy.cli")
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


def _food_infos(client: CronometerClient, results: list[dict]) -> dict[int, FoodInfo]:
    """Best-effort kcal lookup for search results; never fails the search itself."""
    try:
        return client.get_foods([r["id"] for r in results if isinstance(r.get("id"), int)])
    except (CronometerError, IndexError, TypeError, ValueError) as exc:
        log.debug("food details unavailable: %s", exc)
        err_console.print(f"[yellow]Warning:[/yellow] could not load calories: {exc}")
        return {}


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
            foods = {} if as_json else _food_infos(client, results)
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
    table.add_column("Measure ID", justify="right", style="dim")
    table.add_column("kcal", justify="right")
    for item in results:
        food = foods.get(item.get("id"))
        kcal = food.kcal(item.get("measureId", -1)) if food else None
        table.add_row(
            str(item.get("id", "")),
            item.get("displayString") or item.get("name", ""),
            str(item.get("type", "")),
            str(item.get("source", "")),
            str(item.get("measureDisplayName", "")),
            str(item.get("measureId", "")),
            f"{kcal:.0f}" if kcal is not None else "",
        )
    console.print(table)


@app.command()
def food(
    food_id: Annotated[int, typer.Argument(help="Food id (see `crono search`).")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a food's measures with their weight and calories."""
    try:
        with _client_from_disk() as client:
            info = client.get_food(food_id)
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except CronometerError as exc:
        _fail(str(exc))
    if as_json:
        console.print_json(json.dumps(info.to_dict()))
        return
    per100 = f"{info.kcal_per_100g:.0f} kcal / 100 g" if info.kcal_per_100g is not None else ""
    table = Table(title=f"{info.name or food_id} ({per100})")
    table.add_column("Measure ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Measure")
    table.add_column("Grams", justify="right")
    table.add_column("kcal", justify="right")
    for m in info.measures:
        kcal = info.kcal(m.id)
        table.add_row(str(m.id), m.label, f"{m.grams:g}", f"{kcal:.0f}" if kcal is not None else "")
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


DateOption = Annotated[
    dt.datetime | None,
    typer.Option("--date", "-d", formats=["%Y-%m-%d"], help="Day (YYYY-MM-DD, default today)."),
]


def _entries_table(day: dt.date, entries: list[DiaryEntry]) -> Table:
    table = Table(title=f"Diary for {day.isoformat()}")
    table.add_column("Entry ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Time")
    table.add_column("Group", style="magenta")
    table.add_column("Food ID", justify="right")
    table.add_column("Measure ID", justify="right", style="dim")
    table.add_column("Amount", justify="right")
    for e in entries:
        table.add_row(
            str(e.id),
            e.time.strftime("%H:%M"),
            e.group.name.capitalize(),
            str(e.food_id),
            str(e.measure_id),
            f"{e.amount:g}",
        )
    return table


@app.command()
def diary(
    date: DateOption = None, as_json: Annotated[bool, typer.Option("--json")] = False
) -> None:
    """List the food servings logged on a day."""
    day = date.date() if date else dt.date.today()
    try:
        with _client_from_disk() as client:
            entries = client.get_diary(day)
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except CronometerError as exc:
        _fail(str(exc))
    if as_json:
        console.print_json(json.dumps([e.to_dict() for e in entries]))
        return
    if not entries:
        console.print(f"No entries on {day.isoformat()}.")
        return
    console.print(_entries_table(day, entries))


@app.command()
def add(
    food_id: Annotated[int, typer.Argument(help="Food id (see `crono search`).")],
    amount: Annotated[float, typer.Argument(help="Amount, in units of the measure.")],
    measure_id: Annotated[
        int, typer.Option("--measure", "-m", help="Measure id (`measureId` in search --json).")
    ],
    group: Annotated[
        str, typer.Option("--group", "-g", help="uncategorized|breakfast|lunch|dinner|snacks")
    ] = "uncategorized",
    time: Annotated[
        dt.datetime | None,
        typer.Option("--time", "-t", formats=["%H:%M", "%H:%M:%S"], help="Time (default now)."),
    ] = None,
    date: DateOption = None,
) -> None:
    """Add a food serving to the diary."""
    try:
        diary_group = DiaryGroup.parse(group)
    except KeyError:
        _fail(
            f"Unknown group {group!r}; use one of {', '.join(g.name.lower() for g in DiaryGroup)}"
        )
    try:
        with _client_from_disk() as client:
            entry = client.add_food(
                food_id,
                measure_id,
                amount,
                group=diary_group,
                day=date.date() if date else None,
                time=time.time() if time else None,
            )
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except CronometerError as exc:
        _fail(str(exc))
    console.print(
        f"[green]Added[/green] entry {entry.id}: food {entry.food_id} x {entry.amount:g} "
        f"to {entry.group.name.capitalize()} at {entry.time.strftime('%H:%M')} on {entry.day}."
    )


@app.command()
def remove(
    entry_id: Annotated[int, typer.Argument(help="Entry id (see `crono diary`).")],
    date: DateOption = None,
) -> None:
    """Remove a food serving from the diary."""
    try:
        with _client_from_disk() as client:
            entry = client.remove_food(entry_id, date.date() if date else None)
            if (updated := client.refresh_session()) is not None:
                save_session(updated)
    except CronometerError as exc:
        _fail(str(exc))
    console.print(
        f"[green]Removed[/green] entry {entry.id} (food {entry.food_id} x {entry.amount:g})."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
