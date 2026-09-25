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
from cronopy.enums import DiaryGroup, Source
from cronopy.models import (
    METRIC_BODY_FAT,
    METRIC_WEIGHT,
    UNIT_KG,
    UNIT_LBS,
    UNIT_PERCENT,
    BiometricPoint,
    DiaryEntry,
    ExerciseEntry,
    FoodInfo,
)
from cronopy.session import default_session_path, delete_session, load_session, save_session

app = typer.Typer(
    help="Unofficial cli for cronometer.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger("cronopy.cli")
err_console = Console(stderr=True)
_state = {"json": False}


def _json_mode() -> bool:
    return _state["json"]


@app.callback()
def _root(
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", "-j", help="Print raw JSON instead of tables.", envvar="CRONOPY_JSON"
        ),
    ] = False,
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
    _state["json"] = as_json
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
    totp_secret: Annotated[
        str | None,
        typer.Option(
            "--totp-secret",
            "-t",
            envvar="CRONOPY_TOTP_SECRET",
            help="Base32 2FA key shown by Cronometer when two-factor auth was set up.",
        ),
    ] = None,
) -> None:
    """Log in and store the session for later commands."""
    try:
        with CronometerClient(email=email, password=password, totp_secret=totp_secret) as client:
            session = client.login()
    except CronometerError as exc:
        _fail(str(exc))
    path = save_session(session)
    tz = f", tz {session.timezone}" if session.timezone else ""
    console.print(f"[green]Logged in[/green] as {session.email} (user id {session.user_id}{tz}).")
    console.print(f"Session saved to [dim]{path}[/dim]")


@app.command()
def logout() -> None:
    """Remove the stored session (the mobile API has no remote logout)."""
    if not delete_session():
        console.print("Not logged in; nothing to do.")
        return
    console.print("[green]Logged out.[/green] Stored session removed.")


@app.command()
def whoami() -> None:
    """Show the currently stored session."""
    session = load_session()
    if session is None:
        console.print("Not logged in.")
        raise typer.Exit(1)
    tz = f", tz {session.timezone}" if session.timezone else ""
    console.print(f"{session.email or '<unknown>'} (user id {session.user_id}{tz})")
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
) -> None:
    """Search foods, recipes and meals."""
    try:
        with _client_from_disk() as client:
            results = client.search(query, max_results=limit, sources=sources)
            foods = {} if _json_mode() else _food_infos(client, results)
    except NotAuthenticatedError as exc:
        _fail(str(exc))
    except CronometerError as exc:
        _fail(str(exc))

    if _json_mode():
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
            str(item.get("name", "")),
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
) -> None:
    """Show a food's measures with their weight and calories."""
    try:
        with _client_from_disk() as client:
            info = client.get_food(food_id)
    except CronometerError as exc:
        _fail(str(exc))
    if _json_mode():
        console.print_json(json.dumps(info.to_dict()))
        return
    per100 = f"{info.kcal_per_100g:.0f} kcal / 100 g" if info.kcal_per_100g is not None else ""
    table = Table(title=f"{info.name or food_id} ({per100})")
    table.add_column("Measure ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Measure (default in bold)")
    table.add_column("Grams", justify="right")
    table.add_column("kcal", justify="right")
    for m in info.measures:
        kcal = info.kcal(m.id)
        name = f"[bold]{m.name}[/bold]" if m.id == info.default_measure_id else m.name
        table.add_row(str(m.id), name, f"{m.grams:g}", f"{kcal:.0f}" if kcal is not None else "")
    console.print(table)


@app.command()
def calories(
    date: Annotated[
        dt.datetime | None,
        typer.Option(
            "--date", "-d", formats=["%Y-%m-%d"], help="Day to report (YYYY-MM-DD, default today)."
        ),
    ] = None,
) -> None:
    """Show calories consumed, target and remaining for a day."""
    day = date.date() if date else dt.date.today()
    try:
        with _client_from_disk() as client:
            summary = client.get_calories(day)
    except NotAuthenticatedError as exc:
        _fail(str(exc))
    except CronometerError as exc:
        _fail(str(exc))

    if _json_mode():
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
    table.add_column("Grams", justify="right")
    for e in entries:
        table.add_row(
            str(e.id),
            e.time.strftime("%H:%M"),
            e.group.name.capitalize(),
            str(e.food_id),
            str(e.measure_id),
            f"{e.grams:g}",
        )
    return table


