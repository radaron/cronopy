import json
from typing import Any

import pytest
import respx
from httpx import Response

from cronopy.models import Session

BASE = "https://mobile.cronometer.com"
USER_ID = 17689734
TOKEN = "sessionkey-abc123"


@pytest.fixture
def api():
    """respx router bound to mobile.cronometer.com; unmatched requests raise."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def session():
    return Session(user_id=USER_ID, token=TOKEN, email="me@example.com", timezone="Europe/Budapest")


def sent_json(route, index: int = -1) -> dict:
    """Decode the JSON body of the ``index``-th request that hit ``route``."""
    return json.loads(route.calls[index].request.content)


def json_response(body, status: int = 200) -> Response:
    return Response(status, json=body)


LOGIN_OK = {
    "result": "SUCCESS",
    "id": USER_ID,
    "sessionKey": TOKEN,
    "timezone": "Europe/Budapest",
    "nutrients": [],
}

FOOD_OATS = {
    "id": 100,
    "name": "Oats",
    "source": "USDA",
    "defaultMeasureId": 10,
    "measures": [
        {"id": 10, "name": "cup", "value": 81.0, "type": "Weight"},
        {"id": 11, "name": "g", "value": 1, "type": "Weight"},
    ],
    "nutrients": [{"id": 208, "amount": 389.0}, {"id": 203, "amount": 16.9}],
}

FOOD_MILK = {
    "id": 200,
    "name": "Milk",
    "source": "Custom",
    "defaultMeasureId": 20,
    "measures": [{"id": 20, "name": "glass", "value": 244.0, "type": "Volume"}],
    "nutrients": [{"id": 208, "amount": 42.0}],
}

FOOD_RECIPE = {
    "id": 300,
    "name": "Chili",
    "source": "Custom",
    "defaultMeasureId": 30,
    "measures": [{"id": 30, "name": "serving", "value": 1, "type": "Recipe"}],
    "nutrients": [{"id": 208, "amount": 708.5}],
}


def serving(serving_id: int, food_id: int, measure_id: int, grams: float, order: int, time: str):
    return {
        "type": "Serving",
        "servingId": serving_id,
        "day": "2026-9-24",
        "time": time,
        "order": order,
        "userId": USER_ID,
        "foodId": food_id,
        "measureId": measure_id,
        "grams": grams,
        "translationId": 0,
        "offset": None,
        "source": None,
    }


DIARY: dict[str, Any] = {
    "diary": [
        serving(5207940830, 100, 10, 81.0, (1 << 16) | 1, "8:30:0"),
        serving(5207940831, 200, 20, 244.0, (1 << 16) | 2, "8:35:12"),
        {"type": "Exercise", "name": "Running", "order": 1},
        serving(5207940832, 300, 30, 1.0, (3 << 16) | 1, "19:0:0"),
    ],
    "summary": {"macros": {"energy": 2200.0}, "consumed": {"total": 1800.5}},
}


def biometric(
    biometric_id: int, metric_id: int, unit_id: int, amount: float, time: str, order: int
):
    return {
        "type": "Biometric",
        "biometricId": biometric_id,
        "metricId": metric_id,
        "unitId": unit_id,
        "amount": amount,
        "day": "2026-09-24",
        "time": time,
        "order": order,
        "userId": USER_ID,
        "offset": 120,
        "samplesVersion": 0,
        "source": "Samsung Health",
        "externalId": "ext-1",
        "meta": {},
    }


EXERCISE = {
    "type": "Exercise",
    "exerciseId": 864137251,
    "name": "Walking",
    "minutes": 16,
    "calories": -157.5,
    "calorieOverride": False,
    "activityId": 0,
    "activitySpecId": 0,
    "weight": 0,
    "day": "2026-09-24",
    "time": "21:17:00",
    "order": 5,
    "userId": USER_ID,
    "source": "Samsung Health",
    "meta": {},
}

DIARY_FULL: dict[str, Any] = {
    "diary": [
        *DIARY["diary"],
        biometric(1848083090, 3, 5, 56, "21:10:00", 6),
        biometric(1848083088, 1, 1, 106.7, "07:00:00", 7),
        EXERCISE,
    ],
    "summary": {
        **DIARY["summary"],
        "burned": {
            "bmr_kcal": 2047,
            "activity_kcal": 409,
            "exercise_kcal": 157,
            "tef_kcal": 0,
            "total": 2606.6,
        },
    },
}

METRICS = {
    "metrics": [
        {
            "id": 1,
            "name": "Weight",
            "legacy": False,
            "units": [
                {"id": 1, "name": "kg", "conversion": 1},
                {"id": 2, "name": "lbs", "conversion": 0.453592},
            ],
        },
        {"id": 8, "name": "Body Fat", "units": [{"id": 13, "name": "%", "conversion": 1}]},
    ]
}

PROFILE = {
    "result": "SUCCESS",
    "id": USER_ID,
    "weight": 106.7,
    "weightDate": "2026-09-21",
    "timezone": "Europe/Budapest",
    "prefs": [
        {"weightGoal": "-1.6534649999999997"},
        {"wg": "90.0"},
        {"wgkg": "90"},
        {"weightUnit": "Kilograms"},
    ],
}
