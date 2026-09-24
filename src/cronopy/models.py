from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from cronopy.enums import DiaryGroup
from cronopy.util import parse_day, parse_time

# Cronometer nutrient id for energy in kcal (same as USDA nutrient number 208).
# Food ``nutrients`` lists carry amounts per 100 g under this id.
NUTRIENT_ENERGY = 208

# Biometric metric and unit ids from ``get_metrics`` (global catalog, stable).
METRIC_WEIGHT = 1
METRIC_BODY_FAT = 8
UNIT_KG = 1
UNIT_LBS = 2
UNIT_PERCENT = 13

LB_PER_KG = 2.2046226218


@dataclass(frozen=True)
class Session:
    """Authenticated mobile-API session: user id plus the ``sessionKey`` token."""

    user_id: int
    token: str
    email: str | None = None
    timezone: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(
            user_id=int(data["user_id"]),
            token=str(data["token"]),
            email=data.get("email"),
            timezone=data.get("timezone"),
        )


@dataclass(frozen=True)
class CalorieSummary:
    """Energy balance for one day, in kcal, as reported by the diary ``summary``.

    ``target`` is Cronometer's daily energy target (already adjusted for the
    weight goal and, when enabled, exercise). ``remaining`` is
    ``target - consumed`` (negative when over). ``burned`` is the total of
    BMR, baseline activity and exercise.
    """

    day: dt.date
    consumed: float
    target: float
    bmr: float = 0.0
    activity: float = 0.0
    exercise: float = 0.0
    burned: float = 0.0

    @property
    def remaining(self) -> float:
        return self.target - self.consumed

    @classmethod
    def from_summary(cls, day: dt.date, summary: dict[str, Any]) -> CalorieSummary:
        target = (summary.get("macros") or {}).get("energy")
        consumed = (summary.get("consumed") or {}).get("total")
        if target is None or consumed is None:
            raise ValueError(f"Diary summary lacks energy data: {summary!r}")
        burned = summary.get("burned") or {}
        return cls(
            day=day,
            consumed=float(consumed),
            target=float(target),
            bmr=float(burned.get("bmr_kcal") or 0.0),
            activity=float(burned.get("activity_kcal") or 0.0),
            exercise=float(burned.get("exercise_kcal") or 0.0),
            burned=float(burned.get("total") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "consumed": self.consumed,
            "target": self.target,
            "remaining": self.remaining,
            "bmr": self.bmr,
            "activity": self.activity,
            "exercise": self.exercise,
            "burned": self.burned,
        }


@dataclass(frozen=True)
class DiaryEntry:
    """One food serving logged in the diary.

    ``grams`` is the logged weight for weight-based measures; for recipe
    measures Cronometer stores a serving count in the same field. ``raw`` is
    the untouched API object, needed to delete the entry.
    """

    id: int
    day: dt.date
    time: dt.time
    group: DiaryGroup
    order: int
    food_id: int
    measure_id: int
    grams: float
    user_id: int
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> DiaryEntry:
        packed = int(data.get("order") or 0)
        return cls(
            id=int(data["servingId"]),
            day=parse_day(data["day"]),
            time=parse_time(data.get("time") or "0:0:0"),
            group=DiaryGroup((packed >> 16) & 0xFFFF),
            order=packed & 0xFFFF,
            food_id=int(data["foodId"]),
            measure_id=int(data.get("measureId") or 0),
            grams=float(data.get("grams") or 0.0),
            user_id=int(data.get("userId") or 0),
            raw=data,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "time": self.time.isoformat(),
            "group": self.group.name.lower(),
            "order": self.order,
            "food_id": self.food_id,
            "measure_id": self.measure_id,
            "grams": self.grams,
            "user_id": self.user_id,
        }


@dataclass(frozen=True)
class Measure:
    """A serving size of a food, e.g. ``cup`` weighing ``244`` grams.

    ``type`` is ``Weight``, ``Volume`` or ``Recipe`` (nutrients of recipe foods
    are stored per serving instead of per 100 g).
    """

    id: int
    name: str
    grams: float
    type: str = "Weight"

    @property
    def label(self) -> str:
        return f"{self.name} - {self.grams:g}g"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Measure:
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            grams=float(data.get("value") or 0.0),
            type=str(data.get("type") or "Weight"),
        )


@dataclass(frozen=True)
class FoodInfo:
    """Food details: nutrients are stored per 100 g by Cronometer."""

    id: int
    name: str
    kcal_per_100g: float | None
    measures: tuple[Measure, ...]
    default_measure_id: int | None = None
    source: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> FoodInfo:
        energy = next(
            (
                float(n["amount"])
                for n in data.get("nutrients") or []
                if isinstance(n, dict) and n.get("id") == NUTRIENT_ENERGY
            ),
            None,
        )
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            kcal_per_100g=energy,
            measures=tuple(
                Measure.from_api(m) for m in data.get("measures") or [] if isinstance(m, dict)
            ),
            default_measure_id=data.get("defaultMeasureId"),
            source=data.get("source"),
        )

    def measure(self, measure_id: int) -> Measure | None:
        return next((m for m in self.measures if m.id == measure_id), None)

    def grams(self, measure_id: int, amount: float = 1.0) -> float | None:
        """Weight of ``amount`` units of ``measure_id``, or ``None`` if unknown."""
        m = self.measure(measure_id)
        return None if m is None else m.grams * amount

    def kcal(self, measure_id: int, amount: float = 1.0) -> float | None:
        """Energy of ``amount`` units of ``measure_id``, or ``None`` if unknown."""
        m = self.measure(measure_id)
        if m is None or self.kcal_per_100g is None:
            return None
        if m.type == "Recipe":
            return self.kcal_per_100g * m.grams * amount
        return self.kcal_per_100g / 100 * m.grams * amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "kcal_per_100g": self.kcal_per_100g,
            "default_measure_id": self.default_measure_id,
            "measures": [
                {"id": m.id, "name": m.name, "grams": m.grams, "type": m.type}
                for m in self.measures
            ],
        }


