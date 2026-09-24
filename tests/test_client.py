import datetime as dt
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from httpx import Response

from conftest import BASE, CSRF_COOKIE, PERMUTATION, POLICY_HASH, USER_ID
from cronopy.client import CronometerClient, CronometerError, LoginError, NotAuthenticatedError
from cronopy.models import DiaryEntry, DiaryGroup, Source


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


# Wire order of getCaloriesConsumedAndBurned: reversed double[12], then
# array length, inner type, rows, outer type, string table, flags, version.
# Forward order: [consumed, -exercise, ?, bmr, tef, macro kcal x3, alcohol, activity x2, net]
CALORIES_ROW = [
    -1364.0,
    409.4,
    0.0,
    0.0,
    451.8,
    444.1,
    195.6,
    10.0,
    2047.0,
    67.0,
    -571.0,
    2077.4,
    12,
    2,
    1,
    1,
    ["[[D/158574334", "[D/2047612875"],
    0,
    7,
]


def _rpc_dispatch(prefs: dict[str, str | None], calories: str):
    """Route GWT-RPC calls by method name so one mock serves the whole get_calories flow."""

    def handler(request):
        body = request.content.decode()
        if "|getPreference|" in body:
            key = body.split("|")[9]  # 7th string in the table
            value = prefs.get(key)
            if value is None:
                return Response(200, text="//OK[0,[],0,7]")
            return Response(200, text=f'//OK[1,["{value}"],0,7]')
        return Response(200, text=calories)

    return handler


def test_get_calories_request_and_parse(gwt_routes, session):
    rpc = gwt_routes.post("/cronometer/app").mock(
        side_effect=_rpc_dispatch(
            {"weightGoal": "-1.6534649999999997"}, f"//OK{json.dumps(CALORIES_ROW)}"
        )
    )
    day = dt.date(2026, 9, 24)
    with CronometerClient(session) as client:
        summary = client.get_calories(day)

    bodies = [c.request.content.decode() for c in rpc.calls]
    pref_bodies = [b for b in bodies if "|getPreference|" in b]
    assert any(b.endswith("|NONCE1|weightGoal|1|2|3|4|2|5|5|6|7|") for b in pref_bodies)
    assert any(
        b.endswith("|NONCE1|targets.custom.energy.target|1|2|3|4|2|5|5|6|7|") for b in pref_bodies
    )
    body = next(b for b in bodies if "|getCaloriesConsumedAndBurned|" in b)
    assert "|com.cronometer.shared.entries.models.Day/782579793|" in body
    assert body.endswith(f"|{USER_ID}|7|24|9|2026|7|25|9|2026|")
    assert "|NONCE1|" in body

    assert summary.day == day
    assert summary.consumed == 2077.4
    assert summary.exercise == 571.0
    assert summary.bmr == 2047.0
    assert summary.tef == 10.0
    assert summary.activity == pytest.approx(409.4)
    assert summary.burned == pytest.approx(3027.4)
    assert summary.weight_goal_adjustment == -825.0
    assert summary.custom_target is None
    assert summary.target == pytest.approx(2202.4)
    assert summary.remaining == pytest.approx(125.0)
    assert summary.to_dict()["day"] == "2026-09-24"


def test_get_calories_custom_target(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(
        side_effect=_rpc_dispatch(
            {"weightGoal": "-1.0", "targets.custom.energy.target": "1800"},
            f"//OK{json.dumps(CALORIES_ROW)}",
        )
    )
    with CronometerClient(session) as client:
        summary = client.get_calories(dt.date(2026, 9, 24))
    assert summary.custom_target == 1800.0
    assert summary.target == pytest.approx(1800.0 + 571.0)
    assert summary.remaining == pytest.approx(2371.0 - 2077.4)


def test_get_calories_empty_day(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(
        side_effect=_rpc_dispatch({}, '//OK[0,1,["[[D/158574334"],0,7]')
    )
    with CronometerClient(session) as client:
        summary = client.get_calories(dt.date(2026, 1, 1))
    assert summary.consumed == 0.0
    assert summary.weight_goal_adjustment == 0.0
    assert summary.remaining == 0.0


def test_get_preference(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(side_effect=_rpc_dispatch({"wgkg": "90"}, ""))
    with CronometerClient(session) as client:
        assert client.get_preference("wgkg") == "90"
        assert client.get_preference("missing") is None


def test_get_calories_expired_session(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text="<html>login</html>"))
    with CronometerClient(session) as client, pytest.raises(NotAuthenticatedError):
        client.get_calories()


def test_get_calories_server_exception(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text="//EX[0,0,7]"))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="GWT"):
        client.get_calories()


DAYINFO = (Path(__file__).parent / "dayinfo_response.txt").read_text()


def test_get_diary_parses_servings(gwt_routes, session):
    rpc = gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text=DAYINFO))
    with CronometerClient(session) as client:
        entries = client.get_diary(dt.date(2026, 9, 24))

    body = rpc.calls.last.request.content.decode()
    assert "|getDayInfo|" in body
    assert body.endswith(f"|6|24|9|2026|{USER_ID}|")  # Day type is string #6 here

    assert [e.id for e in entries] == [5205674446, 5205751586, 5207895104]
    milk = entries[-1]
    assert milk == DiaryEntry(
        id=5207895104,
        day=dt.date(2026, 9, 24),
        time=dt.time(20, 45),
        group=DiaryGroup.BREAKFAST,
        order=1,
        food_id=455715,
        measure_id=1025057,
        amount=100.0,
        user_id=17669754,
    )
    assert entries[0].group is DiaryGroup.LUNCH
    assert entries[1].amount == 51.0


