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
