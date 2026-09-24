from urllib.parse import parse_qs

import pytest
from httpx import Response

from conftest import BASE, CSRF_COOKIE, PERMUTATION, POLICY_HASH, USER_ID
from cronopy.client import (
    CronometerClient,
    CronometerError,
    LoginError,
    NotAuthenticatedError,
    Source,
)


def test_login_happy_path(login_routes):
    with CronometerClient(email="me@example.com", password="s3cret") as client:
        session = client.login()

    assert client.is_authenticated
    assert client.session is session
    assert session.user_id == USER_ID
    assert session.email == "me@example.com"
    assert session.cookies["sesnonce"] == "NONCE1"
    assert session.cookies["JSESSIONID"] == "JSID1"

    post = next(c for c in login_routes.calls if c.request.url.path == "/login")
    body = parse_qs(post.request.content.decode(), keep_blank_values=True)
    assert body == {
        "anticsrf": [CSRF_COOKIE],
        "password": ["s3cret"],
        "username": ["me@example.com"],
        "userCode": [""],
    }
    assert post.request.headers["X-Requested-With"] == "XMLHttpRequest"
    assert post.request.headers["Referer"] == f"{BASE}/login/"


def test_login_gwt_authenticate_request(login_routes):
    with CronometerClient(email="me@example.com", password="pw") as client:
        client.login()

    rpc = next(c for c in login_routes.calls if c.request.url.path == "/cronometer/app").request
    assert rpc.headers["X-GWT-Permutation"] == PERMUTATION
    assert rpc.headers["Content-Type"].startswith("text/x-gwt-rpc")
    assert rpc.headers["X-GWT-Module-Base"] == f"{BASE}/cronometer/"
    body = rpc.content.decode()
    assert POLICY_HASH in body
    assert "|authenticate|" in body
    assert "sesnonce=NONCE1" in rpc.headers["Cookie"]


def test_login_missing_csrf_cookie(login_routes):
    login_routes.get("/login/").mock(return_value=Response(200, text="<html/>"))
    with (
        CronometerClient(email="me@example.com", password="pw") as client,
        pytest.raises(LoginError, match="anti-CSRF"),
    ):
        client.login()


def test_login_bad_credentials_no_sesnonce(login_routes):
    login_routes.post("/login").mock(return_value=Response(200, text="bad login"))
    with (
        CronometerClient(email="me@example.com", password="wrong") as client,
        pytest.raises(LoginError, match="bad credentials"),
    ):
        client.login()
    assert client.session is None


def test_login_http_error(login_routes):
    login_routes.post("/login").mock(return_value=Response(500, text="boom"))
    with (
        CronometerClient(email="me@example.com", password="pw") as client,
        pytest.raises(LoginError, match="HTTP 500"),
    ):
        client.login()


def test_login_gwt_authenticate_failure(login_routes):
    login_routes.post("/cronometer/app").mock(return_value=Response(200, text="//EX[...]"))
    with (
        CronometerClient(email="me@example.com", password="pw") as client,
        pytest.raises(LoginError, match="GWT authenticate failed"),
    ):
        client.login()


def test_login_gwt_hash_not_found(login_routes):
    login_routes.get("/cronometer/cronometer.nocache.js").mock(
        return_value=Response(200, text="nothing here")
    )
    with (
        CronometerClient(email="me@example.com", password="pw") as client,
        pytest.raises(CronometerError, match="permutation"),
    ):
        client.login()


def test_restored_session_sends_cookies(api, session):
    route = api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(200, json=[])
    )
    with CronometerClient(session) as client:
        assert client.is_authenticated
        client.search("x")
    cookie = route.calls.last.request.headers["Cookie"]
    assert "sesnonce=NONCE1" in cookie
    assert "JSESSIONID=JSID1" in cookie


def test_refresh_session_returns_none_when_unchanged(api, session):
    api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(return_value=Response(200, json=[]))
    with CronometerClient(session) as client:
        client.search("x")
        assert client.refresh_session() is None