@dataclass(frozen=True)
class MetricUnit:
    id: int
    name: str
    conversion: float


@dataclass(frozen=True)
class Metric:
    """A trackable biometric (Weight, Body Fat, Heart Rate, ...) and its units."""

    id: int
    name: str
    units: tuple[MetricUnit, ...]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Metric:
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            units=tuple(
                MetricUnit(int(u["id"]), str(u.get("name") or ""), float(u.get("conversion") or 0))
                for u in data.get("units") or []
                if isinstance(u, dict) and "id" in u
            ),
        )

    def unit(self, unit_id: int) -> MetricUnit | None:
        return next((u for u in self.units if u.id == unit_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "units": [{"id": u.id, "name": u.name, "conversion": u.conversion} for u in self.units],
        }


@dataclass(frozen=True)
class BiometricEntry:
    """One biometric reading logged in the diary."""

    id: int
    day: dt.date
    time: dt.time
    metric_id: int
    unit_id: int
    amount: float
    source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> BiometricEntry:
        return cls(
            id=int(data["biometricId"]),
            day=parse_day(data["day"]),
            time=parse_time(data.get("time") or "0:0:0"),
            metric_id=int(data["metricId"]),
            unit_id=int(data.get("unitId") or 0),
            amount=float(data.get("amount") or 0.0),
            source=data.get("source"),
            raw=data,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "time": self.time.isoformat(),
            "metric_id": self.metric_id,
            "unit_id": self.unit_id,
            "amount": self.amount,
            "source": self.source,
        }


@dataclass(frozen=True)
class BiometricPoint:
    """One value of a biometric time series from ``get_biometrics``."""

    day: dt.date
    value: float
    time: dt.time | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "time": self.time.isoformat() if self.time else None,
            "value": self.value,
        }


@dataclass(frozen=True)
class ExerciseEntry:
    """One exercise logged in the diary. ``calories`` is negative (energy burned)."""

    id: int
    day: dt.date
    time: dt.time
    name: str
    minutes: float
    calories: float
    activity_id: int = 0
    source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def kcal_burned(self) -> float:
        return -self.calories

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ExerciseEntry:
        return cls(
            id=int(data["exerciseId"]),
            day=parse_day(data["day"]),
            time=parse_time(data.get("time") or "0:0:0"),
            name=str(data.get("name") or ""),
            minutes=float(data.get("minutes") or 0.0),
            calories=float(data.get("calories") or 0.0),
            activity_id=int(data.get("activityId") or 0),
            source=data.get("source"),
            raw=data,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "time": self.time.isoformat(),
            "name": self.name,
            "minutes": self.minutes,
            "kcal_burned": self.kcal_burned,
            "activity_id": self.activity_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class DayDiary:
    """Everything logged on one day plus its calorie summary."""

    day: dt.date
    servings: list[DiaryEntry]
    exercises: list[ExerciseEntry]
    biometrics: list[BiometricEntry]
    calories: CalorieSummary | None

    @property
    def is_empty(self) -> bool:
        return not (self.servings or self.exercises or self.biometrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "servings": [e.to_dict() for e in self.servings],
            "exercises": [e.to_dict() for e in self.exercises],
            "biometrics": [e.to_dict() for e in self.biometrics],
            "calories": self.calories.to_dict() if self.calories else None,
        }


@dataclass(frozen=True)
class Activity:
    """An exercise activity from ``find_activity``; ``cals`` is Cronometer's burn factor."""

    id: int
    name: str
    category: str
    cals: float

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Activity:
        return cls(
            id=int(data["id"]),
            name=str(data.get("name") or ""),
            category=str(data.get("category") or ""),
            cals=float(data.get("cals") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "category": self.category, "cals": self.cals}


@dataclass(frozen=True)
class WeightGoal:
    """Weight goal from the profile preferences.

    Cronometer stores the rate as lb/week (negative = lose) in ``weightGoal``
    and the target weight in the account's unit in ``wg`` (kg in ``wgkg``).
    """

    rate_lb_per_week: float
    target_kg: float | None
    current_kg: float | None
    weight_date: dt.date | None

    @property
    def rate_kg_per_week(self) -> float:
        return self.rate_lb_per_week / LB_PER_KG

    @property
    def to_go_kg(self) -> float | None:
        if self.target_kg is None or self.current_kg is None:
            return None
        return self.current_kg - self.target_kg

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate_lb_per_week": self.rate_lb_per_week,
            "rate_kg_per_week": self.rate_kg_per_week,
            "target_kg": self.target_kg,
            "current_kg": self.current_kg,
            "weight_date": self.weight_date.isoformat() if self.weight_date else None,
            "to_go_kg": self.to_go_kg,
        }
