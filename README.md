# cronopy

Unofficial cli for cronometer.

## Install

```sh
uv sync
```

## Usage

```sh
crono login -u you@example.com      # password is prompted (or pass -p)
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

```python
from cronopy import CronometerClient, load_session

with CronometerClient(load_session()) as client:
    results = client.search("chili", max_results=10)
```
