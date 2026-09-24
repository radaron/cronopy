# cronopy

Unofficial cli for cronometer, built on the JSON REST API used by the
Cronometer mobile app (`mobile.cronometer.com`).

The idea and the API knowledge come from
[rwestergren/cronometer-api-mcp](https://github.com/rwestergren/cronometer-api-mcp/tree/main),
an MCP server that reverse-engineered the mobile endpoints. cronopy wraps the
same API in a command line tool and a small Python library.

## Install

```sh
uv sync
```

## Usage

```sh
crono login -e you@example.com      # password is prompted (or pass -p)
crono login -e you@example.com -t "ABCD EFGH ..."   # account with 2FA: base32 TOTP key
crono search "chili" -n 10          # table output
crono search "chili" --json         # raw JSON
crono food 455715                   # measures of a food with grams and kcal
crono calories                      # consumed / target / remaining for today
crono calories -d 2026-09-20 --json # another day, raw JSON
crono diary                         # servings logged today (entry ids)
crono add 455715 100 -m 1025057 -g breakfast -t 08:30   # food id, amount, measure id
crono remove 5207940830             # any entry id from `crono diary` (food, exercise, biometric)
crono weight 106.5                  # log body weight in kg (--lbs for pounds)
crono bodyfat 39.5                  # log body fat %
crono metrics                       # metric and unit ids for other biometrics
crono biometric 3 5 56              # metric id, unit id, value (here: heart rate 56 bpm)
crono biometrics                    # weight and body fat history, last 30 days (-n 90)
crono biometrics 3                  # one metric by id, first unit unless -u given
crono activities "walking"          # search the exercise catalog for activity ids
crono exercise "Run" 30 300 -a 12   # name, minutes, kcal burned, optional activity id
crono goal                          # weight goal: weekly rate, target, current, to go
crono whoami
crono logout
```

The session (user id and API token) is stored in `~/.config/cronopy/session.json`
(or `$XDG_CONFIG_HOME/cronopy/session.json`) and reused by every command.
Cronometer rate-limits logins, so log in once and keep the session. When it
expires, commands fail with "Session expired"; run `crono login` again.

Diary dates and "today" use the timezone of your Cronometer account, which is
learned at login. The TOTP secret can also be given via `CRONOPY_TOTP_SECRET`.

## Library

With credentials (logs in lazily on first use):

```python
from cronopy import CronometerClient

with CronometerClient(email="you@example.com", password="...") as client:
    results = client.search("chili", max_results=10)
    today = client.get_calories()  # CalorieSummary for today
    print(today.consumed, today.target, today.remaining)
```

With a saved session (for example the one written by `crono login`):

```python
from cronopy import CronometerClient, load_session

with CronometerClient(load_session()) as client:
    food = client.get_food(455715)
    entry = client.add_food(food.id, food.default_measure_id, amount=1)
    client.remove_food(entry.id)

    client.add_weight(106.5)  # kg
    client.add_body_fat(39.5)  # percent
    client.add_exercise("Run", minutes=30, kcal_burned=300)
    today = client.get_day()  # servings, exercises, biometrics, calories
    history = client.get_biometrics(metric_id=1, unit_id=1)  # weight, last 30 days
    goal = client.get_weight_goal()  # rate_kg_per_week, target_kg, current_kg
    client.remove_entry(today.exercises[0].id)
```

An expired session raises `NotAuthenticatedError`. Handle it by calling
`client.login()` (needs credentials) and retrying:

```python
from cronopy import CronometerClient, NotAuthenticatedError, load_session, save_session

with CronometerClient(load_session(), email="you@example.com", password="...") as client:
    try:
        results = client.search("chili")
    except NotAuthenticatedError:
        save_session(client.login())
        results = client.search("chili")
```

## Development

```sh
make test      # unit tests (mocked HTTP)
make lint      # ruff + ty
make live      # integration tests against the real API
```

`make live` uses `CRONOMETER_EMAIL` / `CRONOMETER_PASSWORD` (and
`CRONOMETER_TOTP_SECRET` for 2FA), or the session saved by `crono login` when
those are unset. It logs a weight, an exercise and a food serving on
1900-01-02 and deletes them again. The "Live API check" GitHub workflow runs
it daily from the repository secrets of the same names.

## Endpoints used

| Call | Endpoint |
|------|----------|
| login | `POST /api/v2/login` |
| search | `POST /api/v2/find_food` |
| food details | `POST /api/v2/get_food`, `POST /api/v2/get_foods` |
| diary, calories | `POST /api/v2/get_diary` (servings, exercises, biometrics and `summary`) |
| add food | `POST /api/v2/add_serving` |
| add biometric | `POST /api/v2/add_biometric` |
| add exercise | `POST /api/v2/add_exercise` |
| remove entry | `DELETE /api/v3/user/{id}/diary-entries` (any type; strip `meta`, biometrics need `id`) |
| metrics, history | `POST /api/v2/get_metrics`, `POST /api/v2/get_biometrics` |
| activities | `POST /api/v2/find_activity` |
| weight goal | `POST /api/v2/get_profile` (`prefs`: `weightGoal` lb/week, `wgkg` target) |

`add_biometric` and `add_exercise` were found by probing the live API; they are
not part of cronometer-api-mcp.