def test_refresh_session_picks_up_rotated_cookie(api, session):
    api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(200, json=[], headers=[("set-cookie", "AWSALB=ALB2; Path=/")])
    )
    with CronometerClient(session) as client:
        client.search("x")
        updated = client.refresh_session()
    assert updated is not None
    assert updated.cookies["AWSALB"] == "ALB2"
    assert updated.cookies["sesnonce"] == "NONCE1"
    assert updated.user_id == USER_ID
    assert updated.email == "me@example.com"
    assert client.session is updated


def test_refresh_session_without_session():
    with CronometerClient() as client:
        assert client.refresh_session() is None


def test_search_params_and_result(api, session):
    payload = [{"id": 1, "name": "Chili", "type": "FOOD"}]
    route = api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(200, json=payload)
    )
    with CronometerClient(session) as client:
        result = client.search("chili", max_results=7, sources=Source.ALL)

    assert result == payload
    params = route.calls.last.request.url.params
    assert params["query"] == "chili"
    assert params["maxResults"] == "7"
    assert params["sources"] == "All"
    assert params["categoryId"] == "0"
    assert params["selectedTab"] == "ALL"
    assert params["type"] == "All"


@pytest.mark.parametrize("status", [401, 403])
def test_search_expired_session(api, session, status):
    api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(status, json={"code": status, "message": "Unauthorized"})
    )
    with CronometerClient(session) as client, pytest.raises(NotAuthenticatedError, match="expired"):
        client.search("x")


def test_search_server_error_raises(api, session):
    api.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(500, text="oops")
    )
    with CronometerClient(session) as client, pytest.raises(Exception, match="500"):
        client.search("x")


def test_search_requires_session():
    with CronometerClient() as client, pytest.raises(NotAuthenticatedError, match="crono login"):
        client.search("x")


def test_logout_sends_rpc_and_clears(gwt_routes, session):
    rpc = gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text="//OK[]"))
    with CronometerClient(session) as client:
        client.logout()
        assert client.session is None
        assert client.is_authenticated is False
        assert len(client._http.cookies) == 0

    body = rpc.calls.last.request.content.decode()
    assert "|logout|" in body
    assert "|NONCE1|" in body
    assert POLICY_HASH in body
    assert rpc.calls.last.request.headers["X-GWT-Permutation"] == PERMUTATION


def test_logout_clears_even_if_remote_fails(api, session):
    api.get("/cronometer/cronometer.nocache.js").mock(return_value=Response(500, text=""))
    with CronometerClient(session) as client, pytest.raises(CronometerError):
        client.logout()
    assert client.session is None


def test_logout_requires_session():
    with CronometerClient() as client, pytest.raises(NotAuthenticatedError):
        client.logout()


def test_constructor_rejects_partial_credentials():
    with pytest.raises(ValueError, match="together"):
        CronometerClient(email="me@example.com")
    with pytest.raises(ValueError, match="together"):
        CronometerClient(password="pw")


def test_credentials_login_lazily_on_first_use(login_routes):
    search = login_routes.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(200, json=[{"id": 1}])
    )
    with CronometerClient(email="me@example.com", password="pw") as client:
        assert client.has_credentials
        assert not client.is_authenticated
        assert client.search("x") == [{"id": 1}]
        assert client.is_authenticated
        assert client.session is not None
        assert client.session.email == "me@example.com"
        client.search("y")

    post = next(c for c in login_routes.calls if c.request.url.path == "/login")
    assert parse_qs(post.request.content.decode())["password"] == ["pw"]
    assert sum(1 for c in login_routes.calls if c.request.url.path == "/login") == 1
    assert search.call_count == 2


def test_login_without_args_and_without_credentials():
    with CronometerClient() as client, pytest.raises(LoginError, match="No credentials"):
        client.login()


def test_expired_session_is_not_retried_even_with_credentials(login_routes, session):
    search = login_routes.get(f"/api/v3/user/{USER_ID}/food-search/string").mock(
        return_value=Response(401, json={"code": 401})
    )
    with (
        CronometerClient(session, email="me@example.com", password="pw") as client,
        pytest.raises(NotAuthenticatedError, match="expired"),
    ):
        client.search("x")
    assert search.call_count == 1
    assert not any(c.request.url.path == "/login" for c in login_routes.calls)
    assert client.session is session
