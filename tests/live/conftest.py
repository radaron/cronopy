"""Live integration tests against the real Cronometer API.

Authenticates with ``CRONOMETER_EMAIL`` / ``CRONOMETER_PASSWORD`` (CI), or
falls back to the session saved by ``crono login`` (local runs). Skipped when
neither is available. Run with ``make live``. Writes go to a sentinel day far
in the past and are deleted again.
"""

import datetime as dt
import os

import pytest

from cronopy import CronometerClient, load_session

SENTINEL_DAY = dt.date(1900, 1, 2)


@pytest.fixture(scope="session")
def live_client():
    email = os.environ.get("CRONOMETER_EMAIL")
    password = os.environ.get("CRONOMETER_PASSWORD")
    if email and password:
        client = CronometerClient(
            email=email,
            password=password,
            totp_secret=os.environ.get("CRONOMETER_TOTP_SECRET") or None,
        )
        client.login()
    elif (session := load_session()) is not None:
        client = CronometerClient(session)
    else:
        pytest.skip("no CRONOMETER_EMAIL/PASSWORD and no saved `crono login` session")
    with client:
        yield client
        client.logout()
    assert not client.is_authenticated
