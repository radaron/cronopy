import datetime as dt

import pytest

from conftest import (
    DIARY,
    FOOD_MILK,
    FOOD_OATS,
    FOOD_RECIPE,
    LOGIN_OK,
    TOKEN,
    USER_ID,
    json_response,
    sent_json,
)
from cronopy.client import CronometerClient, CronometerError, LoginError, NotAuthenticatedError
from cronopy.enums import DiaryGroup
from cronopy.util import format_day, parse_day, parse_time, totp_code


def test_login_happy_path(api):
    route = api.post("/api/v2/login").mock(return_value=json_response(LOGIN_OK))
    with CronometerClient(email="me@example.com", password="pw") as client:
        session = client.login()
    assert session.user_id == USER_ID
    assert session.token == TOKEN
    assert session.email == "me@example.com"
    assert session.timezone == "Europe/Budapest"
    body = sent_json(route)
    assert body["email"] == "me@example.com"
    assert body["password"] == "pw"
    assert body["timezone"] is None
    assert body["userCode"] is None
    assert body["auth"] == {"userId": None, "token": None, "api": 3, "os": "Android",
                            "build": "2807", "flavour": "free"}  # fmt: skip


def test_login_sends_totp_code(api):
    route = api.post("/api/v2/login").mock(return_value=json_response(LOGIN_OK))
    with CronometerClient(email="e", password="p", totp_secret="JBSW Y3DP EHPK 3PXP") as client:
        client.login()
    code = sent_json(route)["userCode"]
    assert isinstance(code, str)
    assert len(code) == 6
    assert code.isdigit()


def test_login_unknown_timezone_dropped(api):
    api.post("/api/v2/login").mock(
        return_value=json_response({**LOGIN_OK, "timezone": "Mars/Olympus"})
    )
    with CronometerClient(email="e", password="p") as client:
        assert client.login().timezone is None


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"result": "FAIL", "error": "Invalid credentials"}, "Invalid credentials"),
        ({"result": "FAIL", "error": "TOTP_CODE_REQUIRED"}, "two-factor"),
    ],
)
def test_login_failure_body(api, body, match):
    api.post("/api/v2/login").mock(return_value=json_response(body))
    with (
        CronometerClient(email="e", password="p") as client,
        pytest.raises(LoginError, match=match),
    ):
        client.login()


def test_login_http_error(api):
    api.post("/api/v2/login").mock(return_value=json_response({}, status=429))
    with (
        CronometerClient(email="e", password="p") as client,
        pytest.raises(LoginError, match="429"),
    ):
        client.login()


def test_constructor_rejects_partial_credentials():
    with pytest.raises(ValueError, match="together"):
        CronometerClient(email="only@example.com")


def test_login_without_credentials():
    with CronometerClient() as client, pytest.raises(LoginError):
        client.login()


def test_credentials_login_lazily_on_first_use(api):
    login = api.post("/api/v2/login").mock(return_value=json_response(LOGIN_OK))
    find = api.post("/api/v2/find_food").mock(return_value=json_response({"foods": []}))
    with CronometerClient(email="e", password="p") as client:
        assert not client.is_authenticated
        assert client.search("x") == []
        assert client.is_authenticated
    assert login.call_count == 1
    assert sent_json(find)["auth"]["token"] == TOKEN


def test_expired_session_is_not_retried_even_with_credentials(api, session):
    login = api.post("/api/v2/login")
    api.post("/api/v2/find_food").mock(return_value=json_response({}, status=401))
    with (
        CronometerClient(session, email="e", password="p") as client,
        pytest.raises(NotAuthenticatedError),
    ):
        client.search("x")
    assert login.call_count == 0


def test_requires_session():
    with CronometerClient() as client, pytest.raises(NotAuthenticatedError):
        client.search("x")


def test_logout_forgets_session(session):
    with CronometerClient(session) as client:
        client.logout()
        assert client.session is None


def test_totp_code_rfc6238_vector():
    # RFC 6238 test vector: secret "12345678901234567890", T=59 -> 287082 (SHA-1)
    secret_b32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret_b32, for_time=59) == "287082"


def test_v2_auth_block_and_expired_status(api, session):
    route = api.post("/api/v2/find_food").mock(return_value=json_response({"foods": []}))
    with CronometerClient(session) as client:
        client.search("chili")
    body = sent_json(route)
    assert body["auth"] == {"userId": USER_ID, "token": TOKEN, "api": 3, "os": "Android",
                            "build": "2807", "flavour": "free"}  # fmt: skip
    assert body["lastSeen"] == 0


