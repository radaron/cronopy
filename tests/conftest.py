import pytest
import respx
from httpx import Response

from cronopy.session import Session

BASE = "https://cronometer.com"
PERMUTATION = "D79A34972B4F036131906DFAC5BF1EA4"
POLICY_HASH = "13F6CC6C06AE73A6E95DE9B0233AB365"
CSRF_COOKIE = "fe38aca073205243161194197866a502"
USER_ID = 17689734


@pytest.fixture
def api():
    """respx router bound to cronometer.com; unmatched requests raise."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def gwt_routes(api):
    """Routes serving the GWT bootstrap files that carry the permutation/policy hashes."""
    api.get("/cronometer/cronometer.nocache.js").mock(
        return_value=Response(
            200,
            text=f"var x = '{PERMUTATION}';",
            headers={"content-type": "application/javascript"},
        )
    )
    api.get(f"/cronometer/{PERMUTATION}.cache.js").mock(
        return_value=Response(
            200,
            text=f"foo('app','{POLICY_HASH}')",
            headers={"content-type": "application/javascript"},
        )
    )
    return api


@pytest.fixture
def login_routes(gwt_routes):
    """Happy-path login flow."""
    api = gwt_routes
    api.get("/login/").mock(
        return_value=Response(
            200,
            text="<html>login</html>",
            headers=[
                ("set-cookie", "JSESSIONID=JSID1; Path=/; Secure; HttpOnly"),
                ("set-cookie", f"{CSRF_COOKIE}=tokenvalue; Path=/; Secure; HttpOnly"),
            ],
        )
    )
    api.post("/login").mock(
        return_value=Response(
            200,
            text="OK",
            headers=[("set-cookie", "sesnonce=NONCE1; Path=/; Secure; HttpOnly")],
        )
    )
    api.get("/").mock(return_value=Response(200, text="<html>app</html>"))
    api.post("/cronometer/app").mock(return_value=Response(200, text=f"//OK[{USER_ID},1,2]"))
    return api


@pytest.fixture
def session():
    return Session(
        user_id=USER_ID,
        email="me@example.com",
        cookies={"sesnonce": "NONCE1", "JSESSIONID": "JSID1", "AWSALB": "ALB1"},
    )
