"""Biometrics, exercise, profile and day-diary features."""

import datetime as dt

import pytest

from conftest import DIARY_FULL, METRICS, PROFILE, TOKEN, USER_ID, json_response, sent_json
from cronopy.client import CronometerClient, CronometerError
from cronopy.enums import DiaryGroup
from cronopy.models import METRIC_BODY_FAT, METRIC_WEIGHT, UNIT_KG, UNIT_PERCENT, CalorieSummary

DAY = dt.date(2026, 9, 24)


def test_get_day_splits_entry_types_and_calories(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY_FULL))
    with CronometerClient(session) as client:
        day = client.get_day(DAY)
    assert [e.id for e in day.servings] == [5207940830, 5207940831, 5207940832]
    assert [b.id for b in day.biometrics] == [1848083088, 1848083090]  # sorted by time
    assert day.biometrics[0].metric_id == METRIC_WEIGHT
    assert day.biometrics[0].amount == 106.7
    assert day.biometrics[0].source == "Samsung Health"
    assert [x.name for x in day.exercises] == ["Walking"]
    assert day.exercises[0].kcal_burned == 157.5
    assert day.exercises[0].minutes == 16
    assert day.calories is not None
    assert day.calories.burned == 2606.6
    assert day.calories.bmr == 2047
    assert not day.is_empty
    d = day.to_dict()
    assert d["exercises"][0]["kcal_burned"] == 157.5
    assert d["biometrics"][1]["metric_id"] == 3


def test_get_day_without_summary(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response({"diary": []}))
    with CronometerClient(session) as client:
        day = client.get_day(DAY)
    assert day.is_empty
    assert day.calories is None


def test_calories_burned_breakdown(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY_FULL))
    with CronometerClient(session) as client:
        cal = client.get_calories(DAY)
    assert (cal.bmr, cal.activity, cal.exercise, cal.burned) == (2047, 409, 157, 2606.6)
    assert cal.to_dict()["burned"] == 2606.6


def test_calories_from_summary_defaults_burned_to_zero():
    cal = CalorieSummary.from_summary(DAY, {"macros": {"energy": 2000}, "consumed": {"total": 1}})
    assert cal.burned == 0.0


def test_get_metrics(api, session):
    api.post("/api/v2/get_metrics").mock(return_value=json_response(METRICS))
    with CronometerClient(session) as client:
        metrics = client.get_metrics()
    weight = next(m for m in metrics if m.id == METRIC_WEIGHT)
    assert weight.name == "Weight"
    lbs = weight.unit(2)
    assert lbs is not None
    assert lbs.name == "lbs"
    assert weight.unit(99) is None
    assert metrics[1].to_dict()["units"] == [{"id": 13, "name": "%", "conversion": 1.0}]


def test_get_biometrics_payload_and_points(api, session):
    route = api.post("/api/v2/get_biometrics").mock(
        return_value=json_response(
            {
                "data": [
                    {"day": "2026-09-07", "value": 108.2, "time": "21:30:00"},
                    {"day": "2026-09-14", "value": 107.3},
                ]
            }
        )
    )
    with CronometerClient(session) as client:
        points = client.get_biometrics(1, 1, dt.date(2026, 9, 1), dt.date(2026, 9, 24))
    body = sent_json(route)
    assert body["metricId"] == 1
    assert body["unitId"] == 1
    assert body["start"] == "2026-9-1"
    assert body["end"] == "2026-9-24"
    assert points[0].value == 108.2
    assert points[0].time == dt.time(21, 30)
    assert points[1].time is None
    assert points[1].to_dict() == {"day": "2026-09-14", "time": None, "value": 107.3}


def test_get_biometrics_default_range_is_30_days(api, session):
    route = api.post("/api/v2/get_biometrics").mock(return_value=json_response({"data": []}))
    with CronometerClient(session) as client:
        end = client.today()
        assert client.get_biometrics(1, 1) == []
    body = sent_json(route)
    assert body["end"] == f"{end.year}-{end.month}-{end.day}"
    start = end - dt.timedelta(days=30)
    assert body["start"] == f"{start.year}-{start.month}-{start.day}"


def test_add_biometric_payload_and_entry(api, session):
    route = api.post("/api/v2/add_biometric").mock(
        return_value=json_response(
            {"heightInCM": 180, "weightInKG": 106.7, "messages": [], "id": 1849188988}
        )
    )
    with CronometerClient(session) as client:
        entry = client.add_biometric(1, 1, 106.5, day=DAY, time=dt.time(7, 5, 0))
    sent = sent_json(route)
    assert sent["config"] == {"call_version": 1}
    bio = sent["biometric"]
    assert bio["type"] == "Biometric"
    assert bio["metricId"] == 1
    assert bio["unitId"] == 1
    assert bio["amount"] == 106.5
    assert bio["day"] == "2026-9-24"
    assert bio["time"] == "7:5:0"
    assert bio["userId"] == USER_ID
    assert bio["biometricId"] is None
    assert entry.id == 1849188988
    assert entry.day == DAY
    assert entry.time == dt.time(7, 5)
    assert entry.amount == 106.5


def test_add_weight_and_body_fat_shortcuts(api, session):
    route = api.post("/api/v2/add_biometric").mock(return_value=json_response({"id": 5}))
    with CronometerClient(session) as client:
        client.add_weight(100.0, day=DAY)
        client.add_body_fat(30.5, day=DAY)
    first, second = sent_json(route, 0)["biometric"], sent_json(route, 1)["biometric"]
    assert (first["metricId"], first["unitId"], first["amount"]) == (METRIC_WEIGHT, UNIT_KG, 100.0)
    assert (second["metricId"], second["unitId"], second["amount"]) == (
        METRIC_BODY_FAT,
        UNIT_PERCENT,
        30.5,
    )