@pytest.mark.parametrize("status", [401, 403])
def test_v2_expired_status(api, session, status):
    api.post("/api/v2/find_food").mock(return_value=json_response({}, status=status))
    with CronometerClient(session) as client, pytest.raises(NotAuthenticatedError):
        client.search("x")


def test_v2_fail_body_with_auth_error_is_expired(api, session):
    api.post("/api/v2/find_food").mock(
        return_value=json_response({"result": "FAIL", "error": "Invalid session token"})
    )
    with CronometerClient(session) as client, pytest.raises(NotAuthenticatedError):
        client.search("x")


def test_v2_fail_body_other_error(api, session):
    api.post("/api/v2/find_food").mock(
        return_value=json_response({"result": "FAIL", "error": "Something else"})
    )
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="Something"):
        client.search("x")


def test_v2_server_error(api, session):
    api.post("/api/v2/find_food").mock(return_value=json_response({}, status=500))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="500"):
        client.search("x")


def test_today_uses_account_timezone(session):
    with CronometerClient(session) as client:
        assert client.now().tzinfo is not None
        assert str(client.now().tzinfo) == "Europe/Budapest"


def test_format_day_not_zero_padded():
    assert format_day(dt.date(2026, 9, 4)) == "2026-9-4"


def test_parse_day_and_time():
    assert parse_day("2026-9-4") == dt.date(2026, 9, 4)
    assert parse_time("8:5:3") == dt.time(8, 5, 3)
    assert parse_time("19:0") == dt.time(19, 0)


def test_search_payload_and_limit(api, session):
    foods = [{"id": i, "name": f"f{i}"} for i in range(5)]
    route = api.post("/api/v2/find_food").mock(return_value=json_response({"foods": foods}))
    with CronometerClient(session) as client:
        results = client.search("chili", max_results=3)
    assert [r["id"] for r in results] == [0, 1, 2]
    body = sent_json(route)
    assert body["query"] == "chili"
    assert body["sources"] == ["All"]
    assert body["tab"] == "ALL"


def test_get_food_parses_measures_and_energy(api, session):
    route = api.post("/api/v2/get_food").mock(return_value=json_response(FOOD_OATS))
    with CronometerClient(session) as client:
        info = client.get_food(100)
    assert sent_json(route)["id"] == 100
    assert info.name == "Oats"
    assert info.kcal_per_100g == 389.0
    assert info.default_measure_id == 10
    assert [(m.id, m.name, m.grams) for m in info.measures] == [(10, "cup", 81.0), (11, "g", 1.0)]
    assert info.kcal(10) == pytest.approx(389.0 * 0.81)
    assert info.kcal(11, 50) == pytest.approx(194.5)
    assert info.kcal(999) is None


def test_get_food_recipe_measure_kcal_per_serving(api, session):
    api.post("/api/v2/get_food").mock(return_value=json_response(FOOD_RECIPE))
    with CronometerClient(session) as client:
        info = client.get_food(300)
    assert info.kcal(30, 2) == pytest.approx(1417.0)


def test_get_food_missing(api, session):
    api.post("/api/v2/get_food").mock(return_value=json_response({}))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="not found"):
        client.get_food(1)


def test_get_foods_batches_ids(api, session):
    route = api.post("/api/v2/get_foods").mock(
        return_value=json_response({"foods": [FOOD_OATS, FOOD_MILK]})
    )
    with CronometerClient(session) as client:
        foods = client.get_foods([100, 200, 100])
    assert sent_json(route)["ids"] == [100, 200]
    assert set(foods) == {100, 200}
    assert foods[200].measures[0].type == "Volume"


def test_get_foods_empty_makes_no_request(api, session):
    route = api.post("/api/v2/get_foods")
    with CronometerClient(session) as client:
        assert client.get_foods([]) == {}
    assert route.call_count == 0


