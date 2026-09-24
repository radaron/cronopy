# cronopy

Unofficial cli for cronometer.

## Install

```sh
uv sync
```

## Usage

```sh
crono login -e you@example.com      # password is prompted (or pass -p)
crono search "chili" -n 10          # table output
crono search "chili" --json         # raw JSON
crono food 455715                   # measures of a food with grams and kcal
crono calories                      # consumed / burned / remaining for today
crono calories -d 2026-09-20 --json # another day, raw JSON
crono diary                         # servings logged today (entry ids)
crono add 455715 100 -m 1025057 -g breakfast -t 08:30   # food id, amount, measure id
crono remove 5207940830             # entry id from `crono diary`
crono whoami
crono logout
```

The session (cookies and user id) is stored in `~/.config/cronopy/session.json`
(or `$XDG_CONFIG_HOME/cronopy/session.json`) and reused by every command.
GWT hashes are fetched on demand and never persisted, since they change with
each Cronometer frontend deploy.

## Library

With credentials (logs in lazily on first use):

```python
from cronopy import CronometerClient

with CronometerClient(email="you@example.com", password="...") as client:
    results = client.search("chili", max_results=10)
    today = client.get_calories()  # CalorieSummary for today
    print(today.consumed, today.target, today.remaining)  # target = burned + weight goal
```

With a saved session (for example the one written by `crono login`):

```python
from cronopy import CronometerClient, load_session, save_session

with CronometerClient(load_session()) as client:
    results = client.search("chili", max_results=10)
    if (updated := client.refresh_session()) is not None:
        save_session(updated)
```

An expired session raises `NotAuthenticatedError`. Handle it by calling
`client.login()` (needs credentials) and retrying:

```python
from cronopy import CronometerClient, NotAuthenticatedError, load_session

with CronometerClient(load_session(), email="you@example.com", password="...") as client:
    try:
        results = client.search("chili")
    except NotAuthenticatedError:
        client.login()
        results = client.search("chili")
```