def test_add_biometric_without_id_raises(api, session):
    api.post("/api/v2/add_biometric").mock(return_value=json_response({"messages": []}))
    with CronometerClient(session) as client, pytest.raises(CronometerError, match="no entry id"):
        client.add_biometric(1, 1, 1.0)


def test_find_activity(api, session):
    route = api.post("/api/v2/find_activity").mock(
        return_value=json_response(
            {
                "activities": [
                    {
                        "id": 989,
                        "name": "walking, brisk",
                        "cals": 1.95,
                        "category": "walking",
                        "legacy": True,
                    }
                ]
            }
        )
    )
    with CronometerClient(session) as client:
        found = client.find_activity("walk")
    assert sent_json(route)["query"] == "walk"
    assert found[0].id == 989
    assert found[0].category == "walking"
    assert found[0].to_dict()["cals"] == 1.95


def test_add_exercise_payload_and_entry(api, session):
    route = api.post("/api/v2/add_exercise").mock(
        return_value=json_response({"messages": [], "id": 864178870})
    )
    with CronometerClient(session) as client:
        entry = client.add_exercise("Run", 30, 300, activity_id=12, day=DAY, time=dt.time(18, 0))
    ex = sent_json(route)["exercise"]
    assert ex["type"] == "Exercise"
    assert ex["name"] == "Run"
    assert ex["minutes"] == 30
    assert ex["calories"] == -300  # burned energy is negative on the wire
    assert ex["calorieOverride"] is True
    assert ex["activityId"] == 12
    assert ex["exerciseId"] is None
    assert ex["day"] == "2026-9-24"
    assert ex["time"] == "18:0:0"
    assert entry.id == 864178870
    assert entry.kcal_burned == 300
    assert entry.activity_id == 12


def test_add_exercise_accepts_negative_kcal(api, session):
    route = api.post("/api/v2/add_exercise").mock(return_value=json_response({"id": 1}))
    with CronometerClient(session) as client:
        client.add_exercise("x", 1, -50)
    assert sent_json(route)["exercise"]["calories"] == -50


@pytest.mark.parametrize(
    ("entry_id", "kind"),
    [(5207940830, "Serving"), (1848083090, "Biometric"), (864137251, "Exercise")],
)
def test_remove_entry_any_type_strips_meta(api, session, entry_id, kind):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY_FULL))
    route = api.delete(f"/api/v3/user/{USER_ID}/diary-entries").mock(
        return_value=json_response(None, 204)
    )
    with CronometerClient(session) as client:
        removed = client.remove_entry(entry_id, DAY)
    assert removed.id == entry_id
    sent = sent_json(route)["diaryEntries"]
    assert len(sent) == 1
    assert sent[0]["type"] == kind
    assert "meta" not in sent[0]
    expected = {
        k: v
        for k, v in next(
            e for e in DIARY_FULL["diary"] if e.get("type") == kind and entry_id in e.values()
        ).items()
        if k != "meta"
    }
    if kind == "Biometric":
        expected["id"] = entry_id  # v3 identifies biometrics by ``id``
    assert sent[0] == expected
    assert route.calls[0].request.headers["x-crono-session"] == TOKEN


def test_remove_entry_unknown(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY_FULL))
    with (
        CronometerClient(session) as client,
        pytest.raises(CronometerError, match="No diary entry 42"),
    ):
        client.remove_entry(42, DAY)


def test_remove_food_still_serving_only(api, session):
    api.post("/api/v2/get_diary").mock(return_value=json_response(DIARY_FULL))
    api.delete(f"/api/v3/user/{USER_ID}/diary-entries").mock(return_value=json_response(None, 204))
    with CronometerClient(session) as client:
        with pytest.raises(CronometerError, match="No diary entry"):
            client.remove_food(864137251, DAY)
        assert client.remove_food(5207940830, DAY).group is DiaryGroup.BREAKFAST


def test_get_profile_and_preferences(api, session):
    api.post("/api/v2/get_profile").mock(return_value=json_response(PROFILE))
    with CronometerClient(session) as client:
        assert client.get_profile()["weight"] == 106.7
        prefs = client.get_preferences()
    assert prefs["weightUnit"] == "Kilograms"
    assert prefs["wgkg"] == "90"


def test_get_weight_goal(api, session):
    api.post("/api/v2/get_profile").mock(return_value=json_response(PROFILE))
    with CronometerClient(session) as client:
        goal = client.get_weight_goal()
    assert goal.rate_lb_per_week == pytest.approx(-1.653465)
    assert goal.rate_kg_per_week == pytest.approx(-0.75, abs=1e-3)
    assert goal.target_kg == 90.0
    assert goal.current_kg == 106.7
    assert goal.weight_date == dt.date(2026, 9, 21)
    assert goal.to_go_kg == pytest.approx(16.7)
    assert goal.to_dict()["weight_date"] == "2026-09-21"


def test_get_weight_goal_when_unset(api, session):
    api.post("/api/v2/get_profile").mock(
        return_value=json_response({"result": "SUCCESS", "prefs": []})
    )
    with CronometerClient(session) as client:
        goal = client.get_weight_goal()
    assert goal.rate_lb_per_week == 0.0
    assert goal.target_kg is None
    assert goal.current_kg is None
    assert goal.to_go_kg is None