@app.command()
def diary(date: DateOption = None) -> None:
    """List the food servings, exercises and biometrics logged on a day."""
    day = date.date() if date else dt.date.today()
    try:
        with _client_from_disk() as client:
            diary = client.get_day(day)
    except CronometerError as exc:
        _fail(str(exc))
    servings, exercises, biometrics = diary.servings, diary.exercises, diary.biometrics
    if _json_mode():
        console.print_json(json.dumps(diary.to_dict()))
        return
    if not servings and not exercises and not biometrics:
        console.print(f"No entries on {day.isoformat()}.")
        return
    if servings:
        console.print(_entries_table(day, servings))
    if exercises:
        table = Table(title=f"Exercise for {day.isoformat()}")
        table.add_column("Entry ID", justify="right", style="cyan", no_wrap=True)
        table.add_column("Time")
        table.add_column("Name")
        table.add_column("Minutes", justify="right")
        table.add_column("kcal", justify="right")
        for e in exercises:
            table.add_row(
                str(e.id),
                e.time.strftime("%H:%M"),
                e.name,
                f"{e.minutes:g}",
                f"{e.kcal_burned:.0f}",
            )
        console.print(table)
    if biometrics:
        table = Table(title=f"Biometrics for {day.isoformat()}")
        table.add_column("Entry ID", justify="right", style="cyan", no_wrap=True)
        table.add_column("Time")
        table.add_column("Metric ID", justify="right")
        table.add_column("Unit ID", justify="right", style="dim")
        table.add_column("Value", justify="right")
        table.add_column("Source", style="dim")
        for b in biometrics:
            table.add_row(
                str(b.id),
                b.time.strftime("%H:%M"),
                str(b.metric_id),
                str(b.unit_id),
                f"{b.amount:g}",
                b.source or "",
            )
        console.print(table)