def test_get_diary_parses_servings(api, session):
    route = api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    with CronometerClient(session) as client:
        entries = client.get_diary(dt.date(2026, 9, 24))
    assert sent_json(route)["day"] == "2026-9-24"
    assert [e.id for e in entries] == [5207940830, 5207940831, 5207940832]
    first = entries[0]
    assert first.day == dt.date(2026, 9, 24)
    assert first.time == dt.time(8, 30)
    assert first.group is DiaryGroup.BREAKFAST
    assert first.order == 1
    assert first.food_id == 100
    assert first.measure_id == 10
    assert first.grams == 81.0
    assert first.user_id == USER_ID
    assert entries[2].group is DiaryGroup.DINNER
    assert entries[2].time == dt.time(19, 0)
    assert first.raw["servingId"] == 5207940830


def test_get_diary_empty(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response({"diary": []}))
    with CronometerClient(session) as client:
        assert client.get_diary() == []


def test_get_calories_from_summary(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    with CronometerClient(session) as client:
        summary = client.get_calories(dt.date(2026, 9, 24))
    assert summary.consumed == 1800.5
    assert summary.target == 2200.0
    assert summary.remaining == pytest.approx(399.5)
    assert summary.to_dict()["day"] == "2026-09-24"


def test_get_calories_missing_summary(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response({"diary": []}))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="summary"):
        client.get_calories()


def test_add_food_converts_amount_to_grams(api, session):
    api.post("/api/v2/get_food").mock(return_value=json_response(FOOD_OATS))
    route = api.post("/api/v2/add_serving").mock(
        return_value=json_response({"result": "SUCCESS", "id": 999})
    )
    with CronometerClient(session) as client:
        entry = client.add_food(
            100, 10, 2, group=DiaryGroup.BREAKFAST, day=dt.date(2026, 9, 24), time=dt.time(8, 5, 3)
        )
    sent = sent_json(route)["serving"]
    assert sent["foodId"] == 100
    assert sent["measureId"] == 10
    assert sent["grams"] == pytest.approx(162.0)
    assert sent["day"] == "2026-9-24"
    assert sent["time"] == "8:5:3"
    assert sent["order"] == (1 << 16) | 1
    assert sent["userId"] == USER_ID
    assert sent["servingId"] is None
    assert sent["type"] == "Serving"
    assert entry.id == 999
    assert entry.grams == pytest.approx(162.0)
    assert entry.group is DiaryGroup.BREAKFAST


def test_add_food_uncategorized_picks_group_by_hour(api, session):
    api.post("/api/v2/get_food").mock(return_value=json_response(FOOD_OATS))
    route = api.post("/api/v2/add_serving").mock(return_value=json_response({"servingId": 1}))
    with CronometerClient(session) as client:
        entry = client.add_food(100, 11, 50, time=dt.time(12, 0))
    assert sent_json(route)["serving"]["order"] >> 16 == DiaryGroup.LUNCH
    assert entry.group is DiaryGroup.LUNCH


def test_add_food_unknown_measure(api, session):
    api.post("/api/v2/get_food").mock(return_value=json_response(FOOD_OATS))
    add = api.post("/api/v2/add_serving")
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="no measure 77"):
        client.add_food(100, 77, 1)
    assert add.call_count == 0


def test_remove_food_sends_full_serving_to_v3(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    route = api.delete(f"/api/v3/user/{USER_ID}/diary-entries").mock(
        return_value=json_response(None, 204)
    )
    with CronometerClient(session) as client:
        entry = client.remove_food(5207940831, dt.date(2026, 9, 24))
    assert entry.food_id == 200
    request = route.calls[0].request
    assert request.headers["x-crono-session"] == TOKEN
    assert sent_json(route) == {"diaryEntries": [DIARY["diary"][1]]}


def test_remove_food_unknown_entry(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    with (
        CronometerClient(session) as client,
        pytest.raises(CronometerError, match="No diary entry 1"),
    ):
        client.remove_food(1, dt.date(2026, 9, 24))


@pytest.mark.parametrize("status", [401, 403])
def test_remove_food_v3_expired(api, session, status):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    api.delete(f"/api/v3/user/{USER_ID}/diary-entries").mock(return_value=json_response({}, status))
    with CronometerClient(session) as client, pytest.raises(NotAuthenticatedError):
        client.remove_food(5207940830, dt.date(2026, 9, 24))


def test_remove_food_v3_error(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY))
    api.delete(f"/api/v3/user/{USER_ID}/diary-entries").mock(return_value=json_response({}, 500))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="500"):
        client.remove_food(5207940830, dt.date(2026, 9, 24))