def test_get_diary_empty(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(
        return_value=Response(200, text='//OK[0,1,["x/1"],0,7]')
    )
    with CronometerClient(session) as client:
        assert client.get_diary() == []


def test_add_food_sends_add_change_and_returns_created(gwt_routes, session):
    created = (
        '//OK[0,0,1025057,"E2aixA",455715,100.0,17669754,0,45,20,3,262146,0,1,1,2026,9,24,2,1,'
        '["com.cronometer.shared.entries.models.Serving/2553599101",'
        '"com.cronometer.shared.entries.models.Day/782579793",'
        '"com.cronometer.shared.entries.models.Time/1552252503"],0,7]'
    )

    def handler(request):
        body = request.content.decode()
        return Response(200, text=created if "|updateDiary|" in body else DAYINFO)

    rpc = gwt_routes.post("/cronometer/app").mock(side_effect=handler)
    with CronometerClient(session) as client:
        entry = client.add_food(
            455715,
            1025057,
            100,
            group=DiaryGroup.SNACKS,
            day=dt.date(2026, 9, 24),
            time=dt.time(20, 45),
        )

    body = next(
        c.request.content.decode()
        for c in rpc.calls
        if "|updateDiary|" in c.request.content.decode()
    )
    assert "changes.AddEntryChange/3949104564|" in body
    # Snacks (4) << 16 | order 1 (no other snacks that day), time 20:45:00, new id "A"
    packed = 4 << 16 | 1
    assert body.endswith(
        f"|{USER_ID}|9|10|1|1|11|12|24|9|2026|1|1|0|{packed}|13|20|45|0|0|100|455715|A|1025057|0|0|"
    )
    assert entry.id == 5207895104
    assert entry.group is DiaryGroup.SNACKS


def test_remove_food_sends_delete_change_with_full_serving(gwt_routes, session):
    def handler(request):
        body = request.content.decode()
        return Response(200, text="//OK[0,[],0,7]" if "|updateDiary|" in body else DAYINFO)

    rpc = gwt_routes.post("/cronometer/app").mock(side_effect=handler)
    with CronometerClient(session) as client:
        removed = client.remove_food(5207895104, dt.date(2026, 9, 24))

    assert removed.food_id == 455715
    body = next(
        c.request.content.decode()
        for c in rpc.calls
        if "|updateDiary|" in c.request.content.decode()
    )
    assert "changes.DeleteEntryChange/2820697428|" in body
    assert body.endswith(f"|{1 << 16 | 1}|13|20|45|0|17669754|100|455715|E2aixA|1025057|0|0|")


def test_remove_food_unknown_entry(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text=DAYINFO))
    with (
        CronometerClient(session) as client,
        pytest.raises(CronometerError, match="No diary entry"),
    ):
        client.remove_food(1, dt.date(2026, 9, 24))


FOOD = (Path(__file__).parent / "food_response.txt").read_text()


def test_get_food_parses_measures_and_energy(gwt_routes, session):
    rpc = gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text=FOOD))
    with CronometerClient(session) as client:
        info = client.get_food(455715)

    body = rpc.calls.last.request.content.decode()
    assert "|getAllFood|" in body
    # ArrayList of one boxed Integer: list type, size 1, Integer type, value
    assert body.endswith(
        "|7|6|1|8|455715|"
    )  # value: nonce #7, ArrayList #6, size 1, Integer #8, id

    assert info.id == 455715
    assert info.name == "milk, whole (3.5 - 4% fat)"
    assert info.kcal_per_100g == 60.0
    assert [(m.id, m.name, m.quantity, m.grams) for m in info.measures] == [
        (12472318, "cup", 1.0, 244.0),
        (46345006, "individual school container - each 1 CP", 1.0, 244.0),
        (1080638, "oz", 1.0, 28.3495231),
        (1025055, "tbsp", 1.0, 15.2496136),
        (1025054, "tsp", 1.0, 5.0831989),
        (1025057, "g", 1.0, 1.0),
    ]
    assert info.kcal(1025057, 100) == pytest.approx(60.0)
    assert info.kcal(12472318) == pytest.approx(146.4)
    assert info.kcal(999) is None
    oz = info.measure(1080638)
    assert oz is not None
    assert oz.label == "1 oz - 28.3495g"


def test_get_food_missing(gwt_routes, session):
    gwt_routes.post("/cronometer/app").mock(
        return_value=Response(200, text='//OK[0,1,["x/1"],0,7]')
    )
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="not found"):
        client.get_food(1)
    with CronometerClient(session) as client:
        assert client.get_foods([]) == {}


def test_get_foods_two_foods_with_volume_measures(gwt_routes, session):
    two = (Path(__file__).parent / "foods_response.txt").read_text()
    gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text=two))
    with CronometerClient(session) as client:
        foods = client.get_foods([75943603, 455715])

    assert set(foods) == {75943603, 455715}
    cfcd = foods[75943603]
    assert cfcd.kcal_per_100g == pytest.approx(63.887)
    # measures whose Double (ml) slot is set must still parse
    assert [(m.id, m.name, m.grams) for m in cfcd.measures][:2] == [
        (272385630, "cup", 251.0),
        (272381357, "oz", 28.3495231),
    ]
    assert foods[455715].kcal_per_100g == 60.0


def test_get_foods_chunks_requests(gwt_routes, session):
    rpc = gwt_routes.post("/cronometer/app").mock(return_value=Response(200, text=FOOD))
    with CronometerClient(session) as client:
        client.get_foods([*range(1, 61), 1, 2])  # 60 unique ids, duplicates dropped

    bodies = [
        c.request.content.decode()
        for c in rpc.calls
        if "|getAllFood|" in c.request.content.decode()
    ]
    sizes = [int(b.split("|")[-2 - 2 * n]) for b, n in zip(bodies, (25, 25, 10), strict=True)]
    assert sizes == [25, 25, 10]
