import datetime as dt

import pytest

from cronopy import CronometerClient, DiaryGroup
from cronopy.models import METRIC_BODY_FAT, METRIC_WEIGHT, UNIT_KG, UNIT_PERCENT
from live.conftest import SENTINEL_DAY

pytestmark = pytest.mark.live


def test_login_gives_session(live_client: CronometerClient):
    assert live_client.session is not None
    assert live_client.session.user_id > 0
    assert live_client.session.token


def test_search_and_food_details(live_client: CronometerClient):
    results = live_client.search("chili", max_results=5)
    assert results, "search returned nothing"
    first = results[0]
    assert {"id", "name", "measureId"} <= set(first)
    food = live_client.get_food(first["id"])
    assert food.id == first["id"]
    assert food.measures
    assert food.kcal_per_100g is not None
    batch = live_client.get_foods([r["id"] for r in results])
    assert set(batch) == {r["id"] for r in results}


def test_diary_and_calories_today(live_client: CronometerClient):
    day = live_client.get_day()
    assert day.day == live_client.today()
    assert day.calories is not None
    assert day.calories.target > 0
    summary = live_client.get_calories()
    assert summary.consumed == day.calories.consumed


def test_metrics_catalog_has_weight_and_body_fat(live_client: CronometerClient):
    metrics = {m.id: m for m in live_client.get_metrics()}
    assert metrics[METRIC_WEIGHT].name == "Weight"
    assert metrics[METRIC_WEIGHT].unit(UNIT_KG) is not None
    assert metrics[METRIC_BODY_FAT].unit(UNIT_PERCENT) is not None


def test_biometrics_history_and_goal(live_client: CronometerClient):
    end = live_client.today()
    points = live_client.get_biometrics(METRIC_WEIGHT, UNIT_KG, end - dt.timedelta(days=365), end)
    assert isinstance(points, list)
    goal = live_client.get_weight_goal()
    assert isinstance(goal.rate_lb_per_week, float)


def test_find_activity(live_client: CronometerClient):
    found = live_client.find_activity("walking")
    assert found
    assert all(a.id > 0 for a in found)


def test_write_roundtrip_on_sentinel_day(live_client: CronometerClient):
    """Add a weight, an exercise and a food serving on 1900-01-02, then delete them all."""
    before = live_client.get_day(SENTINEL_DAY)
    assert before.is_empty, f"sentinel day not empty, clean it first: {before.to_dict()}"

    created: list[int] = []
    try:
        w = live_client.add_weight(1.0, day=SENTINEL_DAY, time=dt.time(1, 0))
        created.append(w.id)
        e = live_client.add_exercise(
            "cronopy live test", 1, 1, day=SENTINEL_DAY, time=dt.time(1, 1)
        )
        created.append(e.id)
        food = live_client.get_food(live_client.search("banana", max_results=1)[0]["id"])
        assert food.default_measure_id is not None
        f = live_client.add_food(
            food.id,
            food.default_measure_id,
            1,
            group=DiaryGroup.SNACKS,
            day=SENTINEL_DAY,
            time=dt.time(1, 2),
        )
        created.append(f.id)

        day = live_client.get_day(SENTINEL_DAY)
        assert [b.id for b in day.biometrics] == [w.id]
        assert [x.id for x in day.exercises] == [e.id]
        assert [s.id for s in day.servings] == [f.id]
        assert day.biometrics[0].amount == 1.0
    finally:
        for entry_id in created:
            live_client.remove_entry(entry_id, SENTINEL_DAY)

    assert live_client.get_day(SENTINEL_DAY).is_empty