@app.command()
def add(
    food_id: Annotated[int, typer.Argument(help="Food id (see `crono search`).")],
    amount: Annotated[float, typer.Argument(help="Amount, in units of the measure.")],
    measure_id: Annotated[
        int, typer.Option("--measure", "-m", help="Measure id (`measureId` in search --json).")
    ],
    group: Annotated[
        str,
        typer.Option(
            "--group", "-g", help="breakfast|lunch|dinner|snacks (default: by time of day)"
        ),
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
    except CronometerError as exc:
        _fail(str(exc))
    console.print(
        f"[green]Added[/green] entry {entry.id}: food {entry.food_id}, {entry.grams:g} g "
        f"to {entry.group.name.capitalize()} at {entry.time.strftime('%H:%M')} on {entry.day}."
    )


@app.command()
def remove(
    entry_id: Annotated[int, typer.Argument(help="Entry id (see `crono diary`).")],
    date: DateOption = None,
) -> None:
    """Remove a food, exercise or biometric entry from the diary."""
    try:
        with _client_from_disk() as client:
            entry = client.remove_entry(entry_id, date.date() if date else None)
    except CronometerError as exc:
        _fail(str(exc))
    if isinstance(entry, DiaryEntry):
        what = f"food {entry.food_id}, {entry.grams:g} g"
    elif isinstance(entry, ExerciseEntry):
        what = f"exercise {entry.name!r}, {entry.minutes:g} min"
    else:
        what = f"biometric metric {entry.metric_id} = {entry.amount:g}"
    console.print(f"[green]Removed[/green] entry {entry.id} ({what}).")


TimeOption = Annotated[
    dt.datetime | None,
    typer.Option("--time", "-t", formats=["%H:%M", "%H:%M:%S"], help="Time (default now)."),
]


def _stamp(
    date: dt.datetime | None, time: dt.datetime | None
) -> tuple[dt.date | None, dt.time | None]:
    return (date.date() if date else None, time.time() if time else None)


@app.command()
def metrics(
    query: Annotated[str | None, typer.Argument(help="Filter metrics by name.")] = None,
) -> None:
    """List trackable biometrics with their metric and unit ids."""
    try:
        with _client_from_disk() as client:
            found = client.get_metrics()
    except CronometerError as exc:
        _fail(str(exc))
    if query:
        found = [m for m in found if query.lower() in m.name.lower()]
    if _json_mode():
        console.print_json(json.dumps([m.to_dict() for m in found]))
        return
    table = Table(title="Biometric metrics")
    table.add_column("Metric ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Units (id)")
    for m in found:
        table.add_row(str(m.id), m.name, ", ".join(f"{u.name} ({u.id})" for u in m.units))
    console.print(table)


@app.command()
def weight(
    value: Annotated[float, typer.Argument(help="Body weight.")],
    lbs: Annotated[bool, typer.Option("--lbs", help="Value is in pounds instead of kg.")] = False,
    date: DateOption = None,
    time: TimeOption = None,
) -> None:
    """Log body weight."""
    day, at = _stamp(date, time)
    try:
        with _client_from_disk() as client:
            entry = client.add_biometric(
                METRIC_WEIGHT, UNIT_LBS if lbs else UNIT_KG, value, day=day, time=at
            )
    except CronometerError as exc:
        _fail(str(exc))
    unit = "lbs" if lbs else "kg"
    console.print(
        f"[green]Logged[/green] weight {value:g} {unit} on {entry.day} (entry {entry.id})."
    )


@app.command()
def bodyfat(
    percent: Annotated[float, typer.Argument(help="Body fat percentage.")],
    date: DateOption = None,
    time: TimeOption = None,
) -> None:
    """Log body fat percentage."""
    day, at = _stamp(date, time)
    try:
        with _client_from_disk() as client:
            entry = client.add_body_fat(percent, day=day, time=at)
    except CronometerError as exc:
        _fail(str(exc))
    console.print(f"[green]Logged[/green] body fat {percent:g}% on {entry.day} (entry {entry.id}).")


@app.command()
def biometric(
    metric_id: Annotated[int, typer.Argument(help="Metric id (see `crono metrics`).")],
    unit_id: Annotated[int, typer.Argument(help="Unit id (see `crono metrics`).")],
    value: Annotated[float, typer.Argument(help="Reading.")],
    date: DateOption = None,
    time: TimeOption = None,
) -> None:
    """Log any biometric reading."""
    day, at = _stamp(date, time)
    try:
        with _client_from_disk() as client:
            entry = client.add_biometric(metric_id, unit_id, value, day=day, time=at)
    except CronometerError as exc:
        _fail(str(exc))
    console.print(
        f"[green]Logged[/green] metric {metric_id} = {value:g} (unit {unit_id}) on {entry.day} "
        f"(entry {entry.id})."
    )


@app.command()
def biometrics(
    metric_id: Annotated[
        int | None,
        typer.Argument(help="Metric id (see `crono metrics`). Default: weight + body fat."),
    ] = None,
    unit_id: Annotated[
        int | None, typer.Option("--unit", "-u", help="Unit id (default: metric's first unit).")
    ] = None,
    days: Annotated[int, typer.Option("--days", "-n", min=1, help="Days back from today.")] = 30,
) -> None:
    """Show biometric history: weight (kg) and body fat (%) by default, or one metric."""
    series: list[tuple[str, int, int]] = (
        [("Weight (kg)", METRIC_WEIGHT, UNIT_KG), ("Body fat (%)", METRIC_BODY_FAT, UNIT_PERCENT)]
        if metric_id is None
        else [(f"Metric {metric_id}", metric_id, unit_id or 0)]
    )
    try:
        with _client_from_disk() as client:
            end = client.today()
            start = end - dt.timedelta(days=days)
            if metric_id is not None and unit_id is None:
                metric = next((m for m in client.get_metrics() if m.id == metric_id), None)
                if metric is None or not metric.units:
                    raise CronometerError(f"Unknown metric {metric_id}; see `crono metrics`.")
                unit = metric.units[0]
                series = [(f"{metric.name} ({unit.name})", metric.id, unit.id)]
            columns = {
                label: client.get_biometrics(mid, uid, start, end) for label, mid, uid in series
            }
    except CronometerError as exc:
        _fail(str(exc))
    if _json_mode():
        console.print_json(
            json.dumps({label: [p.to_dict() for p in pts] for label, pts in columns.items()})
        )
        return
    if not any(columns.values()):
        console.print(f"No readings in the last {days} days.")
        return
    by_day: dict[dt.date, dict[str, BiometricPoint]] = {}
    for label, pts in columns.items():
        for pt in pts:
            by_day.setdefault(pt.day, {})[label] = pt
    table = Table(title=f"Biometrics, last {days} days")
    table.add_column("Day")
    for label in columns:
        table.add_column(label, justify="right")
    for day in sorted(by_day):
        table.add_row(
            day.isoformat(),
            *(f"{by_day[day][label].value:g}" if label in by_day[day] else "" for label in columns),
        )
    console.print(table)


@app.command()
def activities(
    query: Annotated[str, typer.Argument(help="Activity name to search for.")],
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 25,
) -> None:
    """Search the exercise activity catalog."""
    try:
        with _client_from_disk() as client:
            found = client.find_activity(query)[:limit]
    except CronometerError as exc:
        _fail(str(exc))
    if _json_mode():
        console.print_json(json.dumps([a.to_dict() for a in found]))
        return
    table = Table(title=f"Activities for “{query}”")
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Category", style="magenta")
    for a in found:
        table.add_row(str(a.id), a.name, a.category)
    console.print(table)


@app.command()
def exercise(
    name: Annotated[str, typer.Argument(help="Exercise name as shown in the diary.")],
    minutes: Annotated[float, typer.Argument(help="Duration in minutes.")],
    kcal: Annotated[float, typer.Argument(help="Calories burned.")],
    activity_id: Annotated[
        int, typer.Option("--activity", "-a", help="Activity id (see `crono activities`).")
    ] = 0,
    date: DateOption = None,
    time: TimeOption = None,
) -> None:
    """Log an exercise with the calories it burned."""
    day, at = _stamp(date, time)
    try:
        with _client_from_disk() as client:
            entry = client.add_exercise(
                name, minutes, kcal, activity_id=activity_id, day=day, time=at
            )
    except CronometerError as exc:
        _fail(str(exc))
    console.print(
        f"[green]Logged[/green] {entry.name!r}: {entry.minutes:g} min, "
        f"{entry.kcal_burned:.0f} kcal on {entry.day} (entry {entry.id})."
    )


@app.command()
def goal() -> None:
    """Show the weight goal: weekly rate, target and latest weight."""
    try:
        with _client_from_disk() as client:
            wg = client.get_weight_goal()
    except CronometerError as exc:
        _fail(str(exc))
    if _json_mode():
        console.print_json(json.dumps(wg.to_dict()))
        return
    table = Table(title="Weight goal", show_header=False)
    table.add_column("Item")
    table.add_column("Value", justify="right")
    table.add_row(
        "Rate", f"{wg.rate_kg_per_week:+.2f} kg/week ({wg.rate_lb_per_week:+.2f} lb/week)"
    )
    if wg.target_kg is not None:
        table.add_row("Target", f"{wg.target_kg:g} kg")
    if wg.current_kg is not None:
        when = f" ({wg.weight_date.isoformat()})" if wg.weight_date else ""
        table.add_row("Current", f"{wg.current_kg:g} kg{when}")
    if wg.to_go_kg is not None:
        table.add_row("[bold]To go[/bold]", f"[bold]{wg.to_go_kg:.1f} kg[/bold]")
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
