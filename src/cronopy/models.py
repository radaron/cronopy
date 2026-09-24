"""Data types returned by :class:`cronopy.client.CronometerClient`."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any


class Source(StrEnum):
    """Food source filter accepted by the search endpoint."""

    ALL = "All"


@dataclass(frozen=True)
class CalorieSummary:
    """Energy balance for one day, in kcal.

    ``burned`` is BMR + baseline activity + exercise. ``target`` is the
    custom energy target if one is set, otherwise ``burned`` plus the
    (negative for weight loss) ``weight_goal_adjustment``. ``remaining`` is
    ``target - consumed`` (negative when over). ``tef`` (thermic effect of
    food) is reported separately and not included, matching the web UI.
    """

    day: dt.date
    consumed: float
    bmr: float
    activity: float
    exercise: float
    tef: float
    weight_goal_adjustment: float = 0.0
    custom_target: float | None = None

    @property
    def burned(self) -> float:
        return self.bmr + self.activity + self.exercise

    @property
    def target(self) -> float:
        if self.custom_target is not None:
            return self.custom_target + self.exercise
        return self.burned + self.weight_goal_adjustment

    @property
    def remaining(self) -> float:
        return self.target - self.consumed

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "consumed": self.consumed,
            "bmr": self.bmr,
            "activity": self.activity,
            "exercise": self.exercise,
            "tef": self.tef,
            "burned": self.burned,
            "weight_goal_adjustment": self.weight_goal_adjustment,
            "custom_target": self.custom_target,
            "target": self.target,
            "remaining": self.remaining,
        }


class DiaryGroup(IntEnum):
    """Diary category. Stored in the high 16 bits of a serving's ``order`` field."""

    UNCATEGORIZED = 0
    BREAKFAST = 1
    LUNCH = 2
    DINNER = 3
    SNACKS = 4

    @classmethod
    def parse(cls, name: str) -> DiaryGroup:
        return cls[name.strip().upper()]


@dataclass(frozen=True)
class DiaryEntry:
    """One food serving logged in the diary."""

    id: int
    day: dt.date
    time: dt.time
    group: DiaryGroup
    order: int
    food_id: int
    measure_id: int
    amount: float
    user_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "time": self.time.isoformat(),
            "group": self.group.name.lower(),
            "order": self.order,
            "food_id": self.food_id,
            "measure_id": self.measure_id,
            "amount": self.amount,
            "user_id": self.user_id,
        }


@dataclass(frozen=True)
class Measure:
    """A serving size of a food, e.g. ``1 cup`` weighing ``244`` grams."""

    id: int
    name: str
    quantity: float
    grams: float

    @property
    def label(self) -> str:
        return f"{self.quantity:g} {self.name} - {self.grams:g}g"


@dataclass(frozen=True)
class FoodInfo:
    """Food details: nutrients are stored per 100 g by Cronometer."""

    id: int
    name: str
    kcal_per_100g: float | None
    measures: tuple[Measure, ...]

    def measure(self, measure_id: int) -> Measure | None:
        return next((m for m in self.measures if m.id == measure_id), None)

    def kcal(self, measure_id: int, amount: float = 1.0) -> float | None:
        """Energy of ``amount`` units of ``measure_id``, or ``None`` if unknown."""
        m = self.measure(measure_id)
        if m is None or self.kcal_per_100g is None:
            return None
        return self.kcal_per_100g / 100 * m.grams * amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kcal_per_100g": self.kcal_per_100g,
            "measures": [
                {"id": m.id, "name": m.name, "quantity": m.quantity, "grams": m.grams}
                for m in self.measures
            ],
        }
